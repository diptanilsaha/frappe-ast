"""DocTypes and fields named as data: in frappe.client.* params, and next to `doctype` keys.

    createResource({url: "frappe.client.set_value", params: {doctype: "X", fieldname: "status"}})
    call("frappe.client.insert", {doc: {doctype: "X"}})
    createListResource({doctype: "X", fields: ["name", "title"], filters: {status: "Open"}})

Field refs have target "<DocType>.<field>" so a field rename can be traced to the frontends.
"""

import re

from tree_sitter import Node

from frappe_ast.js import frappe_patterns as fp
from frappe_ast.js import nodes as n

PLAIN_FIELD_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
MAX_DEPTH = 6  # params -> doc -> doctype, or makeParams() { return {...} }


class DocTypeData:
	def __init__(self, ref):
		self.ref = ref  # _Walker._ref

	def on_client_method(self, method: str, args: list[Node], node: Node, scope: list[str]):
		"""A server call to frappe.client.*: the DocType sits somewhere in its arguments."""
		access = fp.CLIENT_METHODS.get(method)
		if not access:
			return
		holder = next((h for h in (_doctype_holder(a, 0) for a in args) if h is not None), None)
		if holder is None:
			return
		self.doctype_object(holder, method, access, node, scope)
		if method in fp.DOC_FIELD_METHODS:
			doctype = n.string(n.object_get(holder, "doctype"))
			for key, member in n.object_members(holder):
				if key not in fp.NON_FIELD_KEYS and key not in fp.FIELD_KEYS:
					for field, at in _plain(key, member):
						self.ref("field", f"{doctype}.{field}", method, at, scope, "write")

	def on_doctype_call(self, callee: str, args: list[Node], access: str, node: Node, scope: list[str]):
		"""useList({doctype, fields}), or frappe.db.get_value("X", name, "field") with a string DocType."""
		if args[0].type == "object":
			self.doctype_object(args[0], callee, access, node, scope)
			return
		doctype = n.string(args[0])
		if not doctype:
			return
		self.ref("doctype", doctype, callee, node, scope, access)
		position = fp.FIELD_ARGS.get(callee)
		values = [args[position]] if position is not None and len(args) > position else []
		if len(args) > 1 and args[1].type == "object":
			values += [n.object_get(args[1], key) for key in fp.FIELD_KEYS]  # get_list("X", {fields})
		for value in values:
			for field, at in _field_names(value):
				self.ref("field", f"{doctype}.{field}", callee, at, scope, access)

	def doctype_object(self, obj: Node, via: str, access: str, node: Node, scope: list[str]):
		"""`{doctype: "X", fields: [...], ...}`: a doctype ref plus a field ref per named field."""
		doctype = n.string(n.object_get(obj, "doctype"))
		if not doctype:
			return
		self.ref("doctype", doctype, via, node, scope, access)
		for key in fp.FIELD_KEYS:
			value = n.object_get(obj, key)
			for field, at in _field_names(value):
				self.ref(
					"field", f"{doctype}.{field}", via, at, scope, access if key == "fieldname" else "read"
				)


def _doctype_holder(node: Node | None, depth: int) -> Node | None:
	"""The first object with a literal `doctype` key, searched depth first."""
	if node is None or depth > MAX_DEPTH:
		return None
	if node.type == "object" and n.string(n.object_get(node, "doctype")):
		return node
	for child in node.named_children:
		found = _doctype_holder(child, depth + 1)
		if found is not None:
			return found
	return None


def _field_names(value: Node | None):
	"""Yield (field, node) for "f", ["f", "g"], {f: ...} and [["f", "=", 1]] filters."""
	if value is None:
		return
	if value.type in ("string", "template_string"):
		yield from _plain(n.string(value).split(" ")[0], value)
	elif value.type == "array":
		for item in value.named_children:
			if item.type == "array" and item.named_children:
				item = item.named_children[0]  # [["status", "=", "Open"]]
			if item.type == "string":
				yield from _plain(n.string(item).split(" ")[0], item)
	elif value.type == "object":
		for key, member in n.object_members(value):
			yield from _plain(key, member)


def _plain(field: str, node: Node):
	"""Skip child-table paths, SQL expressions and `*`: only plain fieldnames are checkable."""
	if PLAIN_FIELD_RE.match(field) and not field.startswith("__"):  # __newname, __islocal: Frappe keys
		yield field, node
