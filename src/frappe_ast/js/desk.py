"""Desk JS (jQuery / vanilla) beyond frappe.ui.form.on: form fields, JS controller classes, realtime.

Desk dispatches a form event to, in order: frappe.ui.form.on(<DocType>) handlers, "*" handlers,
frm.cscript[event] (the JS controller class), then frm.cscript["custom_" + event]. So:

    erpnext.selling.QuotationController = class extends erpnext.selling.SellingController {...}
    cur_frm.script_manager.make(erpnext.selling.QuotationController)      -> form_controller ref
    extend_cscript(cur_frm.cscript, new erpnext.stock.DNController({frm}))  -> form_controller ref
    cur_frm.cscript.custom_validate = function () {}                        -> cscript_event ref
    frm.set_value("status", ...), frm.doc.status, frm.set_query("item_code", "items")  -> form_field refs

form_field targets are "field" or "table.field" with no DocType: `frm` is the form the file
serves, which queries resolve (the DocType folder, a doctype_js hook, or the file's one form).
"""

from tree_sitter import Node

from frappe_ast.js import frappe_patterns as fp
from frappe_ast.js import nodes as n


class DeskRefs:
	def __init__(self, ref):
		self.ref = ref  # _Walker._ref

	def on_call(self, callee: str, args: list[Node], node: Node, scope: list[str]):
		if not args:
			return
		receiver, _, method = callee.rpartition(".")
		if receiver in fp.FORM_OBJECTS and method in fp.FORM_FIELD_CALLS:
			self._field_call(receiver, method, args, scope)
		elif callee in fp.CONTROLLER_CALLS:
			self._controller(args[0], callee, node, scope)
		elif (
			callee in fp.CSCRIPT_EXTENDS
			and len(args) > 1
			and n.compact(n.text(args[0])) in fp.CSCRIPT_OBJECTS
		):
			self._cscript_extend(args[1], callee, node, scope)
		elif callee in fp.ROUTE_CALLS and len(args) > 1 and n.string(args[0]) == "query-report":
			self.ref("record_link", f"report:{n.string(args[1])}", callee, node, scope, "route")
		elif callee in fp.REALTIME_LISTEN:
			self.ref("realtime", n.string(args[0]), callee, node, scope, "listen")

	def on_member(self, node: Node, scope: list[str]):
		"""frm.doc.status and frm.fields_dict.items: a field of the form's DocType."""
		obj, prop = node.child_by_field_name("object"), node.child_by_field_name("property")
		if obj is None or prop is None or prop.type != "property_identifier":
			return
		owner = n.compact(n.text(obj))
		base, _, attr = owner.rpartition(".")
		if base in fp.FORM_OBJECTS and attr in ("doc", "fields_dict"):
			field = n.text(prop)
			if not field.startswith("_"):
				self.ref("form_field", field, f"{base}.{attr}", node, scope, "read")

	def on_pair(self, node: Node, scope: list[str]):
		"""{fieldname: "x", fieldtype: ...}: a control the script builds (make_control, dialogs)."""
		if n.key_text(node.child_by_field_name("key")) != "fieldname" or node.parent is None:
			return
		field = n.string(node.child_by_field_name("value"))
		if field and n.object_get(node.parent, "fieldtype") is not None:
			self.ref("script_field", field, "fieldtype", node, scope, "define")

	def on_assignment(self, left_text: str, right: Node | None, node: Node, scope: list[str]):
		"""cur_frm.cscript.x = function () {} adds an old-style handler for event x.
		frm.doc.x = ... sets a property the script owns, field or not."""
		owner, _, prop = left_text.rpartition(".")
		if owner.rpartition(".")[0] in fp.FORM_OBJECTS and owner.endswith(".doc"):
			self.ref("script_field", prop, "frm.doc", node, scope, "define")
		base, _, event = left_text.rpartition(".")
		if base in fp.CSCRIPT_OBJECTS and right is not None and right.type in n.FUNCTION_TYPES:
			self.ref("cscript_event", event, base, node, scope, "handler")

	def on_report(self, options: Node | None, report: str, scope: list[str]):
		"""frappe.query_reports["X"] = {filters: [{fieldtype: "Link", options: "Company"}]}"""
		filters = n.object_get(options, "filters")
		for item in filters.named_children if filters is not None and filters.type == "array" else []:
			if n.string(n.object_get(item, "fieldtype")) in ("Link", "MultiSelectList", "Table MultiSelect"):
				doctype = n.string(n.object_get(item, "options"))
				if doctype:
					self.ref("doctype", doctype, f"report:{report}", item, scope, "filter")

	def _field_call(self, receiver: str, method: str, args: list[Node], scope: list[str]):
		first = args[0]
		access = fp.FORM_FIELD_CALLS[method] or "read"
		if method in fp.CHILD_FIELD_CALLS and len(args) > 1 and n.string(args[1]):
			for field, at in _names(first):
				self.ref(
					"form_field", f"{n.string(args[1])}.{field}", f"{receiver}.{method}", at, scope, access
				)
			return
		# add_fetch's link field may sit in any child table: frappe defaults target_doctype to "*".
		prefix = "*." if method == "add_fetch" else ""
		for field, at in _names(first):
			if not field.startswith("__"):  # __newname and friends are Frappe keys
				self.ref("form_field", prefix + field, f"{receiver}.{method}", at, scope, access)

	def _controller(self, value: Node, via: str, node: Node, scope: list[str]):
		"""The class in `X`, `new X(...)` or `new X({frm})`."""
		if value.type == "new_expression":
			value = value.child_by_field_name("constructor")
		name = n.compact(n.text(value)) if value is not None else ""
		if name and value.type in ("identifier", "member_expression"):
			self.ref("form_controller", name, via, node, scope, "controller")

	def _cscript_extend(self, value: Node, via: str, node: Node, scope: list[str]):
		if value.type == "new_expression":
			self._controller(value, via, node, scope)
		elif value.type == "object":
			for event, member in n.object_members(value):
				self.ref("cscript_event", event, via, member, scope, "handler")


def _names(value: Node):
	"""Yield (field, node) for "f", ["f", "g"] and {f: value}."""
	if value.type == "string":
		yield n.string(value), value
	elif value.type == "array":
		for item in value.named_children:
			if item.type == "string" and n.string(item):
				yield n.string(item), item
	elif value.type == "object":
		for key, member in n.object_members(value):
			yield key, member
