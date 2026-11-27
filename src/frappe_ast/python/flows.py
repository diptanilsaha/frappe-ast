"""Argument flow inside one function: which call arguments carry a parameter or request input.

A pass over the function's statements in order, keeping what each local name holds:
`x = frappe.form_dict.get("name")` makes `x` hold `form_dict:name`, and `frappe.get_doc(dt, x)`
then records that source on argument 1. A value read through an attribute, a subscript or `.get()`
stays direct; anything else between the source and the argument is its `via`, outermost first:
`f-string,frappe.utils.cint` for `f"... {cint(x)}"`. A step is a resolved call, `.<method>` on a
value, `f-string`, `format` (`%`, `+`) or `container` (a list, dict or comprehension). Assignments
inside a branch or a loop add to what a name held before, since either path may run; loop bodies
run twice so a value assigned late in the body reaches calls early in it. Nested functions get
their own pass.

Three more kinds of row let queries join functions: `<return>` (a value the function returns or
yields), `self.<x>` with arg `=` (a value a method stores on the instance), and the source
`self.<x>` (reading an attribute that some method of the class stores a source on). Filling a
container in place (`x.append(v)`, `x[k] = v`) makes `x` hold what went in.
"""

import ast
from collections.abc import Callable

from frappe_ast.model import Flow
from frappe_ast.python.astutil import callee_text, dotted
from frappe_ast.python.flow_steps import Sources, call_step, keyed, split_via, through, union
from frappe_ast.python.frappe_api import REQUEST_SOURCE_CALLS, REQUEST_SOURCES

# Builtins that inspect a value without passing it on: a flow into them says nothing.
_INSPECT = {"isinstance", "issubclass", "len", "hasattr", "type", "callable", "id", "bool", "print"}

# Reading a part of a value: `x.get("k")` is `x["k"]`.
_GETTERS = {"get", "getlist", "pop"}

# Methods that put their arguments into the value they are called on.
_FILLERS = {"append", "extend", "insert", "add", "update", "setdefault"}

RETURN = "<return>"


class FunctionFlows:
	def __init__(
		self,
		node: ast.FunctionDef,
		qualname: str,
		resolve: Callable[[str], str],
		method: bool,
		position: Callable[[ast.AST], tuple[int, int]],
	):
		self.qualname = qualname
		self.resolve = resolve
		self.position = position  # (line, column) of a fact, as the analyzer records it
		self.method = method
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
		elif isinstance(stmt, ast.Return):
			if stmt.value is not None:
				self._calls(stmt.value)
				self._record(stmt, RETURN, "", "", self.sources(stmt.value))
		else:  # Expr, Raise, Assert, Delete, ...
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
		elif _on_self(target):
			if self.method and value:
				cls = self.qualname.rsplit(".", 1)[0]
				self._record(target, f"self.{target.attr}", f"{cls}.{target.attr}", "=", value)
		elif isinstance(target, ast.Subscript | ast.Attribute):
			self._fill(target.value, value)  # x[k] = v, x.k = v

	def _fill(self, container: ast.expr, value: Sources):
		"""A value put into a container: the local name it hangs off (`x` for `x[k].append(v)`)
		holds it, or, for `self.a[k] = v`, it is a store on `self.a`."""
		node = container
		while isinstance(node, ast.Subscript | ast.Attribute) and not _on_self(node):
			node = node.value
		if not value:
			return
		if _on_self(node) and self.method:
			cls = self.qualname.rsplit(".", 1)[0]
			self._record(node, f"self.{node.attr}", f"{cls}.{node.attr}", "=", through(value, "container"))
		elif isinstance(node, ast.Name) and node.id != "self":
			self.env[node.id] = union(self.env.get(node.id, {}), through(value, "container"))

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
			elif isinstance(node, ast.Yield | ast.YieldFrom) and node.value is not None:
				self._record(node, RETURN, "", "", self.sources(node.value))
			stack.extend(ast.iter_child_nodes(node))

	def _call(self, node: ast.Call):
		func = node.func
		if isinstance(func, ast.Attribute) and func.attr in _FILLERS:  # `x[k].append(v)` has no callee
			for _, value in _arguments(node):
				self._fill(func.value, self.sources(value))
		callee = callee_text(node)
		if not callee or callee in _INSPECT:
			return
		resolved = "" if "()" in callee else self.resolve(callee)
		for name, value in _arguments(node):
			self._record(node, callee, resolved, name, self.sources(value))

	def _record(self, node: ast.AST, callee: str, resolved: str, arg: str, found: Sources):
		for source, chain in found.items():
			via, returns = split_via(chain)
			line, col = self.position(node)
			key = (line, col, callee, arg, source)
			if key not in self.found or (via == "" and self.found[key].via):
				self.found[key] = Flow(self.qualname, callee, resolved, line, arg, source, via, col, returns)

	# ---- expressions -----------------------------------------------------------------------

	def sources(self, node: ast.expr | None) -> Sources:
		"""Where an expression's value comes from, as far as this function says."""
		if node is None:
			return {}
		if isinstance(node, ast.Name):
			request = self._request(node.id)
			return {request: ""} if request else dict(self.env.get(node.id, {}))
		if isinstance(node, ast.Attribute):
			if _on_self(node) and self.method:  # a candidate: see keep_stored
				return {f"self.{node.attr}": ""}
			request = self._request(dotted(node))
			return {request: ""} if request else self.sources(node.value)
		if isinstance(node, ast.Subscript):
			return keyed(self.sources(node.value), node.slice)
		if isinstance(node, ast.Call):
			return self._call_sources(node)
		if isinstance(node, ast.Starred | ast.Await | ast.NamedExpr | ast.FormattedValue):
			return self.sources(node.value)
		if isinstance(node, ast.IfExp):
			return union(self.sources(node.body), self.sources(node.orelse))
		if isinstance(node, ast.BoolOp):
			return union(*map(self.sources, node.values))
		if isinstance(node, ast.JoinedStr):
			return through(union(*map(self.sources, node.values)), "f-string")
		if isinstance(node, ast.BinOp):
			return through(union(self.sources(node.left), self.sources(node.right)), "format")
		if isinstance(node, ast.UnaryOp):
			return self.sources(node.operand)
		if isinstance(node, ast.Compare):  # `field >= x` builds a query criterion that holds x
			return through(union(*map(self.sources, [node.left, *node.comparators])), "compare")
		if isinstance(node, ast.List | ast.Tuple | ast.Set):
			return through(union(*map(self.sources, node.elts)), "container")
		if isinstance(node, ast.Dict):
			return through(union(*map(self.sources, node.values)), "container")
		if isinstance(node, ast.ListComp | ast.SetComp | ast.GeneratorExp | ast.DictComp):
			return through(union(*(self.sources(g.iter) for g in node.generators)), "container")
		return {}

	def _call_sources(self, node: ast.Call) -> Sources:
		callee = callee_text(node)
		resolved = self.resolve(callee) if callee and "()" not in callee else ""
		if isinstance(node.func, ast.Name):
			resolved = resolved or callee  # a builtin: int(), str()
		if resolved in REQUEST_SOURCE_CALLS:
			label = REQUEST_SOURCE_CALLS[resolved]
			return keyed({label: ""}, node.args[0]) if node.args else {label: ""}
		func = node.func
		if isinstance(func, ast.Attribute) and func.attr in _GETTERS:
			receiver = self.sources(func.value)
			if receiver:
				return keyed(receiver, node.args[0]) if node.args else receiver
		step = resolved or "call"
		found = union(
			*(
				through(self.sources(value), call_step(step, *self.position(node), callee, name))
				for name, value in _arguments(node)
			)
		)
		receiver = self.sources(func.value) if isinstance(func, ast.Attribute) else {}
		if receiver:  # name.strip(), doc.as_dict(): a method on the value itself
			return union(found, through(receiver, f".{func.attr}"))
		return found

	def _request(self, text: str) -> str:
		absolute = self.resolve(text) or text
		for prefix, label in REQUEST_SOURCES.items():
			if absolute == prefix:
				return label
			if absolute.startswith(prefix + "."):
				part = absolute[len(prefix) + 1 :].split(".")[0]
				return f"{label}:{part}" if label == "form_dict" else f"{label}.{part}"
		return ""


def keep_stored(flows: list[Flow], cls: str) -> list[Flow]:
	"""Drop the rows of `cls`'s methods whose source is an attribute no method of the class stores a
	source on. A store counts when its value comes from a parameter, the request or another stored
	attribute (`self.b = self.a`), so the stored set grows to a fixed point first."""
	mine = [f.caller.rsplit(".", 1)[0] == cls for f in flows]
	stores = [f for f, own in zip(flows, mine) if own and f.arg == "=" and f.callee.startswith("self.")]
	stored: set[str] = set()
	while True:
		grown = stored | {f.callee for f in stores if not f.source.startswith("self.") or f.source in stored}
		if grown == stored:
			break
		stored = grown
	return [
		f
		for f, own in zip(flows, mine)
		if not (own and f.source.startswith("self.") and f.source not in stored)
	]


def _arguments(node: ast.Call) -> list[tuple[str, ast.expr]]:
	"""(arg, value) per argument: `0`, `1`, ... by position, the keyword, or `*` / `**`."""
	args = [("*" if isinstance(a, ast.Starred) else str(i), a) for i, a in enumerate(node.args)]
	return args + [(kw.arg or "**", kw.value) for kw in node.keywords]


def _on_self(node: ast.expr) -> bool:
	return isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self"


def _is_static(node: ast.FunctionDef) -> bool:
	return any(dotted(d) == "staticmethod" for d in node.decorator_list)
