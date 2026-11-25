"""Module-level names that other modules can import: re-exports, bound callables, typed attributes."""

import ast

from frappe_ast.model import Symbol
from frappe_ast.python.astutil import dotted
from frappe_ast.python.imports import ImportMap, annotation_types


def module_aliases(tree: ast.Module, imports: ImportMap, module: str) -> list[Symbol]:
	"""Module-level `from x import y` makes `module.y` a valid dotted path (re-export)."""
	aliases = []
	for node in tree.body:
		if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
			types = annotation_types(node.annotation, imports, module)
			if types:
				name = node.target.id
				aliases.append(
					Symbol(
						name,
						f"{module}.{name}",
						"variable",
						node.lineno,
						node.lineno,
						signature="|".join(types),
					)
				)
			continue
		if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
			# `execute = Report.execute`: a module-level name bound to another callable.
			value = dotted(node.value) if isinstance(node.value, (ast.Name, ast.Attribute)) else ""
			if value:
				target = imports.resolve(value) or f"{module}.{value}"
				name = node.targets[0].id
				aliases.append(
					Symbol(name, f"{module}.{name}", "alias", node.lineno, node.lineno, signature=target)
				)
			continue
		if not isinstance(node, ast.ImportFrom):
			continue
		for alias in node.names:
			local = alias.asname or alias.name
			if alias.name != "*":
				target = imports.names.get(local, "")
				aliases.append(
					Symbol(local, f"{module}.{local}", "alias", node.lineno, node.lineno, signature=target)
				)
	return aliases
