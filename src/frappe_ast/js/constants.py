"""Method paths kept in constants: `const URL = "app.api.x"` and `const API = {get: "app.api.get"}`.

Apps call them as call(URL) or createResource({url: API.get}). The analyzer resolves a
constant from the same file, and records any other name as a `method_const` ref that
queries match against `const` symbols (constants exported by another file).
"""

from tree_sitter import Node

from frappe_ast.js import frappe_patterns as fp
from frappe_ast.js import nodes as n


def collect(root: Node) -> dict[str, tuple[str, Node]]:
	"""Name (`URL`, `API.get`) -> (method path, node) for every constant in the file."""
	found: dict[str, tuple[str, Node]] = {}
	stack = [root]
	while stack:
		node = stack.pop()
		if node.type == "lexical_declaration" and n.text(node).startswith("const"):
			for declarator in (c for c in node.named_children if c.type == "variable_declarator"):
				_add(found, declarator)
		stack.extend(node.named_children)
	return found


def reference(node: Node | None) -> str:
	"""`URL` or `API.get` when `node` names a constant, else ""."""
	if node is None or node.type not in ("identifier", "member_expression"):
		return ""
	name = n.compact(n.text(node)).removesuffix("?")
	parts = name.replace("?.", ".").split(".")
	if parts[0] in fp.DYNAMIC_ROOTS or len(parts) > 2 or not all(p.isidentifier() for p in parts):
		return ""
	return ".".join(parts)


def _add(found: dict, declarator: Node):
	name, value = declarator.child_by_field_name("name"), declarator.child_by_field_name("value")
	if name is None or name.type != "identifier" or value is None:
		return
	if value.type in ("as_expression", "satisfies_expression") and value.named_children:
		value = value.named_children[0]  # `{...} as const`
	method = n.string(value)
	if method and fp.looks_like_method(method):
		found[n.text(name)] = (fp.normalize_method(method), declarator)
	elif value.type == "object":
		for key, member in n.object_members(value):
			method = n.string(member)
			if method and fp.looks_like_method(method):
				found[f"{n.text(name)}.{key}"] = (fp.normalize_method(method), member)
