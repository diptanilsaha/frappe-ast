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


def short_literal(node) -> str:
	"""A string literal short enough to be a name (DocType, hook key, method), else "".

	A SQL string or a message is not a name, and a lone surrogate cannot be stored."""
	if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
		return ""
	value = node.value
	if len(value) > 140 or "\n" in value:
		return ""
	try:
		value.encode("utf-8")
	except UnicodeEncodeError:
		return ""
	return value


def literal_text(node: ast.expr) -> str:
	try:
		value = ast.literal_eval(node)
	except ValueError:
		return ast.unparse(node)
	return ",".join(value) if isinstance(value, list | tuple) else str(value)
