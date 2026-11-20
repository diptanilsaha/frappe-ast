"""Client code that names a DocType's methods or fields: FX204 and FX207.

A doc_method / module_method target is "x" (frm.call in the DocType's own form script,
DocType taken from the folder) or "<DocType>:x" (frappe-ui useDoc, useDoctype, v2 URLs).
"""

import re

from frappe_ast.queries.finding import finding
from frappe_ast.queries.forms import FormScripts
from frappe_ast.queries.resolver import Resolver
from frappe_ast.store import Store

# Columns every DocType table has without declaring them in its JSON.
STANDARD_FIELDS = {
	"name", "owner", "creation", "modified", "modified_by", "docstatus", "idx", "parent",
	"parentfield", "parenttype", "_assign", "_comments", "_liked_by", "_user_tags", "_seen",
	"lft", "rgt", "old_parent", "doctype",
}  # fmt: skip
# cur_frm is whichever form is open now (after frappe.new_doc, another one); tests set up their own.
UNSURE_FORM_RE = re.compile(
	r"(^|/)(_?test_[^/]*|[^/]*\.(spec|cy|test)\.[jt]s)$|/(playwright|cypress|tests?)/"
)
REF_ROWS = (
	"select f.path, r.target, r.line, r.scope, r.via from refs r join files f on f.id = r.file_id "
	"where r.kind = ? and f.path like ? and f.app not like '%@%'"
)


class DocTypeClientChecks:
	def __init__(self, store: Store, resolver: Resolver):
		self.store = store
		self.resolver = resolver

	def methods(self, prefix: str) -> list[dict]:
		out = []
		for row in self.store.rows(REF_ROWS, "doc_method", prefix + "%"):
			doctype_name, method = self._split(row)
			doctype = self.resolver.doctype(doctype_name) if doctype_name else None
			cls = self.resolver.controller_class(doctype) if doctype else None
			if cls is None or "." in method:
				continue
			found, complete = self._find_doc_method(doctype["name"], cls, method)
			if found is None and complete:
				out.append(_fx204(f"{cls['name']} has no method {method}", row))
			elif found is not None and not found["whitelisted"]:
				out.append(_fx204(f"{cls['name']}.{method} is not @frappe.whitelist()", row))
		return out + self._module_methods(prefix)

	def fields(self, prefix: str) -> list[dict]:
		"""FX207: a field named next to `doctype` that the DocType JSON does not declare."""
		out = []
		known: dict[str, set[str] | None] = {}
		for row in self.store.rows(REF_ROWS, "field", prefix + "%"):
			doctype, _, field = row["target"].rpartition(".")
			if doctype not in known:
				known[doctype] = self._fieldnames(doctype)
			fields = known[doctype]
			if fields is None or field in fields or field in STANDARD_FIELDS:
				continue
			msg = f"{doctype} has no field {field} (custom field, or a typo?)"
			out.append(finding("FX207", "warning", msg, row["path"], row["line"], row["scope"]))
		return out

	def form_fields(self, prefix: str) -> list[dict]:
		"""FX207 in desk JS: frm.set_value("x"), frm.doc.x, frm.set_query("x", "items") on the file's form."""
		out = []
		forms = FormScripts(self.store, self.resolver)
		by_file: dict[str, tuple[list[str], set[str] | None]] = {}
		for row in self.store.rows(REF_ROWS, "form_field", prefix + "%"):
			if row["via"].startswith("cur_frm") or UNSURE_FORM_RE.search(row["path"]):
				continue
			if row["path"] not in by_file:
				doctypes = forms.doctypes_for_file(row["path"])
				by_file[row["path"]] = (doctypes, self._union(doctypes), self._script_fields(row["path"]))
			doctypes, fields, own = by_file[row["path"]]
			table, _, field = row["target"].rpartition(".")
			if fields is None:
				continue
			if table == "*":
				children = {c for d in doctypes for c in forms.child_tables(d).values()}
				fields = fields | (self._union(sorted(children)) or set())
			elif table:
				children = {forms.child_tables(d).get(table) for d in doctypes} - {None}
				if not children:
					continue  # `items` is not a table here: a dynamic path, not ours to judge
				fields = self._union(sorted(children))
				if fields is None:
					continue
			if field in fields or field in STANDARD_FIELDS or field in own:
				continue
			where = " / ".join(doctypes) + (f" table {table}" if table else "")
			msg = f"{where} has no field {field} (custom field, or a typo?)"
			out.append(finding("FX207", "warning", msg, row["path"], row["line"], row["scope"]))
		return out

	def _script_fields(self, path: str) -> set[str]:
		"""Names the file itself defines: frm.doc.x = ..., and controls built with {fieldname, fieldtype}."""
		rows = self.store.rows(
			"select distinct r.target from refs r join files f on f.id = r.file_id "
			"where f.path = ? and r.kind = 'script_field'",
			path,
		)
		return {r["target"] for r in rows}

	def _union(self, doctypes: list[str]) -> set[str] | None:
		"""Fields of any of these DocTypes; None when one of them is not indexed (cannot judge)."""
		fields: set[str] = set()
		for doctype in doctypes:
			known = self._fieldnames(doctype)
			if known is None:
				return None
			fields |= known
		return fields if doctypes else None

	def _module_methods(self, prefix: str) -> list[dict]:
		"""frm.call({method: "x"}) without `doc`, and useDoctype(X).runMethod, call <controller module>.x."""
		out = []
		for row in self.store.rows(REF_ROWS, "module_method", prefix + "%"):
			doctype_name, method = self._split(row)
			doctype = self.resolver.doctype(doctype_name or "")
			if doctype is None:
				continue
			dotted = controller_module(doctype["path"]) + "." + method
			symbol = self.resolver.symbol(dotted)
			if symbol is None or not symbol["whitelisted"]:
				state = "does not exist" if symbol is None else "is not @frappe.whitelist()"
				out.append(_fx204(f"{dotted} {state}", row))
		return out

	def _split(self, row: dict) -> tuple[str, str]:
		doctype, _, method = row["target"].rpartition(":")
		return doctype or self.resolver.doctype_for_file(row["path"]) or "", method

	def _find_doc_method(self, doctype: str, cls: dict, name: str) -> tuple[dict | None, bool]:
		"""Search the controller, then classes other apps swap in or mix in via hooks."""
		classes = [cls]
		for hook in self.store.rows(
			"select value from hooks where key in ('override_doctype_class', 'extend_doctype_class') and p1 = ?",
			doctype,
		):
			extra = self.resolver.symbol(hook["value"])
			if extra is None:
				return None, False
			classes.append(extra)
		complete = True
		for candidate in classes:
			method, candidate_complete = self.resolver.find_method(candidate, name)
			if method is not None:
				return method, True
			complete = complete and candidate_complete
		return None, complete

	def _fieldnames(self, doctype: str) -> set[str] | None:
		"""Declared fields plus custom fields any app ships; None when the DocType is not indexed."""
		if not self.resolver.doctype(doctype):
			return None
		rows = self.store.rows(
			"select fieldname from fields where doctype = ? union select target from refs "
			"where kind = 'custom_field' and via = ?",
			doctype,
			doctype,
		)
		return {r["fieldname"] for r in rows}


def controller_module(json_path: str) -> str:
	"""apps/erpnext/erpnext/x/doctype/y/y.json -> erpnext.x.doctype.y.y"""
	return ".".join(json_path.removesuffix(".json").split("/")[2:])


def _fx204(message: str, row: dict) -> dict:
	return finding("FX204", "error", message, row["path"], row["line"], row["scope"])
