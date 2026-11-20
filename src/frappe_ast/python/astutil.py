"""Small helpers over the stdlib ast module."""

import ast


def dotted(node: ast.expr) -> str:
	"""`frappe.db.get_value` for an Attribute chain on a Name, else ""."""
	parts = []
	while isinstance(node, ast.Attribute):
		parts.append(node.attr)
		node = node.value
	if isinstance(node, ast.Name):
		parts.append(node.id)
		return ".".join(reversed(parts))
	return ""


def truthy(node: ast.expr) -> bool:
	return isinstance(node, ast.Constant) and bool(node.value)
