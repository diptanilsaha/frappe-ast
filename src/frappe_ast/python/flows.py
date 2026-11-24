"""Argument flow inside one function: which call arguments carry a parameter or request input.

A pass over the function's statements in order, keeping what each local name holds:
`x = frappe.form_dict.get("name")` makes `x` hold `form_dict:name`, and `frappe.get_doc(dt, x)`
then records that source on argument 1. A value read through an attribute, a subscript or `.get()`
stays direct; anything else between the source and the argument is its `via`, outermost first:
`f-string,frappe.utils.cint` for `f"... {cint(x)}"`. A step is a resolved call, `.<method>` on a
value, `f-string`, `format` (`%`, `+`) or `container` (a list, dict or comprehension). Assignments inside a branch or a loop
add to what a name held before, since either path may run; loop bodies run twice so a value
assigned late in the body reaches calls early in it. Nested functions get their own pass.
"""

import ast
from collections.abc import Callable

from frappe_ast.model import Flow
from frappe_ast.python.astutil import dotted
from frappe_ast.python.frappe_api import REQUEST_SOURCE_CALLS, REQUEST_SOURCES

Sources = dict[str, str]  # source -> via ("" when direct)

# Builtins that inspect a value without passing it on: a flow into them says nothing.
_INSPECT = {"isinstance", "issubclass", "len", "hasattr", "type", "callable", "id", "bool", "print"}

# Reading a part of a value: `x.get("k")` is `x["k"]`.
_GETTERS = {"get", "getlist", "pop"}


class FunctionFlows:
	def __init__(self, node: ast.FunctionDef, qualname: str, resolve: Callable[[str], str], method: bool):
		self.qualname = qualname
		self.resolve = resolve
		args = node.args
		names = [a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]]
		if method and names and not _is_static(node):
			names = names[1:]  # self / cls
		names += [a.arg for a in (args.vararg, args.kwarg) if a]
		self.env: dict[str, Sources] = {n: {f"param:{n}": ""} for n in names}
		self.body = node.body
		self.found: dict[tuple, Flow] = {}

	def run(self) -> list[Flow]:
		self._block(self.body, strong=True)
		return list(self.found.values())

	# ---- statements ------------------------------------------------------------------------

	def _block(self, body: list[ast.stmt], strong: bool):
		for stmt in body:
			self._stmt(stmt, strong)

	def _stmt(self, stmt: ast.stmt, strong: bool):
		if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
			return
		if isinstance(stmt, ast.Assign):
			self._calls(stmt.value)
			value = self.sources(stmt.value)
			for target in stmt.targets:
				self._assign(target, value, strong)
		elif isinstance(stmt, ast.AnnAssign | ast.AugAssign):
			if stmt.value is not None:
				self._calls(stmt.value)
				self._assign(
					stmt.target, self.sources(stmt.value), strong and isinstance(stmt, ast.AnnAssign)
				)
		elif isinstance(stmt, ast.For | ast.AsyncFor):
			self._calls(stmt.iter)
			for _ in range(2):
				self._assign(stmt.target, self.sources(stmt.iter), False)
				self._block(stmt.body, False)
			self._block(stmt.orelse, False)
		elif isinstance(stmt, ast.While):
			for _ in range(2):
				self._calls(stmt.test)
				self._block(stmt.body, False)
			self._block(stmt.orelse, False)
		elif isinstance(stmt, ast.If):
			self._calls(stmt.test)
			self._block(stmt.body, False)
			self._block(stmt.orelse, False)
		elif isinstance(stmt, ast.With | ast.AsyncWith):
			for item in stmt.items:
				self._calls(item.context_expr)
				if item.optional_vars is not None:
					self._assign(item.optional_vars, self.sources(item.context_expr), strong)
			self._block(stmt.body, strong)
		elif isinstance(stmt, ast.Try) or type(stmt).__name__ == "TryStar":
			self._block(stmt.body, False)
			for handler in stmt.handlers:
				self._block(handler.body, False)
			self._block(stmt.orelse, False)
			self._block(stmt.finalbody, strong)
		elif type(stmt).__name__ == "Match":
			self._calls(stmt.subject)
			for case in stmt.cases:
				self._block(case.body, False)
		else:  # Expr, Return, Raise, Assert, Delete, ...
			for child in ast.iter_child_nodes(stmt):
				if isinstance(child, ast.expr):
					self._calls(child)

	def _assign(self, target: ast.expr, value: Sources, strong: bool):
		if isinstance(target, ast.Name):
			merged = dict(value) if strong else {**value, **self.env.get(target.id, {})}
			if merged:
				self.env[target.id] = merged
			else:
				self.env.pop(target.id, None)
		elif isinstance(target, ast.Tuple | ast.List):
			for element in target.elts:
				self._assign(element.value if isinstance(element, ast.Starred) else element, value, strong)

	# ---- calls -----------------------------------------------------------------------------

	def _calls(self, expr: ast.expr):
		"""Record the flows into every call in an expression, outside nested lambdas."""
		stack = [expr]
		while stack:
			node = stack.pop()
			if isinstance(node, ast.Lambda):
				continue
			if isinstance(node, ast.Call):
				self._call(node)
			stack.extend(ast.iter_child_nodes(node))

	def _call(self, node: ast.Call):
		callee = _callee(node)
		if not callee or callee in _INSPECT:
			return
		resolved = "" if "()" in callee else self.resolve(callee)
		args = [("*" if isinstance(a, ast.Starred) else str(i), a) for i, a in enumerate(node.args)]
		args += [(kw.arg or "**", kw.value) for kw in node.keywords]
		for name, value in args:
			for source, via in self.sources(value).items():
				key = (node.lineno, callee, name, source)
				if key not in self.found or (via == "" and self.found[key].via):
					self.found[key] = Flow(self.qualname, callee, resolved, node.lineno, name, source, via)

	# ---- expressions -----------------------------------------------------------------------

	def sources(self, node: ast.expr | None) -> Sources:
		"""Where an expression's value comes from, as far as this function says."""
		if node is None:
			return {}
		if isinstance(node, ast.Name):
			request = self._request(node.id)
			return {request: ""} if request else dict(self.env.get(node.id, {}))
		if isinstance(node, ast.Attribute):
			request = self._request(dotted(node))
			return {request: ""} if request else self.sources(node.value)
		if isinstance(node, ast.Subscript):
			return _keyed(self.sources(node.value), node.slice)
		if isinstance(node, ast.Call):
			return self._call_sources(node)
		if isinstance(node, ast.Starred | ast.Await | ast.NamedExpr | ast.FormattedValue):
			return self.sources(node.value)
		if isinstance(node, ast.IfExp):
			return _union(self.sources(node.body), self.sources(node.orelse))
		if isinstance(node, ast.BoolOp):
			return _union(*map(self.sources, node.values))
		if isinstance(node, ast.JoinedStr):
			return _through(_union(*map(self.sources, node.values)), "f-string")
		if isinstance(node, ast.BinOp):
			return _through(_union(self.sources(node.left), self.sources(node.right)), "format")
		if isinstance(node, ast.List | ast.Tuple | ast.Set):
			return _through(_union(*map(self.sources, node.elts)), "container")
		if isinstance(node, ast.Dict):
			return _through(_union(*map(self.sources, node.values)), "container")
		if isinstance(node, ast.ListComp | ast.SetComp | ast.GeneratorExp | ast.DictComp):
			return _through(_union(*(self.sources(g.iter) for g in node.generators)), "container")
		return {}

	def _call_sources(self, node: ast.Call) -> Sources:
		callee = _callee(node)
		resolved = self.resolve(callee) if callee and "()" not in callee else ""
		if isinstance(node.func, ast.Name):
			resolved = resolved or callee  # a builtin: int(), str()
		if resolved in REQUEST_SOURCE_CALLS:
			label = REQUEST_SOURCE_CALLS[resolved]
			return _keyed({label: ""}, node.args[0]) if node.args else {label: ""}
		func = node.func
		if isinstance(func, ast.Attribute) and func.attr in _GETTERS:
			receiver = self.sources(func.value)
			if receiver:
				return _keyed(receiver, node.args[0]) if node.args else receiver
		found = _union(*(self.sources(a) for a in node.args), *(self.sources(k.value) for k in node.keywords))
		receiver = self.sources(func.value) if isinstance(func, ast.Attribute) else {}
		if receiver:  # name.strip(), doc.as_dict(): a method on the value itself
			return _union(_through(found, resolved or "call"), _through(receiver, f".{func.attr}"))
		return _through(found, resolved or "call")

	def _request(self, text: str) -> str:
		absolute = self.resolve(text) or text
		for prefix, label in REQUEST_SOURCES.items():
			if absolute == prefix:
				return label
			if absolute.startswith(prefix + "."):
				part = absolute[len(prefix) + 1 :].split(".")[0]
				return f"{label}:{part}" if label == "form_dict" else f"{label}.{part}"
		return ""


def _callee(node: ast.Call) -> str:
	"""The callee as the analyzer writes it: `a.b`, or `a().b` for a method on a call's result."""
	callee = dotted(node.func)
	if not callee and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Call):
		inner = dotted(node.func.value.func)
		callee = f"{inner}().{node.func.attr}" if inner else ""
	return callee


def _keyed(found: Sources, key: ast.expr) -> Sources:
	"""`form_dict["name"]`: a request source without a key gains it; anything else passes through."""
	if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
		return found
	return {
		(f"{s}:{key.value}" if not s.startswith("param:") and ":" not in s else s): via
		for s, via in found.items()
	}


def _union(*parts: Sources) -> Sources:
	out: Sources = {}
	for part in parts:
		for source, via in part.items():
			if source not in out or via == "":
				out[source] = via
	return out


def _through(found: Sources, via: str) -> Sources:
	"""The same sources, now behind one more step (a step repeated in a row is kept once)."""
	return {s: v if v.split(",")[0] == via else (f"{via},{v}" if v else via) for s, v in found.items()}


def _is_static(node: ast.FunctionDef) -> bool:
	return any(dotted(d) == "staticmethod" for d in node.decorator_list)
