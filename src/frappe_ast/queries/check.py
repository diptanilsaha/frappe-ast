"""`frappe-ast check`: per-file findings plus cross-file checks over the index.

Cross-file rules:
  FX201 error    hooks.py names a dotted path that does not exist
  FX202 error    client calls a server method that does not exist
  FX203 error    client calls a server method that is not whitelisted
  FX204 error    client runs DocType method x (frm.call, useDoc, useDoctype) that is missing or not whitelisted
  FX205 info     form handler for a name that is no field, event or trigger of the DocType
  FX206 warning  required_apps names an app that is not in this bench (its references go unchecked)
  FX207 warning  client or record names a field (fields, filters, frm.set_value, frm.doc.x) the DocType does not declare
  FX208 error    a Report or Page JSON and its .py / .js disagree (no execute(), registered under another name)
"""

from frappe_ast.queries.apps import app_name_of
from frappe_ast.queries.doc_checks import DocTypeClientChecks
from frappe_ast.queries.finding import finding as _finding
from frappe_ast.queries.forms import FORM_EVENTS, ROW_EVENT_SUFFIXES
from frappe_ast.queries.record import RecordChecks
from frappe_ast.queries.resolver import Resolver
from frappe_ast.store import Store

# Hook values that are not Python paths (file paths, routes, labels).
NON_PATH_HOOKS = {"doctype_js", "doctype_list_js", "doctype_tree_js", "doctype_calendar_js", "page_js", "app_include_js",
	"app_include_css", "web_include_js", "web_include_css", "website_route_rules", "fixtures", "required_apps"}  # fmt: skip


class Checker:
	def __init__(self, store: Store, known_apps: set[str]):
		self.store = store
		self.resolver = Resolver(store)
		self.known_apps = known_apps
		self.doc = DocTypeClientChecks(store, self.resolver)

	def run(self, path_prefix: str = "", rules: set[str] | None = None, cross: bool = True) -> list[dict]:
		findings = self._file_findings(path_prefix)
		if cross:
			findings += self._hook_paths(path_prefix) + self._client_calls(path_prefix)
			findings += (
				self.doc.methods(path_prefix)
				+ self.doc.fields(path_prefix)
				+ self.doc.form_fields(path_prefix)
			)
			findings += self._form_handlers(path_prefix)
			findings += self._missing_required_apps(path_prefix) + RecordChecks(self.store).run(path_prefix)
		if rules:
			findings = [f for f in findings if f["rule"] in rules]
		return sorted(findings, key=lambda f: (f["path"], f["line"]))

	def _file_findings(self, prefix: str) -> list[dict]:
		return self.store.rows(
			"select x.rule, x.severity, x.message, f.path, x.line, x.scope from findings x "
			"join files f on f.id = x.file_id where f.path like ? and f.app not like '%@%'",
			prefix + "%",
		)

	def _hook_paths(self, prefix: str) -> list[dict]:
		out = []
		for hook in self.store.rows("select * from hooks"):
			value = hook["value"]
			if (
				hook["key"] in NON_PATH_HOOKS
				or value.split(".")[0] not in self.known_apps
				or "/" in value
				or "(" in value
			):
				continue
			path = f"apps/{hook['app']}/{hook['app']}/hooks.py"
			if path.startswith(prefix) and "." in value and not self.resolver.resolves(value):
				where = " > ".join(p for p in (hook["key"], hook["p1"], hook["p2"]) if p)
				out.append(
					_finding("FX201", "error", f"{where}: {value} does not resolve", path, hook["line"])
				)
		return out

	def _client_calls(self, prefix: str) -> list[dict]:
		out = []
		rows = self.store.rows(
			"select f.path, r.target, r.line, r.scope from refs r join files f on f.id = r.file_id "
			"where r.kind = 'method' and f.lang != 'python' and f.path like ? and f.app not like '%@%'",
			prefix + "%",
		)
		for row in rows:
			target = row["target"]
			if target.split(".")[0] not in self.known_apps:
				continue
			symbol = self.resolver.symbol(target)
			if symbol is None and not self._overridden(target):
				out.append(
					_finding(
						"FX202", "error", f"{target} does not exist", row["path"], row["line"], row["scope"]
					)
				)
			elif (
				symbol is not None and not symbol["whitelisted"] and symbol["kind"] in ("function", "method")
			):
				msg = f"{target} is not @frappe.whitelist()"
				out.append(_finding("FX203", "error", msg, row["path"], row["line"], row["scope"]))
		return out

	def _form_handlers(self, prefix: str) -> list[dict]:
		out = []
		rows = self.store.rows(
			"select f.path, r.target, r.line from refs r join files f on f.id = r.file_id "
			"where r.kind = 'form_event' and f.path like ? and f.app not like '%@%'",
			prefix + "%",
		)
		triggers = {
			r["target"]
			for r in self.store.rows("select distinct target from refs where kind = 'form_trigger'")
		}
		# Helpers in the handler object are also called as frm.events.x(frm).
		triggers |= {
			r["callee"].rsplit(".", 1)[-1]
			for r in self.store.rows("select distinct callee from calls where callee like '%events.%'")
		}
		fields_cache: dict[str, set[str] | None] = {}
		for row in rows:
			doctype, _, event = row["target"].partition(":")
			if doctype not in fields_cache:
				fields_cache[doctype] = self.doc.fieldnames(doctype)
			fields = fields_cache[doctype]
			if fields is None or event in FORM_EVENTS or event in fields or event in triggers:
				continue
			if event.endswith(ROW_EVENT_SUFFIXES):
				continue
			msg = f"{doctype} has no field or event '{event}' (custom field or dead handler?)"
			out.append(_finding("FX205", "info", msg, row["path"], row["line"]))
		return out

	def _missing_required_apps(self, prefix: str) -> list[dict]:
		out = []
		for hook in self.store.rows("select app, value, line from hooks where key = 'required_apps'"):
			path = f"apps/{hook['app']}/{hook['app']}/hooks.py"
			needed = app_name_of(hook["value"])
			if needed not in self.known_apps and path.startswith(prefix):
				msg = f"{hook['app']} requires {needed}, which is not in this bench; calls into it are not checked"
				out.append(_finding("FX206", "warning", msg, path, hook["line"]))
		return out

	def _overridden(self, target: str) -> bool:
		return bool(
			self.store.rows(
				"select 1 from hooks where key = 'override_whitelisted_methods' and p1 = ?", target
			)
		)
