"""Symbol rows for a class or def: qualname, signature, first doc line, decorators, whitelisting."""

import ast

from frappe_ast.model import Symbol
from frappe_ast.python.astutil import dotted, literal_text, truthy
from frappe_ast.python.imports import ImportMap


def make_symbol(node, kind: str, qualname: str, parent: str, signature: str, imports: ImportMap) -> Symbol:
	sym = Symbol(
		name=node.name,
		qualname=qualname,
		kind=kind,
		line=node.lineno,
		end_line=node.end_lineno or node.lineno,
		signature=signature,
		parent=parent,
		doc=_first_doc_line(node),
		decorators=[ast.unparse(d) for d in node.decorator_list],
	)
	_apply_whitelist(sym, node.decorator_list, imports)
	return sym


def function_signature(node) -> str:
	text = ast.unparse(node.args)
	returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
	return f"({text}){returns}"


def _apply_whitelist(sym: Symbol, decorators: list[ast.expr], imports: ImportMap):
	for deco in decorators:
		target = deco.func if isinstance(deco, ast.Call) else deco
		name = dotted(target)
		if not name or (imports.resolve(name) or name) != "frappe.whitelist":
			continue
		sym.whitelisted = True
		for kw in deco.keywords if isinstance(deco, ast.Call) else []:
			if kw.arg == "allow_guest":
				sym.allow_guest = truthy(kw.value)
			elif kw.arg == "methods":
				sym.http_methods = literal_text(kw.value)


def _first_doc_line(node) -> str:
	doc = ast.get_docstring(node, clean=True)
	return doc.strip().splitlines()[0][:160] if doc else ""
