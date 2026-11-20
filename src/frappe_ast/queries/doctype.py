"""`frappe-ast doctype <Name>`: schema, controller, hooks, client code and references."""

import json
from collections import Counter

from frappe_ast.python.frappe_api import LIFECYCLE_METHODS
from frappe_ast.queries.forms import FormScripts
from frappe_ast.queries.resolver import SYMBOL_COLS, Resolver
from frappe_ast.render import short_scope
from frappe_ast.store import Store

PER_DOCTYPE_HOOKS = (
	"override_doctype_class",
	"extend_doctype_class",
	"doctype_js",
	"doctype_list_js",
	"doctype_tree_js",
	"doctype_calendar_js",
	"permission_query_conditions",
	"has_permission",
	"override_doctype_dashboards",
	"standard_queries",
	"default_mail_footer",
)


class DoctypeReport:
	def __init__(self, store: Store, limit: int = 20):
		self.store = store
		self.resolver = Resolver(store)
		self.limit = limit

	def run(self, name: str, with_fields: bool = False) -> dict:
		doctype = self.resolver.doctype(name)
		if doctype is None:
			return {
				"query": name,
				"found": False,
				"similar": self._similar(name),
				"code_refs": self._code_refs(name),
			}
		name = doctype["name"]
		report = {
			"query": name,
			"found": True,
			"definition": {**doctype, "flags": json.loads(doctype["flags"])},
			"schema": self._schema(name, with_fields),
			"controller": self._controller(doctype),
			"hooks": self._hooks(name),
			"form_events": self._form_events(name),
			"client_registrations": self._registrations(name),
			"linked_from": self._linked_from(name),
			"code_refs": self._code_refs(name),
			"form": self._form(name),
			"records": self._records(name),
			"client_methods": self._client_methods(name),
			"client_fields": self._client_fields(name),
		}
		return report

	def _schema(self, name: str, with_fields: bool) -> dict:
		fields = self.store.rows("select * from fields where doctype = ? order by idx", name)
		data_fields = [f for f in fields if f["fieldname"]]
		schema = {
			"field_count": len(data_fields),
			"by_type": dict(Counter(f["fieldtype"] for f in data_fields).most_common()),
			"links": [f"{f['fieldname']} -> {f['options']}" for f in data_fields if f["fieldtype"] == "Link"],
			"child_tables": [
				f"{f['fieldname']} -> {f['options']}"
				for f in data_fields
				if f["fieldtype"] in ("Table", "Table MultiSelect")
			],
			"mandatory": [f["fieldname"] for f in data_fields if '"reqd": 1' in f["attrs"]],
		}
		if with_fields:
			schema["fields"] = [_field_line(f) for f in data_fields]
		return schema

	def _controller(self, doctype: dict) -> dict:
		cls = self.resolver.controller_class(doctype)
		if cls is None:
			return {"path": doctype["path"].removesuffix(".json") + ".py", "class": None}
		methods = self.store.rows(
			f"select {SYMBOL_COLS} from symbols s join files f on f.id = s.file_id "
			"where f.path = ? and s.parent = ? and s.kind = 'method' order by s.line",
			cls["path"],
			cls["qualname"],
		)
		return {
			"path": cls["path"],
			"class": cls["qualname"],
			"bases": cls["signature"],
			"lifecycle": [f"{m['name']}:{m['line']}" for m in methods if m["name"] in LIFECYCLE_METHODS],
			"whitelisted": [f"{m['name']}:{m['line']}" for m in methods if m["whitelisted"]],
			"other_methods": len(
				[m for m in methods if m["name"] not in LIFECYCLE_METHODS and not m["whitelisted"]]
			),
		}

	def _hooks(self, name: str) -> dict:
		events = self.store.rows(
			"select app, p1 as doctype, p2 as event, value, line from hooks "
			"where key = 'doc_events' and p1 in (?, '*') order by p1 = '*', p2",
			name,
		)
		marks = ",".join("?" * len(PER_DOCTYPE_HOOKS))
		others = self.store.rows(
			f"select app, key, value, line from hooks where key in ({marks}) and p1 = ?",
			*PER_DOCTYPE_HOOKS,
			name,
		)
		return {"doc_events": events, "per_doctype": others}

	def _form_events(self, name: str) -> list[dict]:
		rows = self.store.rows(
			"select f.path, r.target, r.line from refs r join files f on f.id = r.file_id "
			"where r.kind = 'form_event' and r.target like ? order by f.path, r.line",
			name + ":%",
		)
		by_file: dict[str, list[str]] = {}
		for row in rows:
			by_file.setdefault(row["path"], []).append(f"{row['target'].split(':', 1)[1]}:{row['line']}")
		return [f"{path}: {' '.join(events)}" for path, events in by_file.items()]

	def _registrations(self, name: str) -> list[dict]:
		return self.store.rows(
			"select f.path, r.kind, r.line from refs r join files f on f.id = r.file_id "
			"where r.kind in ('listview', 'treeview', 'calendar') and r.target = ?",
			name,
		)

	def _linked_from(self, name: str) -> list[str]:
		rows = self.store.rows(
			"select doctype, fieldname, fieldtype from fields where options = ? "
			"and fieldtype in ('Link', 'Table', 'Table MultiSelect') order by fieldtype, doctype",
			name,
		)
		return [f"{r['doctype']}.{r['fieldname']} ({r['fieldtype']})" for r in rows]

	def _code_refs(self, name: str) -> dict:
		rows = self.store.rows(
			"select f.app, f.path, r.via, r.access, r.line, r.scope from refs r join files f on f.id = r.file_id "
			"where r.kind = 'doctype' and r.target = ? order by f.path, r.line",
			name,
		)
		return {
			"total": len(rows),
			"by_access": dict(Counter(r["access"] for r in rows).most_common()),
			"by_app": dict(Counter(r["app"] for r in rows).most_common()),
			"writes": [_ref_line(r) for r in rows if r["access"] in ("write", "delete")][: self.limit],
			"sample": [_ref_line(r) for r in rows][: self.limit],
		}

	def _form(self, name: str) -> str:
		"""One line; `form <DocType>` has the scripts, the controller chain and every handler."""
		forms = FormScripts(self.store, self.resolver)
		controllers = forms.controllers(name)
		scripts = forms.scripts(name)
		if not scripts:
			return ""
		controller = f", controller {controllers[0]['target']}" if controllers else ""
		return f'{len(scripts)} scripts{controller} (run: form "{name}")'

	def _records(self, name: str) -> list[str]:
		"""Reports, charts, cards, web forms, print formats, notifications ... that target this DocType."""
		rows = self.store.rows(
			"select distinct s.kind, s.name, f.path from refs r join files f on f.id = r.file_id "
			"join symbols s on s.file_id = f.id where r.kind = 'doctype' and r.target = ? and f.lang = 'record' "
			"order by s.kind, s.name limit ?",
			name,
			self.limit * 2,
		)
		return [f"{r['kind']} {r['name']}  {r['path']}" for r in rows]

	def _client_methods(self, name: str) -> list[str]:
		"""frappe-ui calls that run this DocType's methods: useDoc, useDoctype, v2 URLs."""
		rows = self.store.rows(
			"select r.kind, r.target, count(*) n, min(f.path || ':' || r.line) at from refs r "
			"join files f on f.id = r.file_id where r.kind in ('doc_method', 'module_method') "
			"and r.target like ? group by r.kind, r.target order by n desc",
			name + ":%",
		)
		return [f"{r['target'].split(':', 1)[1]} ({r['kind']}, {r['n']}x) {r['at']}" for r in rows][
			: self.limit
		]

	def _client_fields(self, name: str) -> dict:
		"""Fields client code names next to this DocType: what a rename or removal breaks."""
		rows = self.store.rows(
			"select substr(r.target, ?) field, count(*) n from refs r where r.kind = 'field' "
			"and r.target like ? group by field order by n desc",
			len(name) + 2,
			name + ".%",
		)
		custom = self.store.rows(
			"select r.target, f.path, r.line from refs r join files f on f.id = r.file_id "
			"where r.kind = 'custom_field' and r.via = ? order by f.path, r.line",
			name,
		)
		return {
			"read_by_client": " ".join(f"{r['field']}:{r['n']}" for r in rows[: self.limit * 2]),
			"custom_fields": [f"{c['target']} {c['path']}:{c['line']}" for c in custom][: self.limit],
		}

	def _similar(self, name: str) -> list[str]:
		rows = self.store.rows("select name from doctypes where name like ? limit 10", f"%{name}%")
		return [r["name"] for r in rows]


def _field_line(f: dict) -> str:
	attrs = json.loads(f["attrs"])
	flags = [k for k in ("reqd", "read_only", "hidden", "unique", "allow_on_submit") if attrs.get(k)]
	cond = {
		k: attrs[k]
		for k in ("depends_on", "mandatory_depends_on", "read_only_depends_on", "fetch_from")
		if k in attrs
	}
	text = f"{f['fieldname']} {f['fieldtype']}"
	if f["options"]:
		text += f"[{f['options'][:60]}]"
	if flags:
		text += " " + ",".join(flags)
	if cond:
		text += " " + " ".join(f"{k}={v}" for k, v in cond.items())
	return text


def _ref_line(r: dict) -> str:
	return f"{r['path']}:{r['line']} {r['via']} ({r['access']}) in {short_scope(r['path'], r['scope'])}"
