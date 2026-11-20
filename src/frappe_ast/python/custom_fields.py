"""Custom fields an app adds to other DocTypes, so field checks do not flag them.

create_custom_fields({"Sales Invoice": [{"fieldname": "x", ...}], ("A", "B"): [...]})
create_custom_fields({"Tag": get_tag_fields()})   # a list built in this module
create_custom_field("User", {"fieldname": "x"})
frappe.get_doc({"doctype": "Custom Field", "dt": "User", "fieldname": "x"})
"""

import ast


class FieldLists:
	"""Field dicts per list variable and per function that returns one, across one module.
	Names merge across functions: an extra match only hides a finding, never adds one."""

	def __init__(self, tree: ast.Module):
		self.by_name: dict[str, list[ast.expr]] = {}
		self.returns: dict[str, list[str]] = {}  # function -> names it returns
		self.direct: dict[str, list[ast.expr]] = {}  # function -> items of a returned list literal
		self.dicts: dict[str, list[ast.expr]] = {}  # `e_waybill_field = {"fieldname": ...}`
		for node in ast.walk(tree):
			self._collect(node)

	def items(self, value: ast.expr, depth: int = 0) -> list[ast.expr]:
		"""Field dicts `value` stands for: a list literal, `[*A, b]`, `A + B`, a list or dict
		variable, f() returning a list, or one field dict."""
		if depth > 6:
			return []
		if isinstance(value, ast.Dict):
			return [value]
		if isinstance(value, (ast.List, ast.Tuple)):
			return [i for e in value.elts for i in self.items(e, depth + 1)]
		if isinstance(value, ast.Starred):
			return self.items(value.value, depth + 1)
		if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add):
			return self.items(value.left, depth + 1) + self.items(value.right, depth + 1)
		if isinstance(value, ast.Name):
			named = self.by_name.get(value.id, []) + self.dicts.get(value.id, [])
			return [i for e in named for i in self.items(e, depth + 1)]
		if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
			func = value.func.id
			named = [item for name in self.returns.get(func, []) for item in self.by_name.get(name, [])]
			return [i for e in named + self.direct.get(func, []) for i in self.items(e, depth + 1)]
		return []

	def _collect(self, node: ast.AST):
		if isinstance(node, ast.Assign) and isinstance(node.value, (ast.List, ast.Tuple, ast.BinOp)):
			for target in (t for t in node.targets if isinstance(t, ast.Name)):
				elts = node.value.elts if not isinstance(node.value, ast.BinOp) else [node.value]
				self.by_name.setdefault(target.id, []).extend(elts)
		elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
			for target in (t for t in node.targets if isinstance(t, ast.Name)):
				self.dicts.setdefault(target.id, []).append(node.value)
		elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args:
			owner = node.func.value
			if isinstance(owner, ast.Name) and node.func.attr in ("append", "extend"):
				added = (
					node.args[0].elts
					if node.func.attr == "extend" and isinstance(node.args[0], ast.List)
					else [node.args[0]]
				)
				self.by_name.setdefault(owner.id, []).extend(added)
		elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
			for ret in (r for r in ast.walk(node) if isinstance(r, ast.Return) and r.value is not None):
				if isinstance(ret.value, ast.Name):
					self.returns.setdefault(node.name, []).append(ret.value.id)
				elif isinstance(ret.value, (ast.List, ast.Tuple)):
					self.direct.setdefault(node.name, []).extend(ret.value.elts)


def from_dict(node: ast.Dict, lists: FieldLists) -> list[tuple[str, str, int]]:
	"""(doctype, fieldname, line) for a dict shaped like one of the forms above."""
	pairs = {_str(k): v for k, v in zip(node.keys, node.values) if _str(k)}
	if _str(pairs.get("doctype")) == "Custom Field":
		doctype, field = _str(pairs.get("dt")), _str(pairs.get("fieldname"))
		return [(doctype, field, node.lineno)] if doctype and field else []
	out = []
	for key, value in zip(node.keys, node.values):
		doctypes = _doctypes(key)
		if not doctypes or isinstance(value, ast.Constant):
			continue
		if isinstance(value, ast.Dict) and _fieldname(value) is None:
			continue  # an ordinary nested dict, not a single field spec
		fields = [f for f in (_fieldname(item) for item in lists.items(value)) if f]
		for doctype in doctypes:
			out += [(doctype, field, line) for field, line in fields]
	return out


def from_call(node: ast.Call, callee: str) -> list[tuple[str, str, int]]:
	if not callee.endswith("create_custom_field") or len(node.args) < 2:
		return []
	doctype, spec = _str(node.args[0]), node.args[1]
	field = _fieldname(spec)
	return [(doctype, field[0], field[1])] if doctype and field else []


def _fieldname(item: ast.expr) -> tuple[str, int] | None:
	if isinstance(item, ast.Dict):
		for key, value in zip(item.keys, item.values):
			if _str(key) == "fieldname" and _str(value):
				return _str(value), value.lineno
	return None


def _doctypes(key: ast.expr | None) -> list[str]:
	if isinstance(key, ast.Tuple):
		return [s for s in (_str(e) for e in key.elts) if s]
	return [_str(key)] if _str(key) else []


def _str(node: ast.expr | None) -> str:
	return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else ""
