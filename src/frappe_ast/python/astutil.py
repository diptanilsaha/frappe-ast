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


def callee_text(node: ast.Call) -> str:
	"""The callee as the analyzer writes it: `a.b`, or `a().b` for a method on the result of a call
	to `a`, however long the chain between them (`qb.from_(t).where(c).limit(n)` is
	`qb.from_().limit`). "" when no call in the chain has a dotted name."""
	callee = dotted(node.func)
	if callee or not isinstance(node.func, ast.Attribute):
		return callee
	base = node.func.value
	while True:
		if isinstance(base, ast.Call):
			inner = dotted(base.func)
			if inner:
				return f"{inner}().{node.func.attr}"
			base = base.func
		elif isinstance(base, ast.Attribute | ast.Subscript):
			base = base.value
		else:
			return ""


def call_position(node: ast.Call) -> tuple[int, int]:
	"""(line, byte offset) of the name a call calls: `limit` in `q.where(x).limit(n)`, so each call of
	a chain has its own position, on the line that shows it."""
	func = node.func
	if isinstance(func, ast.Attribute) and func.end_lineno is not None:
		return func.end_lineno, func.end_col_offset - len(func.attr.encode())
	return node.lineno, node.col_offset


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
