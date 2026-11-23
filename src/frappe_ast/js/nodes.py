"""Small readers for tree-sitter nodes, shared by the JS walkers."""

import re

from tree_sitter import Node

FUNCTION_TYPES = {"function_expression", "function", "arrow_function", "generator_function"}


def text(node: Node | None) -> str:
	return node.text.decode(errors="replace") if node is not None and node.text else ""


def compact(value: str) -> str:
	return re.sub(r"\s+", "", value)[:160]


def string(node: Node | None) -> str:
	"""Value of a string literal or a template string without substitutions."""
	if node is None:
		return ""
	if node.type == "string":
		return "".join(text(c) for c in node.children if c.type in ("string_fragment", "escape_sequence"))
	if node.type == "template_string" and not any(c.type == "template_substitution" for c in node.children):
		return text(node)[1:-1]
	return ""


def key_text(node: Node | None) -> str:
	return string(node) if node is not None and node.type == "string" else text(node)


def arguments(call: Node) -> list[Node]:
	args = call.child_by_field_name("arguments")
	return [c for c in args.named_children if c.type != "comment"] if args is not None else []


def imported_names(node: Node) -> list[str]:
	"""Names an import binds: the local name of a default import, else the exported name."""
	names = []
	for clause in (c for c in node.children if c.type == "import_clause"):
		for child in clause.named_children:
			if child.type == "identifier":
				names.append(text(child))
			elif child.type == "named_imports":
				for spec in (c for c in child.named_children if c.type == "import_specifier"):
					names.append(text(spec.child_by_field_name("name")))
	return [n for n in names if n]


def object_get(node: Node | None, key: str) -> Node | None:
	if node is None or node.type != "object":
		return None
	for name, value in object_members(node):
		if name == key:
			return value
	return None


def object_members(obj: Node):
	"""Yield (key, value) for pairs, methods and shorthand properties."""
	for child in obj.named_children:
		if child.type == "pair":
			yield key_text(child.child_by_field_name("key")), child.child_by_field_name("value")
		elif child.type == "method_definition":
			yield text(child.child_by_field_name("name")), child
		elif child.type == "shorthand_property_identifier":
			yield text(child), child


def params(node: Node) -> str:
	target = node
	for field in ("value", "right"):
		candidate = node.child_by_field_name(field)
		if candidate is not None and candidate.type in FUNCTION_TYPES:
			target = candidate
	found = target.child_by_field_name("parameters") or target.child_by_field_name("parameter")
	return compact(text(found))[:120] if found is not None else ""


def type_members(body: Node | None) -> list[str]:
	"""Member names of an interface body or object type, `?` kept for optional ones."""
	if body is None:
		return []
	names = []
	for member in body.named_children:
		if member.type in ("property_signature", "method_signature"):
			optional = any(c.type == "?" for c in member.children)
			names.append(key_text(member.child_by_field_name("name")) + ("?" if optional else ""))
		elif member.type == "call_signature":
			# Old-style emits: (e: "update:open", value: boolean): void
			event = re.search(r"""^\(\s*\w+\s*:\s*['"]([^'"]+)['"]""", text(member))
			if event:
				names.append(event.group(1))
	return names


def pattern(node: Node) -> str:
	"""A template string with each substitution replaced by `*`."""
	parts = []
	for child in node.children:
		if child.type == "template_substitution":
			parts.append("*")
		elif child.type not in ("`",):
			parts.append(text(child))
	return "".join(parts)


def heritage(cls: Node) -> str:
	"""`(erpnext.TransactionController)` for `class X extends erpnext.TransactionController`."""
	clause = next((c for c in cls.named_children if c.type == "class_heritage"), None)
	if clause is None:
		return ""
	return "(" + compact(text(clause)).removeprefix("extends").split("implements")[0] + ")"


def registry_key(left: Node, registry: str) -> str:
	"""The key of `registry["X"]`, also inside `frappe.pages["x"].on_page_load`."""
	node = left
	while node is not None and node.type in ("member_expression", "subscript_expression"):
		if (
			node.type == "subscript_expression"
			and compact(text(node.child_by_field_name("object"))) == registry
		):
			return string(node.child_by_field_name("index"))
		node = node.child_by_field_name("object")
	return ""


def enclosing_params(node: Node) -> list[str]:
	"""Parameter names of the nearest function around `node`."""
	parent = node.parent
	while parent is not None and parent.type not in (
		*FUNCTION_TYPES,
		"method_definition",
		"function_declaration",
	):
		parent = parent.parent
	if parent is None:
		return []
	found = parent.child_by_field_name("parameters") or parent.child_by_field_name("parameter")
	if found is None:
		return []
	if found.type == "identifier":
		return [text(found)]
	names = (
		c.child_by_field_name("left") if c.type == "assignment_pattern" else c for c in found.named_children
	)
	return [text(c) for c in names if c is not None and c.type == "identifier"]


def binding(node: Node, name: str) -> tuple[str, Node | None] | None:
	"""("param", function) or ("var", initial value) for the nearest binding of `name` around `node`."""
	parent = node.parent
	while parent is not None:
		if parent.type in (*FUNCTION_TYPES, "method_definition", "function_declaration"):
			if name in enclosing_params(_first_child(parent)):
				return "param", parent
			value = _declared(parent.child_by_field_name("body"), name)
			if value is not None:
				return "var", value
		parent = parent.parent
	return None


def _first_child(fn: Node) -> Node:
	"""A node inside `fn`, so enclosing_params(...) reads fn's own parameters."""
	return fn.child_by_field_name("body") or fn


def _declared(body: Node | None, name: str) -> Node | None:
	"""Initial value of `var/let/const name = ...` in `body`, not inside nested functions."""
	stack = [body] if body is not None else []
	while stack:
		node = stack.pop()
		if node.type == "variable_declarator" and text(node.child_by_field_name("name")) == name:
			return node.child_by_field_name("value") or node
		stack += [
			c for c in node.named_children if c.type not in (*FUNCTION_TYPES, "function_declaration", "class")
		]
	return None
