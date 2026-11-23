"""`frappe-ast form <DocType> [event]`: what runs on a desk form, in the order desk runs it.

For one event, desk calls: frappe.ui.form.on(<DocType>) handlers, then frappe.ui.form.on("*"),
then frm.cscript[event] (the JS controller class, most derived definition), then
frm.cscript["custom_" + event]. A child-table field event runs the child DocType's handlers
and the parent form's cscript method of the same name.
"""

from collections import Counter
from pathlib import PurePath

from frappe_ast.queries.contexts import FormContexts
from frappe_ast.queries.forms import FormScripts, is_event_name
from frappe_ast.queries.resolver import Resolver
from frappe_ast.store import Store


class FormReport:
	def __init__(self, store: Store, limit: int = 20):
		self.store = store
		self.shared: list[dict] = []
		self.resolver = Resolver(store)
		self.forms = FormScripts(store, self.resolver)
		self.limit = limit

	def run(self, name: str, event: str = "") -> dict:
		record = self.resolver.doctype(name)
		if record is None:
			return {"query": name, "found": False}
		doctype = record["name"]
		# Handlers that shared code (public/js setup functions, JS classes) registers on this form.
		self.shared = FormContexts(self.store, self.resolver, self.forms).registrations(doctype)
		scripts = self.forms.scripts(doctype)
		controllers = self.forms.controllers(doctype)
		chain = self.forms.chain(controllers[0]["target"]) if controllers else []
		methods = self.forms.methods(chain)
		tables = self.forms.child_tables(doctype)
		report = {
			"query": doctype,
			"found": True,
			"scripts": [f"{path}  ({label})" for path, label in self.forms.labelled_scripts(doctype)],
			"controller": [_class_line(c) for c in chain],
			"installed_by": [f"{c['path']}:{c['line']}" for c in controllers],
		}
		if event:
			report["runs"] = self._runs(doctype, event, scripts, methods, tables)
			return report
		report["events"] = self._events(doctype, scripts, methods, tables)
		report["fields_used"] = self._fields_used(scripts)
		return report

	def _runs(self, doctype, event, scripts, methods, tables) -> list[str]:
		"""Every handler for `event`, in run order, on the form and on each child table it applies to."""
		out = []
		for target, table in self._targets(doctype, event, scripts, tables):
			where = scripts if not table else scripts + self._own(target)
			when = f"[{target}.{event}, table {table}] " if table else ""
			out += [f'{when}on("{target}")  {h}' for h in self._handlers(target, event, where)]
			out += [f'{when}on("*")  {h}' for h in self._handlers("*", event, None)]
			for name in (event, f"custom_{event}"):
				if name in methods:
					m = methods[name]
					out.append(f"{when}cscript.{name}  {m['cls']}  {m['path']}:{m['line']}")
				out += [f"{when}cscript.{name} (assigned)  {h}" for h in self._assigned(name, scripts)]
		return out

	def _targets(self, doctype, event, scripts, tables) -> list[tuple[str, str]]:
		"""(DocType, table field) pairs where `event` fires: a form event, or a field of that DocType."""
		targets = []
		if is_event_name(event, self.forms.fieldnames(doctype)) or self._handlers(doctype, event, scripts):
			targets.append((doctype, ""))
		for table, child in tables.items():
			where = scripts + self._own(child)
			if event in self.forms.fieldnames(child) or self._handlers(child, event, where):
				targets.append((child, table))
		return targets

	def _events(self, doctype, scripts, methods, tables) -> dict[str, list[str]]:
		"""Per DocType: events with frappe.ui.form.on handlers, then the ones only cscript handles."""
		out = {}
		for target, table in [(doctype, ""), *((c, t) for t, c in tables.items())]:
			fields = self.forms.fieldnames(target)
			where = scripts if not table else scripts + self._own(target)
			counts = Counter(r["event"] for r in self._event_rows(target, where))
			if table:
				cscript = {m for m in methods if m in fields}
			else:
				cscript = {m for m in methods if is_event_name(m, fields)}
				cscript |= {r["target"] for r in self._assigned_rows(scripts)}
			lines = [f"{e}: on x{counts[e]}" + (", cscript" if e in cscript else "") for e in sorted(counts)]
			only = sorted(cscript - set(counts))
			if only:
				lines.append("cscript only: " + " ".join(only))
			if lines:
				out[target if not table else f"{target} ({table})"] = lines
		return out

	def _handlers(self, doctype: str, event: str, where: list[str] | None) -> list[str]:
		return [f"{r['path']}:{r['line']}" for r in self._event_rows(doctype, where, event)]

	def _event_rows(self, doctype: str, where: list[str] | None, event: str = "") -> list[dict]:
		params = [f"{doctype}:{event}" if event else f"{doctype}:%"]
		path_sql = ""
		if where is not None:
			if not where:
				return []
			path_sql = f" and f.path in ({','.join('?' * len(where))})"
			params += where
		rows = self.store.rows(
			"select f.path, r.line, r.target from refs r join files f on f.id = r.file_id "
			f"where r.kind = 'form_event' and r.target like ?{path_sql} order by f.path, r.line",
			*params,
		)
		prefix = params[0].removesuffix("%")
		shared = [
			r
			for r in self.shared
			if r["target"] == params[0] or (not event and r["target"].startswith(prefix))
		]
		return [{**r, "event": r["target"].split(":", 1)[1]} for r in [*rows, *shared]]

	def _assigned(self, name: str, scripts: list[str]) -> list[str]:
		return [f"{r['path']}:{r['line']}" for r in self._assigned_rows(scripts) if r["target"] == name]

	def _assigned_rows(self, scripts: list[str]) -> list[dict]:
		if not scripts:
			return []
		return self.store.rows(
			"select f.path, r.line, r.target from refs r join files f on f.id = r.file_id "
			f"where r.kind = 'cscript_event' and f.path in ({','.join('?' * len(scripts))})",
			*scripts,
		)

	def _own(self, doctype: str) -> list[str]:
		record = self.resolver.doctype(doctype)
		if record is None:
			return []
		folder = str(PurePath(record["path"]).parent)
		return [
			r["path"]
			for r in self.store.rows(
				"select path from files where path like ? and lang = 'js'", folder + "/%"
			)
		]

	def _fields_used(self, scripts: list[str]) -> str:
		if not scripts:
			return ""
		rows = self.store.rows(
			"select r.target, count(*) n from refs r join files f on f.id = r.file_id "
			f"where r.kind = 'form_field' and f.path in ({','.join('?' * len(scripts))}) "
			"group by r.target order by n desc limit ?",
			*scripts,
			self.limit * 2,
		)
		return " ".join(f"{r['target']}:{r['n']}" for r in rows)


def _class_line(cls: dict) -> str:
	if cls.get("missing"):
		return f"{cls['qualname']} (not indexed)"
	return f"{cls['qualname']}  {cls['path']}:{cls['line']}"
