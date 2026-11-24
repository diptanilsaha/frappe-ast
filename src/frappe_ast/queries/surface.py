"""Entry points: which functions something outside the process can make run, and how.

A root is a function a request, a document operation, a hook or the scheduler runs directly:

- `api` / `guest_api`: `@frappe.whitelist()` (with `allow_guest`), and `override_whitelisted_methods`.
- `route`: a werkzeug `Rule(..., endpoint=fn)` (the REST API's own handlers).
- `request`: the WSGI entry (`frappe.app.application`) and request-time hooks (`before_request`,
  `auth_hooks`, `on_session_creation`, ...).
- `www`: `get_context` of a website route (`www/`, `templates/pages/`, web forms).
- `doc_lifecycle`: a DocType controller's lifecycle method (`validate`, `on_update`, ...), which
  desk saves and `/api/resource` run.
- `doc_event`, `permission`, `scheduler`, `template`: hooks.py `doc_events`, permission hooks,
  `scheduler_events`, `jinja`.
- `report`: a report's `execute`.
- `hook`: any other hooks.py value naming a function.

`reached()` walks every edge of the call graph from every root, so a function deep inside the
framework shows which surfaces can make it run. It is static and conservative in the same way as
the call graph: a missing edge hides a route, it never invents a root.
"""

from collections import defaultdict, deque

from frappe_ast.python.frappe_api import (
	LIFECYCLE_METHODS,
	REQUEST_ROOTS,
	ROUTE_APIS,
	SURFACE_HOOKS,
	WWW_DIRS,
)
from frappe_ast.queries.callgraph import CallGraph
from frappe_ast.store import Store

_LIFECYCLE = set(LIFECYCLE_METHODS) | {"get_context", "has_website_permission"}


class Surface:
	def __init__(self, store: Store, graph: CallGraph | None = None):
		self.store = store
		self.graph = graph or CallGraph(store)
		self._edges: list[dict] | None = None

	def edges(self) -> list[dict]:
		if self._edges is None:
			self._edges = self.graph.edges()
		return self._edges

	def roots(self) -> dict[str, set[str]]:
		"""qualname -> the kinds of surface that run it directly."""
		roots: dict[str, set[str]] = defaultdict(set)
		for r in self.store.rows(
			"select s.qualname, s.name, s.kind, s.parent, s.whitelisted, s.allow_guest, f.path "
			"from symbols s join files f on f.id = s.file_id "
			"where f.lang = 'python' and s.kind in ('function', 'method')"
		):
			q, name, path = r["qualname"], r["name"], r["path"]
			if r["whitelisted"]:
				roots[q].add("guest_api" if r["allow_guest"] else "api")
			if q in REQUEST_ROOTS:
				roots[q].add("request")
			if name in ("get_context", "get_list_context") and (
				any(d in path for d in WWW_DIRS) or "/web_form/" in path
			):
				roots[q].add("www")
			elif r["kind"] == "method" and "/doctype/" in path and name in _LIFECYCLE:
				roots[q].add("doc_lifecycle")
			if name == "execute" and "/report/" in path and r["kind"] == "function":
				roots[q].add("report")
		for r in self.store.rows("select target, via from refs where kind = 'callback'"):
			if r["via"] in ROUTE_APIS:
				target = self.graph._callable(r["target"])
				if target:
					roots[target].add("route")
		for h in self.store.rows("select key, value from hooks"):
			target = self.graph._callable(h["value"] or "")
			if target:
				roots[target].add(SURFACE_HOOKS.get(h["key"], "hook"))
		return dict(roots)

	def reached(self, max_depth: int = 40) -> dict[str, dict]:
		"""Every function a root reaches: {qualname: {"kinds": [...], "depth": n}}, where depth is
		the fewest calls from any root (0 for a root)."""
		out = defaultdict(set)
		for e in self.edges():
			out[e["caller"]].add(e["callee"])
		kinds: dict[str, set[str]] = defaultdict(set)
		depth: dict[str, int] = {}
		queue = deque()
		for q, ks in self.roots().items():
			kinds[q] |= ks
			depth[q] = 0
			queue.append(q)
		while queue:  # propagates root kinds: a function reached by several surfaces keeps them all
			node = queue.popleft()
			if depth[node] >= max_depth:
				continue
			for callee in out.get(node, ()):
				if callee not in depth or not kinds[node] <= kinds[callee]:
					kinds[callee] |= kinds[node]
					depth.setdefault(callee, depth[node] + 1)
					queue.append(callee)
		return {q: {"kinds": sorted(kinds[q]), "depth": depth[q]} for q in depth}

	def reach(self, target: str, limit: int = 10, max_depth: int = 20) -> list[dict]:
		"""Shortest routes from the roots to `target`, at most one per root."""
		goal = self.graph._callable(target) or target
		into = defaultdict(list)
		for e in self.edges():
			into[e["callee"]].append(e)
		roots = self.roots()
		found = []
		parent: dict[str, dict | None] = {goal: None}
		queue = deque([(goal, 0)])
		while queue and len(found) < limit:
			node, d = queue.popleft()
			if node in roots:
				steps, cur = [], node
				while parent[cur] is not None:
					steps.append(parent[cur])
					cur = parent[cur]["callee"]
				found.append({"root": node, "kinds": sorted(roots[node]), "depth": d, "steps": steps})
			if d >= max_depth:
				continue
			for e in sorted(into.get(node, ()), key=lambda x: (x["path"], x["line"])):
				if e["caller"] not in parent:
					parent[e["caller"]] = e
					queue.append((e["caller"], d + 1))
		return found
