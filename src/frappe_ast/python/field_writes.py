"""Writes to a document's fields that name the field as a string, not as an attribute.

`super().__init__({"limit": 50})` in a virtual DocType's `load_from_db`, `self.update(d)`,
`doc.db_set("status", s)` and `frappe.get_doc({"doctype": "ToDo", "owner": u})` set fields by
name (the calls are rows in `frappe_api.py`). Each name becomes an `attribute` ref with access
`set`, on `self:<class>` or `doctype:<DocType>`: not `write`, since Document.set stores into
`__dict__` and runs no `@property` setter. The dict is a literal at the call, or a local name
bound to one earlier in the same function (`d = frappe._dict({...})`).
"""

import ast
from collections.abc import Callable

from frappe_ast.python.astutil import dotted
from frappe_ast.python.frappe_api import (
	DICT_BUILDERS,
	FIELD_DICT_APIS,
	FIELD_DICT_METHODS,
	FIELD_NAME_METHODS,
	dict_doctype,
)

Write = tuple[str, ast.expr, str]  # field, the node that names it, receiver


class FieldWrites:
	def __init__(self, resolve: Callable[[str], str]):
		self.resolve = resolve
		self.dicts: list[dict[str, ast.Dict]] = [{}]  # per def scope: name -> the dict literal it holds

	def enter(self):
		self.dicts.append({})

	def leave(self):
		self.dicts.pop()

	def bind(self, target: ast.expr, value: ast.expr):
		if isinstance(target, ast.Name):
			found = self._literal(value)
			if found:
				self.dicts[-1][target.id] = found
			else:
				self.dicts[-1].pop(target.id, None)

	def writes(self, node: ast.Call, api: str, receiver: str, cls: str) -> list[Write]:
		"""The fields a call sets. `receiver` is the call's (`Call.receiver`), `cls` the enclosing class."""
		first = node.args[0] if node.args else None
		if api in FIELD_DICT_APIS:
			found = self._dict(first)
			doctype = dict_doctype(found) if found else ""
			return [w for w in _keys(found, f"doctype:{doctype}") if w[0] != "doctype"] if doctype else []
		target = self._target(node, receiver, cls)
		method = node.func.attr if isinstance(node.func, ast.Attribute) else ""
		if not target:
			return []
		if method in FIELD_NAME_METHODS and isinstance(first, ast.Constant) and isinstance(first.value, str):
			return [(first.value, first, target)]
		return _keys(self._dict(first), target) if method in FIELD_DICT_METHODS else []

	def _target(self, node: ast.Call, receiver: str, cls: str) -> str:
		"""The document a method call is on: `self.m()` and `super().m()` are on the instance."""
		func = node.func
		on_self = (
			isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "self"
		)
		if (on_self or receiver.startswith("super:")) and cls:
			return f"self:{cls}"
		return receiver if receiver.startswith("doctype:") and receiver != "doctype:?" else ""

	def _dict(self, node: ast.expr | None) -> ast.Dict | None:
		if isinstance(node, ast.Name):
			return self.dicts[-1].get(node.id)
		return self._literal(node)

	def _literal(self, node: ast.expr | None) -> ast.Dict | None:
		if isinstance(node, ast.Dict):
			return node
		if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Dict):
			callee = dotted(node.func)
			if (self.resolve(callee) or callee) in DICT_BUILDERS:
				return node.args[0]
		return None


def _keys(found: ast.Dict | None, target: str) -> list[Write]:
	if found is None:
		return []
	return [
		(key.value, key, target)
		for key in found.keys
		if isinstance(key, ast.Constant) and isinstance(key.value, str) and key.value.isidentifier()
	]
