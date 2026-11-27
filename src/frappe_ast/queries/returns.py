"""Whether a call's return value carries the argument a value went into it by.

The analyzer passes every argument of a call through to its return value, since it sees one file.
A `flows` row that went through calls lists them in `returns` (`line:col:callee:arg`). Here each
call is resolved like `edges` does, and the callee's `<return>` rows say which parameters it returns:
`rows = finder.find(key, limit=limit)` gives `rows` neither key nor limit when `find` returns
`self.rows`. A call keeps the value when no callee is known (a library or a builtin), when it
creates an instance (`Cls(x)` runs `__init__`, which returns nothing, but the call returns the
object), when an argument binds to no parameter, or when a callee returns the parameter
(checked the same way through the calls inside it). Summaries are memoised, and a recursive call keeps the value.
"""

from collections import defaultdict

from frappe_ast.queries.callgraph import CallGraph
from frappe_ast.store import Store

# Edge kinds whose call binds the written arguments to the callee's parameters.
ARG_EDGES = {"call", "inherited", "instance", "doc_method", "override", "proxy"}

RETURN = "<return>"


class ReturnSummaries:
	def __init__(self, store: Store, graph: CallGraph, bind):
		"""`bind(function, arg)` is the parameter of `function` that `arg` binds to, or ""."""
		self.store = store
		self.graph = graph
		self.bind = bind
		self._calls: dict[int, dict[tuple[int, int], list[dict]]] = {}  # file -> (line, col) -> calls
		self._returns: dict[str, list[dict]] = {}  # function -> its <return> rows
		self._memo: dict[tuple[str, str], bool] = {}

	def keeps(self, file_id: int, returns: str) -> bool:
		"""Whether a value reaches the end of a `returns` chain: every call on it returns it."""
		return all(self._passes(file_id, step) for step in filter(None, returns.split(";")))

	def _passes(self, file_id: int, step: str) -> bool:
		line, col, rest = step.split(":", 2)
		callee, arg = rest.rsplit(":", 1)
		targets = {
			target
			for call in self._calls_at(file_id, int(line), int(col))
			if call["callee"] == callee
			for target, kind in self.graph.targets(call)
			if kind in ARG_EDGES
		}
		return not targets or any(self._returns_param(t, self.bind(t, arg)) for t in targets)

	def _returns_param(self, function: str, param: str) -> bool:
		if not param or function.endswith(".__init__"):
			return True  # calling a class returns the instance its arguments built
		key = (function, param)
		if key not in self._memo:
			self._memo[key] = True  # a call back into itself keeps the value
			self._memo[key] = any(
				self.keeps(r["file_id"], r["returns"] or "")
				for r in self._return_rows(function)
				if r["source"] == f"param:{param}"
			)
		return self._memo[key]

	def _calls_at(self, file_id: int, line: int, col: int) -> list[dict]:
		if file_id not in self._calls:
			found = defaultdict(list)
			for c in self.store.rows(
				"select callee, resolved, receiver, arg, line, col from calls where file_id = ?", file_id
			):
				found[(c["line"], c["col"])].append(c)
			self._calls[file_id] = found
		return self._calls[file_id].get((line, col), [])

	def _return_rows(self, function: str) -> list[dict]:
		if function not in self._returns:
			self._returns[function] = self.store.rows(
				"select source, returns, file_id from flows where caller = ? and callee = ?", function, RETURN
			)
		return self._returns[function]
