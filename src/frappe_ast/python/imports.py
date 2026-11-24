"""Local names to absolute dotted paths: imports, module-level defs and typed module attributes."""

import ast
import re

from frappe_ast.python.astutil import dotted


class ImportMap:
	"""Map local names to absolute dotted paths, from imports and module-level defs."""

	def __init__(self, module: str, is_package: bool = False):
		self.module = module
		self.is_package = is_package
		self.names: dict[str, str] = {}

	@classmethod
	def from_tree(cls, tree: ast.Module, module: str, is_package: bool = False) -> "ImportMap":
		imports = cls(module, is_package)
		# Module-level imports win: a class's `if TYPE_CHECKING:` block (in every generated DocType
		# controller) can import a child-table class that has the same name as a module-level base.
		nested = [n for n in ast.walk(tree) if isinstance(n, ast.Import | ast.ImportFrom)]
		for node in [*_module_level_imports(tree.body), *nested]:
			for local, target in imports._bindings(node):
				imports.names.setdefault(local, target)
		for node in tree.body:
			if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
				imports.names.setdefault(node.name, f"{module}.{node.name}")
			elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
				# `qb: LocalProxy["MariaDB"] = local("qb")`: a typed module attribute (see _aliases)
				imports.names.setdefault(node.target.id, f"{module}.{node.target.id}")
		return imports

	def _bindings(self, node: ast.Import | ast.ImportFrom):
		if isinstance(node, ast.Import):
			for alias in node.names:
				local = alias.asname or alias.name.split(".")[0]
				yield local, alias.name if alias.asname else local
			return
		base = self.absolute(node)
		for alias in node.names:
			yield alias.asname or alias.name, f"{base}.{alias.name}" if base else alias.name

	def resolve(self, dotted: str) -> str:
		head, _, rest = dotted.partition(".")
		target = self.names.get(head)
		if not target:
			return ""
		return f"{target}.{rest}" if rest else target

	def absolute(self, node: ast.ImportFrom) -> str:
		if not node.level:
			return node.module or ""
		parts = self.module.split(".")
		# Level 1 means "my package". An __init__ module is its own package.
		drop = node.level - int(self.is_package)
		base = parts[: len(parts) - drop] if drop else parts
		return ".".join([*base, node.module] if node.module else base)


# Wrappers around the real type in an annotation: `LocalProxy["MariaDB"]`, `Optional[X]`.
_ANNOTATION_WRAPPERS = {"LocalProxy", "Optional", "Union", "None", "Any", "type", "Type", "ClassVar", "Final"}


def annotation_types(node: ast.expr, imports: "ImportMap", module: str) -> list[str]:
	"""The class names an annotation names, resolved: `LocalProxy["MariaDB" | "Postgres"]` gives
	both builder classes. String parts are read as the names they spell."""
	names = []
	for sub in ast.walk(node):
		if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
			names += [n.strip().strip("\"'") for n in re.split(r"[|,\[\]]", sub.value)]
		elif isinstance(sub, ast.Name | ast.Attribute) and not isinstance(
			getattr(sub, "ctx", None), ast.Store
		):
			names.append(dotted(sub))
	out = []
	for name in names:
		if not name or name.split(".")[-1] in _ANNOTATION_WRAPPERS:
			continue
		resolved = imports.resolve(name)  # imported (TYPE_CHECKING too) or defined in this module
		if resolved and resolved not in out:
			out.append(resolved)
	return out


def _module_level_imports(body: list[ast.stmt]):
	"""Imports that run at import time: top level, and in module-level if / try blocks."""
	for node in body:
		if isinstance(node, ast.Import | ast.ImportFrom):
			yield node
		elif isinstance(node, ast.If | ast.Try):
			for block in (
				node.body,
				node.orelse,
				getattr(node, "finalbody", []),
				*[h.body for h in getattr(node, "handlers", [])],
			):
				yield from _module_level_imports(block)
