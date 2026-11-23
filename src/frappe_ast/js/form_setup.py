"""How shared desk JS (public/js) attaches itself to forms.

erpnext writes form code once and installs it on many forms:
- frappe.ui.form.on(<DocType>, {event: fn}) or (<DocType>, "event", fn). The DocType is a literal,
  the form's own (`this.frm.doctype`, recorded as "@self", "@self Item" for `+ " Item"`), or the
  first parameter of a setup function (recorded as "$"): `erpnext.accounts.taxes.setup_tax_validations
  = function (doctype) {...}`, called as `setup_tax_validations("Sales Invoice")` (a `setup_call` ref).
- Namespaces: `erpnext.sales_common = {f() {}}` and `$.extend(erpnext.utils, {f() {}})` define
  erpnext.sales_common.f and erpnext.utils.f.
- Helper classes a form creates: `new erpnext.utils.CRMNotes({frm})` (a `js_new` ref).
queries/contexts.py resolves the forms each of these runs on.
"""

import re

from tree_sitter import Node

from frappe_ast.js import frappe_patterns as fp
from frappe_ast.js import nodes as n

DOCTYPE_NAME_RE = re.compile(r"^[A-Z][\w-]*( [\w&()-]+){0,7}$")


class FormSetups:
	def __init__(self, ref, known_apps: set[str]):
		self.ref = ref  # _Walker._ref
		self.known_apps = known_apps

	def on_form_on(self, node: Node, args: list[Node], scope: list[str]) -> list[str] | None:
		"""frappe.ui.form.on(...): form_event refs for a literal DocType, setup_event refs otherwise."""
		if not args:
			return None
		doctype = n.string(args[0])
		tag = doctype or self._tag(args[0], node)
		if not tag:
			return None
		if doctype:
			self.ref("doctype", doctype, "frappe.ui.form.on", node, scope, "form")
		kind = "form_event" if doctype else "setup_event"
		for event, value in _events(args[1:]):
			self.ref(kind, f"{tag}:{event}", "frappe.ui.form.on", value, scope, "handler")
		return [*scope, f"form:{tag}"]

	def on_call(self, callee: str, args: list[Node], node: Node, scope: list[str]):
		"""`erpnext.accounts.pos.setup("Sales Invoice")`: an app function given a DocType name."""
		if not args or callee.split(".")[0] not in self.known_apps or "." not in callee:
			return
		doctype = n.string(args[0])
		if DOCTYPE_NAME_RE.match(doctype):
			self.ref("setup_call", doctype, callee, node, scope, "call")

	def on_new(self, node: Node, scope: list[str]):
		ctor = node.child_by_field_name("constructor")
		name = n.compact(n.text(ctor)) if ctor is not None and ctor.type == "member_expression" else ""
		if name.split(".")[0] in self.known_apps:
			self.ref("js_new", name, "new", node, scope, "call")

	def namespace(self, target: str, value: Node | None) -> list[str] | None:
		"""Scope for the members of `target = {...}` and `$.extend(target, {...})`."""
		if value is None or value.type != "object" or target in fp.CSCRIPT_OBJECTS:
			return None
		dotted = re.match(r"^[A-Za-z_$][\w$]*(\.[A-Za-z_$][\w$]*)+$", target)
		if not dotted and target not in self.known_apps:  # `$.extend(hrms, {...})`
			return None
		return target.split(".")

	def _tag(self, arg: Node, node: Node) -> str:
		value = n.compact(n.text(arg))
		if value in fp.FORM_SELF:
			return "@self"
		if (
			arg.type == "binary_expression"
			and n.compact(n.text(arg.child_by_field_name("left"))) in fp.FORM_SELF
		):
			suffix = n.string(arg.child_by_field_name("right"))
			return f"@self{suffix}" if suffix else ""
		if arg.type == "identifier" and n.enclosing_params(node)[:1] == [value]:
			return "$"
		return ""


def _events(args: list[Node]):
	"""(event, handler) for ({event: fn}) and ("event", fn) / (["a", "b"], fn)."""
	if not args:
		return
	if args[0].type == "object":
		for obj in (a for a in args if a.type == "object"):
			yield from n.object_members(obj)
	elif len(args) > 1:
		names = (
			[n.string(c) for c in args[0].named_children] if args[0].type == "array" else [n.string(args[0])]
		)
		for name in (x for x in names if x):
			yield name, args[1]
