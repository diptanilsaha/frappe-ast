"""Monkey patches: code that replaces a function or method of an app at run time.

`ac.get_payment_term_details = our_get_payment_term_details` (erpnext_france, with
`import erpnext.controllers.accounts_controller as ac`), `Document.run_method = patched`, and
`setattr(Database, "sql", traced_sql)`, and a wrapper the patching function defines itself
(`def run_method(...)` then `Document.run_method = run_method`, in frappe/recorder.py). The ref is `monkey_patch`: target is the patched
dotted path, via is the replacement. Only replacements that resolve to a name (an import or a
module-level def) or a lambda count, so restoring a saved original (`ac.x = original`) does not.
"""

import ast

from frappe_ast.model import Ref
from frappe_ast.python.astutil import dotted


class PatchRefs:
	def __init__(self, imports, known_apps: set[str]):
		self.imports = imports  # analyzer.ImportMap
		self.known_apps = known_apps

	def from_assign(self, node: ast.Assign, scope: str, local_defs: set[str]) -> list[Ref]:
		refs = []
		for target in node.targets:
			if isinstance(target, ast.Attribute):
				refs += self._ref(target.value, target.attr, node.value, node.lineno, scope, local_defs)
		return refs

	def from_setattr(self, node: ast.Call, scope: str, local_defs: set[str]) -> list[Ref]:
		if len(node.args) != 3 or not isinstance(node.args[1], ast.Constant):
			return []
		attr = node.args[1].value
		return (
			self._ref(node.args[0], attr, node.args[2], node.lineno, scope, local_defs)
			if isinstance(attr, str)
			else []
		)

	def _ref(
		self, owner, attr: str, value: ast.expr, line: int, scope: str, local_defs: set[str]
	) -> list[Ref]:
		name = dotted(owner)
		if not name or name.split(".")[0] in ("self", "cls"):
			return []
		resolved = self.imports.resolve(name)
		if not resolved or resolved.split(".")[0] not in self.known_apps:
			return []
		local = isinstance(value, ast.Name) and value.id in local_defs
		replacement = f"{scope}.{value.id}" if local else self._replacement(value)
		if not replacement:
			return []
		return [Ref("monkey_patch", f"{resolved}.{attr}", replacement, line, scope, "patch")]

	def _replacement(self, value: ast.expr) -> str:
		if isinstance(value, ast.Lambda):
			return "<lambda>"
		name = dotted(value) if isinstance(value, (ast.Name, ast.Attribute)) else ""
		resolved = self.imports.resolve(name) if name else ""
		return resolved if "." in resolved else ""
