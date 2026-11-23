"""Which Python method a desk `frm.call("x")` or `frappe.call({doc, method: "x"})` runs.

The call names only the method. The DocType is the form's, which queries/contexts.py resolves
for shared code too. The method is looked up on each form's controller and its bases.
"""

from frappe_ast.queries.contexts import FormContexts
from frappe_ast.queries.doc_checks import DocTypeClientChecks, controller_module
from frappe_ast.queries.resolver import Resolver
from frappe_ast.store import Store

REF_KINDS = {"method": "doc_method", "function": "module_method"}


class DocCalls:
	def __init__(self, store: Store, resolver: Resolver):
		self.store = store
		self.resolver = resolver
		self.contexts = FormContexts(store, resolver)
		self.checks = DocTypeClientChecks(store, resolver)

	def trace(self, symbol: dict) -> tuple[list[dict], list[dict]]:
		"""(calls that run `symbol`, whitelisted-name calls whose form static analysis cannot know)."""
		kind = REF_KINDS.get(symbol["kind"])
		if not kind:
			return [], []
		calls, maybe = [], []
		for row in self._refs(kind, symbol["name"]):
			doctype, _, _ = row.pop("target").rpartition(":")
			# "?:x" is a doc whose DocType the code does not name (`doc: connected_app`).
			doctypes = [doctype] if doctype else self.contexts.doctypes(row["path"], row["scope"])
			doctypes = [] if doctype == "?" else doctypes
			if not doctypes:
				if symbol["whitelisted"]:
					maybe.append(row)
			elif any(self._runs(kind, d, symbol["name"]) == symbol["qualname"] for d in doctypes):
				calls.append(row)
		return calls, maybe

	def _refs(self, kind: str, name: str) -> list[dict]:
		return self.store.rows(
			"select f.path, r.line, r.via, r.scope, r.target from refs r join files f on f.id = r.file_id "
			"where r.kind = ? and (r.target = ? or r.target like ?) and f.app not like '%@%' "
			"order by f.path, r.line",
			kind,
			name,
			"%:" + name,
		)

	def _runs(self, kind: str, doctype_name: str, name: str) -> str:
		"""Qualname of the method `name` that a call on this DocType's form runs, or ""."""
		doctype = self.resolver.doctype(doctype_name)
		if doctype is None:
			return ""
		if kind == "module_method":
			symbol = self.resolver.symbol(controller_module(doctype["path"]) + "." + name)
		else:
			cls = self.resolver.controller_class(doctype)
			symbol = self.checks.find_doc_method(doctype["name"], cls, name)[0] if cls else None
		return symbol["qualname"] if symbol else ""
