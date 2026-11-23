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
