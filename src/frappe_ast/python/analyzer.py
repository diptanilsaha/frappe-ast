"""Extract symbols, calls and Frappe references from one Python file."""

import ast

from frappe_ast.model import Call, FileFacts, Ref
from frappe_ast.python import compat, custom_fields
from frappe_ast.python.aliases import module_aliases
from frappe_ast.python.astutil import dotted, short_literal
from frappe_ast.python.checks import PythonChecks
from frappe_ast.python.flows import FunctionFlows
from frappe_ast.python.frappe_api import (
	API_URL_RE,
	DOC_LOADER_APIS,
	DOCTYPE_APIS,
	DOTTED_APIS,
	DOTTED_RE,
	REALTIME_APIS,
	doctype_arg,
	realtime_event,
)
from frappe_ast.python.imports import ImportMap
from frappe_ast.python.patches import PatchRefs
from frappe_ast.python.symbols import function_signature, make_symbol


class PythonAnalyzer:
	def __init__(self, known_apps: set[str]):
		self.known_apps = known_apps

	def analyze(self, path: str, source: str, module: str) -> FileFacts:
		facts = FileFacts(path=path, lang="python", module=module)
		try:
			tree = compat.parse(source, filename=path)
		except SyntaxError as e:
			facts.error = f"SyntaxError: {e.msg} (line {e.lineno})"
			return facts
		imports = ImportMap.from_tree(tree, module, is_package=path.endswith("__init__.py"))
		# Custom field lists are rare; skip the extra tree walk in files that cannot hold one.
		lists = custom_fields.FieldLists(tree if "fieldname" in source else ast.Module(body=[]))
		_ModuleVisitor(facts, imports, self.known_apps, lists).visit(tree)
		facts.symbols.extend(module_aliases(tree, imports, module))
		facts.findings = PythonChecks(imports).run(tree)
		return facts


class _ModuleVisitor(ast.NodeVisitor):
	def __init__(self, facts: FileFacts, imports: ImportMap, known_apps: set[str], lists):
		self.facts = facts
		self.lists = lists
		self.imports = imports
		self.known_apps = known_apps
		self.patches = PatchRefs(imports, known_apps)
		self.scope: list[tuple[str, str]] = []  # (kind, name)
		self.local_defs: list[set[str]] = [set()]  # functions each enclosing def defines directly
		# what each name is bound to, per def scope (module first): see Call.receiver
		self.bindings: list[dict[str, str]] = [{}]
		self.attributes: set[tuple[str, str, str, str]] = set()  # attribute refs already recorded

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
		self._add_symbol(node, "method" if in_class else "function", signature=function_signature(node))
		self.scope.append(("def", node.name))
		self.local_defs.append(
			{c.name for c in node.body if isinstance(c, ast.FunctionDef | ast.AsyncFunctionDef)}
		)
		self.bindings.append({})
		self.facts.flows += FunctionFlows(node, self.qual_scope, self._resolve_callee, in_class).run()
		self.generic_visit(node)
		self.bindings.pop()
		self.local_defs.pop()
		self.scope.pop()

	visit_AsyncFunctionDef = visit_FunctionDef

	def visit_Call(self, node: ast.Call):
		callee = dotted(node.func)
		receiver = ""
		if isinstance(node.func, ast.Attribute):
			base = node.func.value
			if isinstance(base, ast.Name):
				receiver = self._binding(base.id)
			elif isinstance(base, ast.Call):
				# `Engine().run()`, `frappe.get_doc("Item", n).save()`: a method on a call's result
				receiver = self._value_type(base)
				inner = dotted(base.func)
				callee = callee or (f"{inner}().{node.func.attr}" if inner else "")
		if callee:
			resolved = "" if "()" in callee else self._resolve_callee(callee)
			arg = short_literal(node.args[0] if node.args else None)
			self.facts.calls.append(Call(self.qual_scope, callee, resolved, node.lineno, receiver, arg))
			self._record_frappe_refs(node, resolved or callee)
			for value in [*node.args, *(kw.value for kw in node.keywords)]:
				self._callback(value, resolved or callee)
			self._custom_fields(custom_fields.from_call(node, resolved or callee))
			if callee == "setattr":
				self.facts.refs += self.patches.from_setattr(node, self.qual_scope, self.local_defs[-1])
		# The called attribute is a call, not an attribute read: visit only what it is taken from.
		func = node.func
		for child in [func.value if isinstance(func, ast.Attribute) else func, *node.args, *node.keywords]:
			self.visit(child)

	def visit_With(self, node: ast.With | ast.AsyncWith):
		enter, exit = (
			("__aenter__", "__aexit__") if isinstance(node, ast.AsyncWith) else ("__enter__", "__exit__")
		)
		for item in node.items:
			expr = item.context_expr
			receiver = self._value_type(expr)
			inner = dotted(expr.func) if isinstance(expr, ast.Call) else dotted(expr)
			if not receiver.startswith("call:") or not inner:
				continue
			text = f"{inner}()" if isinstance(expr, ast.Call) else inner
			for method in (enter, exit):
				callee = f"{text}.{method}"
				resolved = "" if "()" in callee else self._resolve_callee(callee)
				self.facts.calls.append(Call(self.qual_scope, callee, resolved, node.lineno, receiver))
		self.generic_visit(node)

	visit_AsyncWith = visit_With

	def visit_Attribute(self, node: ast.Attribute):
		"""An attribute read or written on a receiver of known type: a @property runs code there."""
		base = node.value
		if isinstance(base, ast.Name) and base.id == "self":
			cls = self._class_qualname()
			via = f"self:{cls}" if cls else ""
		else:
			via = self._value_type(base)
		if via and via != "doctype:?" and not isinstance(node.ctx, ast.Del):
			access = "read" if isinstance(node.ctx, ast.Load) else "write"
			key = (self.qual_scope, node.attr, via, access)
			if key not in self.attributes:  # the first use in a function is enough for an edge
				self.attributes.add(key)
				self.facts.refs.append(Ref("attribute", node.attr, via, node.lineno, self.qual_scope, access))
		self.generic_visit(node)

	def visit_Assign(self, node: ast.Assign):
		self.facts.refs += self.patches.from_assign(node, self.qual_scope, self.local_defs[-1])
		self.generic_visit(node)
		value = self._value_type(node.value)
		for target in node.targets:
			if isinstance(target, ast.Name):
				self._bind(target.id, value)

	def visit_AnnAssign(self, node: ast.AnnAssign):
		self.generic_visit(node)
		if isinstance(node.target, ast.Name) and node.value is not None:
			self._bind(node.target.id, self._value_type(node.value))

	def _bind(self, name: str, value: str):
		"""Rebinding a name to something unknown forgets what it was."""
		if value:
			self.bindings[-1][name] = value
		else:
			self.bindings[-1].pop(name, None)

	def _binding(self, name: str) -> str:
		for scope in reversed(self.bindings):
			if name in scope:
				return scope[name]
		return ""

	def _value_type(self, node: ast.expr) -> str:
		"""What an expression evaluates to, as far as this file says (see Call.receiver)."""
		if isinstance(node, ast.Name):
			return self._binding(node.id)
		if not isinstance(node, ast.Call):
			return ""
		callee = dotted(node.func)
		if callee == "super" and "super" not in self.imports.names:
			return self._super_receiver(node)
		resolved = self._resolve_callee(callee) if callee else ""
		if (resolved or callee) in DOC_LOADER_APIS:
			return f"doctype:{doctype_arg(node, resolved or callee) or '?'}"
		return f"call:{resolved}" if resolved else ""

	def _super_receiver(self, node: ast.Call) -> str:
		"""`super()` in a method of C, or `super(C, self)`: "super:<C>"."""
		named = dotted(node.args[0]) if node.args else ""
		cls = (
			(self._resolve_callee(named) or f"{self.facts.module}.{named}")
			if named
			else self._class_qualname()
		)
		return f"super:{cls}" if cls else ""

	def _class_qualname(self) -> str:
		"""The innermost enclosing class, by its qualname."""
		names = [name for _, name in self.scope]
		for i in range(len(self.scope) - 1, -1, -1):
			if self.scope[i][0] == "class":
				return ".".join([self.facts.module, *names[: i + 1]])
		return ""

	def visit_Import(self, node: ast.Import):
		for alias in node.names:
			self._import_ref("import", alias.name, "import", node)

	def visit_ImportFrom(self, node: ast.ImportFrom):
		base = self.imports.absolute(node)
		self._import_ref("import", base, "import", node)
		for alias in node.names:
			if alias.name != "*":
				# target = absolute name, via = module, the same shape as JS import_name refs.
				self._import_ref("import_name", f"{base}.{alias.name}" if base else alias.name, base, node)

	def _import_ref(self, kind: str, target: str, via: str, node: ast.stmt):
		self.facts.refs.append(Ref(kind, target, via, node.lineno, self.qual_scope, "import"))

	def visit_Dict(self, node: ast.Dict):
		self._custom_fields(custom_fields.from_dict(node, self.lists))
		for value in node.values:
			self._callback(value, "dict")
		self.generic_visit(node)

	def _callback(self, node: ast.expr, via: str):
		"""A function of a bench app named without being called: passed to a call
		(`frappe.enqueue(fn)`, `Rule(endpoint=fn)`) or stored in a dict (sandbox globals)."""
		if not isinstance(node, ast.Name | ast.Attribute):
			return
		name = dotted(node)
		target = self._resolve_callee(name) if name else ""
		if target and target.split(".")[0] in self.known_apps:
			self.facts.refs.append(Ref("callback", target, via, node.lineno, self.qual_scope, "pass"))

	def _custom_fields(self, found: list[tuple[str, str, int]]):
		for doctype, field, line in found:
			self.facts.refs.append(Ref("custom_field", field, doctype, line, self.qual_scope, "define"))

	def visit_Constant(self, node: ast.Constant):
		value = node.value
		if not isinstance(value, str):
			return
		url = API_URL_RE.search(value) if "method/" in value else None
		method, via = (url.group(1), "url") if url else (value, "string")
		if DOTTED_RE.match(method) and method.split(".")[0] in self.known_apps:
			self.facts.refs.append(Ref("dotted", method, via, node.lineno, self.qual_scope, "call"))

	def _add_symbol(self, node, kind: str, signature: str):
		qualname = f"{self.qual_scope if self.scope else self.facts.module}.{node.name}"
		parent = self.qual_scope if self.scope else ""
		self.facts.symbols.append(make_symbol(node, kind, qualname, parent, signature, self.imports))

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
