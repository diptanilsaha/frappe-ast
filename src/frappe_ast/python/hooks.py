"""Read an app's hooks.py statically, without importing it.

hooks.py often computes keys, e.g. `doc_events = {tuple(period_closing_doctypes): {...}}`.
A small evaluator folds module-level names, tuple()/list() calls, + and
subscript assignment. Anything else becomes an "<expr ...>" leaf.
"""

import ast
from dataclasses import dataclass
from pathlib import Path


@dataclass
class HookEntry:
	app: str
	key: str  # top-level name, e.g. doc_events
	path: tuple[str, ...]  # nested keys, e.g. ("Sales Invoice", "on_submit")
	value: str
	line: int


@dataclass(frozen=True)
class _Leaf:
	value: object
	line: int


class HooksReader:
	def read(self, app_name: str, hooks_py: Path) -> list[HookEntry]:
		if not hooks_py.exists():
			return []
		tree = ast.parse(hooks_py.read_text(), filename=str(hooks_py))
		env = _Evaluator().run(tree)
		entries: list[HookEntry] = []
		for key, value in env.items():
			if not key.startswith("_"):
				self._flatten(app_name, key, (), value, entries)
		return entries

	def _flatten(self, app: str, key: str, path: tuple, value, out: list[HookEntry]):
		if isinstance(value, dict):
			for sub_key, sub_value in value.items():
				keys = sub_key if isinstance(sub_key, tuple) else (sub_key,)
				for k in keys:
					self._flatten(app, key, (*path, str(_unwrap(k))), sub_value, out)
		elif isinstance(value, list | tuple):
			for item in value:
				self._flatten(app, key, path, item, out)
		elif isinstance(value, _Leaf):
			out.append(HookEntry(app, key, path, str(value.value), value.line))


class _Evaluator:
	def __init__(self):
		self.env: dict[str, object] = {}

	def run(self, tree: ast.Module) -> dict[str, object]:
		for stmt in tree.body:
			self._statement(stmt)
		return self.env

	def _statement(self, stmt: ast.stmt):
		if isinstance(stmt, ast.Assign):
			value = self.eval(stmt.value)
			for target in stmt.targets:
				self._assign(target, value)
		elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
			self._assign(stmt.target, self.eval(stmt.value))
		elif isinstance(stmt, ast.AugAssign) and isinstance(stmt.target, ast.Name):
			current = self.env.get(stmt.target.id)
			self.env[stmt.target.id] = _combine(current, self.eval(stmt.value))
		elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
			self._mutating_call(stmt.value)

	def _assign(self, target: ast.expr, value):
		if isinstance(target, ast.Name):
			self.env[target.id] = value
		elif isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
			container = self.env.get(target.value.id)
			if isinstance(container, dict):
				container[_unwrap(self.eval(target.slice))] = value

	def _mutating_call(self, call: ast.Call):
		"""Fold `doc_events.update({...})` and `x.append(...)` / `x.extend(...)`."""
		func = call.func
		if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and call.args):
			return
		container = self.env.get(func.value.id)
		arg = self.eval(call.args[0])
		if func.attr == "update" and isinstance(container, dict) and isinstance(arg, dict):
			container.update(arg)
		elif func.attr == "append" and isinstance(container, list):
			container.append(arg)
		elif func.attr == "extend" and isinstance(container, list) and isinstance(arg, list | tuple):
			container.extend(arg)

	def eval(self, node: ast.expr):
		if isinstance(node, ast.Constant):
			return _Leaf(node.value, node.lineno)
		if isinstance(node, ast.Dict):
			return {
				_hashable(self.eval(k)) if k else "**": self.eval(v) for k, v in zip(node.keys, node.values)
			}
		if isinstance(node, ast.List | ast.Set):
			return [self.eval(e) for e in node.elts]
		if isinstance(node, ast.Tuple):
			return tuple(self.eval(e) for e in node.elts)
		if isinstance(node, ast.Name) and node.id in self.env:
			return self.env[node.id]
		if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
			return _combine(self.eval(node.left), self.eval(node.right))
		if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and len(node.args) == 1:
			if node.func.id in ("tuple", "list", "set", "sorted"):
				inner = self.eval(node.args[0])
				if isinstance(inner, list | tuple):
					return tuple(inner) if node.func.id == "tuple" else list(inner)
		return _Leaf(f"<expr {ast.unparse(node)[:80]}>", node.lineno)


def _combine(left, right):
	if isinstance(left, list | tuple) and isinstance(right, list | tuple):
		return [*left, *right]
	if isinstance(left, dict) and isinstance(right, dict):
		return {**left, **right}
	return right if left is None else left


def _hashable(value):
	if isinstance(value, list):
		return tuple(value)
	return value


def _unwrap(value):
	if isinstance(value, _Leaf):
		return value.value
	if isinstance(value, tuple):
		return tuple(_unwrap(v) for v in value)
	return value
