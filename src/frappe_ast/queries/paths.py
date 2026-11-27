"""Every shortest route from one function to another over the call graph.

A breadth-first search from the source reads the edges of a whole layer in one query and keeps,
for each function it reaches, every edge that reaches it at its shortest depth. It stops at the
layer that holds the target. The kept edges form a graph of shortest routes only: walking it
forward from the source, call sites in line order, gives every shortest route in a fixed order,
and counting along it gives how many there are without listing them.

A route is a chain of functions. Two calls from one function to the same callee are one step,
shown at the first call site, so routes differ in the functions they pass through.
"""

from frappe_ast.queries.callgraph import CallGraph

Step = dict  # an edge as `CallGraph.edges` returns it


class ShortestPaths:
	def __init__(self, graph: CallGraph):
		self.graph = graph

	def find(self, source: str, target: str, limit: int = 5, max_depth: int = 12) -> dict:
		"""{"routes": [[step, ...], ...], "more": n}: up to `limit` shortest routes, and how many
		other routes of the same length there are."""
		start = self.graph._callable(source) or source
		goal = self.graph._callable(target) or target
		into = self._search(start, goal, max_depth)
		if goal not in into or goal == start:
			return {"routes": [], "more": 0}
		out = _forward(into, goal)
		routes = self._walk(start, goal, out, limit)
		return {"routes": routes, "more": _count(start, goal, into) - len(routes)}

	def _search(self, start: str, goal: str, max_depth: int) -> dict[str, list[Step]]:
		"""function -> the edges that reach it at its shortest depth from `start`."""
		into: dict[str, list[Step]] = {start: []}
		frontier = [start]
		for _ in range(max_depth):
			if goal in into or not frontier:
				break
			layer: dict[str, dict[str, Step]] = {}  # callee -> caller -> its first call site
			for edge in sorted(self.graph.edges(callers=frontier), key=_order):
				if edge["callee"] not in into:
					layer.setdefault(edge["callee"], {}).setdefault(edge["caller"], edge)
			into.update((callee, list(steps.values())) for callee, steps in layer.items())
			frontier = sorted(layer)
		return into

	def _walk(self, start: str, goal: str, out: dict[str, list[Step]], limit: int) -> list[list[Step]]:
		"""The first `limit` routes, depth first from `start`, each function's steps in line order."""
		routes: list[list[Step]] = []
		stack: list[tuple[str, list[Step]]] = [(start, [])]
		while stack and len(routes) < limit:
			node, route = stack.pop()
			if node == goal:
				routes.append(route)
				continue
			for step in reversed(out.get(node, ())):
				stack.append((step["callee"], [*route, step]))
		return routes


def _forward(into: dict[str, list[Step]], goal: str) -> dict[str, list[Step]]:
	"""The shortest-route edges that lead to `goal`, keyed by caller and sorted by call site."""
	out: dict[str, list[Step]] = {}
	todo, seen = [goal], {goal}
	while todo:
		node = todo.pop()
		for step in into[node]:
			out.setdefault(step["caller"], []).append(step)
			if step["caller"] not in seen:
				seen.add(step["caller"])
				todo.append(step["caller"])
	return {caller: sorted(steps, key=_order) for caller, steps in out.items()}


def _count(start: str, goal: str, into: dict[str, list[Step]]) -> int:
	"""How many routes lead from `start` to `goal`, by paths into each function, nearest first."""
	ways: dict[str, int] = {}

	def count(node: str) -> int:
		if node == start:
			return 1
		if node not in ways:
			ways[node] = sum(count(s["caller"]) for s in into[node])
		return ways[node]

	return count(goal)


def _order(edge: Step) -> tuple:
	return edge["path"], edge["line"], edge["callee"], edge["kind"]
