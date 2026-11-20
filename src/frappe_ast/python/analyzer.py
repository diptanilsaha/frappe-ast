"""Extract symbols, calls and Frappe references from one Python file."""

import ast
import re

from frappe_ast.model import Call, FileFacts, Ref, Symbol
from frappe_ast.python import custom_fields
from frappe_ast.python.astutil import dotted, truthy
from frappe_ast.python.checks import PythonChecks
from frappe_ast.python.frappe_api import DOCTYPE_APIS, DOTTED_APIS, REALTIME_APIS, doctype_arg, realtime_event

DOTTED_RE = re.compile(r"^[a-z_][a-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*){2,}$")


class PythonAnalyzer:
	def __init__(self, known_apps: set[str]):
		self.known_apps = known_apps

	def analyze(self, path: str, source: str, module: str) -> FileFacts:
		facts = FileFacts(path=path, lang="python", module=module)
		try:
			tree = ast.parse(source, filename=path)
		except SyntaxError as e:
			facts.error = f"SyntaxError: {e.msg} (line {e.lineno})"
			return facts
		imports = ImportMap.from_tree(tree, module, is_package=path.endswith("__init__.py"))
		# Custom field lists are rare; skip the extra tree walk in files that cannot hold one.
		lists = custom_fields.FieldLists(tree if "fieldname" in source else ast.Module(body=[]))
		_ModuleVisitor(facts, imports, self.known_apps, lists).visit(tree)
		facts.symbols.extend(self._aliases(tree, imports, module))
		facts.findings = PythonChecks(imports).run(tree)
		return facts

	def _aliases(self, tree: ast.Module, imports: "ImportMap", module: str) -> list[Symbol]:
		"""Module-level `from x import y` makes `module.y` a valid dotted path (re-export)."""
		aliases = []
		for node in tree.body:
			if (
				isinstance(node, ast.Assign)
				and len(node.targets) == 1
				and isinstance(node.targets[0], ast.Name)
			):
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
						Symbol(
							local, f"{module}.{local}", "alias", node.lineno, node.lineno, signature=target
						)
					)
		return aliases


class ImportMap:
	"""Map local names to absolute dotted paths, from imports and module-level defs."""

	def __init__(self, module: str, is_package: bool = False):
		self.module = module
		self.is_package = is_package
		self.names: dict[str, str] = {}

	@classmethod
	def from_tree(cls, tree: ast.Module, module: str, is_package: bool = False) -> "ImportMap":
		imports = cls(module, is_package)
		for node in ast.walk(tree):
			if isinstance(node, ast.Import):
				for alias in node.names:
					local = alias.asname or alias.name.split(".")[0]
					imports.names[local] = alias.name if alias.asname else local
			elif isinstance(node, ast.ImportFrom):
				base = imports._absolute(node)
				for alias in node.names:
					imports.names[alias.asname or alias.name] = f"{base}.{alias.name}" if base else alias.name
		for node in tree.body:
			if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
				imports.names.setdefault(node.name, f"{module}.{node.name}")
		return imports

	def resolve(self, dotted: str) -> str:
		head, _, rest = dotted.partition(".")
		target = self.names.get(head)
		if not target:
			return ""
		return f"{target}.{rest}" if rest else target

	def _absolute(self, node: ast.ImportFrom) -> str:
		if not node.level:
			return node.module or ""
		parts = self.module.split(".")
		# Level 1 means "my package". An __init__ module is its own package.
		drop = node.level - int(self.is_package)
		base = parts[: len(parts) - drop] if drop else parts
		return ".".join([*base, node.module] if node.module else base)


class _ModuleVisitor(ast.NodeVisitor):
	def __init__(self, facts: FileFacts, imports: ImportMap, known_apps: set[str], lists):
		self.facts = facts
		self.lists = lists
		self.imports = imports
		self.known_apps = known_apps
		self.scope: list[tuple[str, str]] = []  # (kind, name)

	@property
	def qual_scope(self) -> str:
		if not self.scope:
			return "<module>"
		return ".".join([self.facts.module, *(name for _, name in self.scope)])

	def visit_ClassDef(self, node: ast.ClassDef):
		bases = [ast.unparse(b) for b in node.bases]
		self._add_symbol(node, "class", signature=f"({', '.join(bases)})")
		self.scope.append(("class", node.name))
		self.generic_visit(node)
		self.scope.pop()

	def visit_FunctionDef(self, node):
		in_class = bool(self.scope) and self.scope[-1][0] == "class"
		self._add_symbol(node, "method" if in_class else "function", signature=_signature(node))
		self.scope.append(("def", node.name))
		self.generic_visit(node)
		self.scope.pop()

	visit_AsyncFunctionDef = visit_FunctionDef

	def visit_Call(self, node: ast.Call):
		callee = dotted(node.func)
		if callee:
			resolved = self._resolve_callee(callee)
			self.facts.calls.append(Call(self.qual_scope, callee, resolved, node.lineno))
			self._record_frappe_refs(node, resolved or callee)
			self._custom_fields(custom_fields.from_call(node, resolved or callee))
		self.generic_visit(node)

	def visit_Dict(self, node: ast.Dict):
		self._custom_fields(custom_fields.from_dict(node, self.lists))
		self.generic_visit(node)

	def _custom_fields(self, found: list[tuple[str, str, int]]):
		for doctype, field, line in found:
			self.facts.refs.append(Ref("custom_field", field, doctype, line, self.qual_scope, "define"))

	def visit_Constant(self, node: ast.Constant):
		value = node.value
		if isinstance(value, str) and DOTTED_RE.match(value) and value.split(".")[0] in self.known_apps:
			self.facts.refs.append(Ref("dotted", value, "string", node.lineno, self.qual_scope, "call"))

	def _add_symbol(self, node, kind: str, signature: str):
		decorators = [ast.unparse(d) for d in node.decorator_list]
		parent = self.qual_scope if self.scope else ""
		sym = Symbol(
			name=node.name,
			qualname=f"{self.qual_scope if self.scope else self.facts.module}.{node.name}",
			kind=kind,
			line=node.lineno,
			end_line=node.end_lineno or node.lineno,
			signature=signature,
			parent=parent,
			doc=_first_doc_line(node),
			decorators=decorators,
		)
		self._apply_whitelist(sym, node.decorator_list)
		self.facts.symbols.append(sym)

	def _apply_whitelist(self, sym: Symbol, decorators: list[ast.expr]):
		for deco in decorators:
			target = deco.func if isinstance(deco, ast.Call) else deco
			name = dotted(target)
			if not name or (self.imports.resolve(name) or name) != "frappe.whitelist":
				continue
			sym.whitelisted = True
			for kw in deco.keywords if isinstance(deco, ast.Call) else []:
				if kw.arg == "allow_guest":
					sym.allow_guest = truthy(kw.value)
				elif kw.arg == "methods":
					sym.http_methods = _literal_text(kw.value)

	def _resolve_callee(self, callee: str) -> str:
		head, _, rest = callee.partition(".")
		if head in ("self", "cls") and rest:
			cls = next((name for kind, name in reversed(self.scope) if kind == "class"), None)
			return f"{self.facts.module}.{cls}.{rest}" if cls else ""
		return self.imports.resolve(callee)

	def _record_frappe_refs(self, node: ast.Call, api: str):
		if api in DOCTYPE_APIS:
			doctype = doctype_arg(node, api)
			if doctype:
				access = DOCTYPE_APIS[api]
				self.facts.refs.append(Ref("doctype", doctype, api, node.lineno, self.qual_scope, access))
		if api in REALTIME_APIS and realtime_event(node):
			self.facts.refs.append(
				Ref("realtime", realtime_event(node), api, node.lineno, self.qual_scope, "publish")
			)
		if api in DOTTED_APIS and node.args and isinstance(node.args[0], ast.Constant):
			target = node.args[0].value
			if isinstance(target, str):
				line = node.args[0].lineno
				self.facts.refs.append(Ref("method", target, api, line, self.qual_scope, "call"))


def _signature(node) -> str:
	text = ast.unparse(node.args)
	returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
	return f"({text}){returns}"


def _first_doc_line(node) -> str:
	doc = ast.get_docstring(node, clean=True)
	return doc.strip().splitlines()[0][:160] if doc else ""


def _literal_text(node: ast.expr) -> str:
	try:
		value = ast.literal_eval(node)
	except ValueError:
		return ast.unparse(node)
	return ",".join(value) if isinstance(value, list | tuple) else str(value)
