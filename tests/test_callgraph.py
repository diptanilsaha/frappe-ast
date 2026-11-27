"""Call-graph edges: one case per edge kind, the shapes that must get none, and paths across kinds."""

import pytest

from frappe_ast.queries import callgraph
from frappe_ast.queries.callgraph import CallGraph
from frappe_ast.queries.paths import ShortestPaths

TASK = "demo.demo.doctype.demo_task.demo_task"


@pytest.fixture(scope="module")
def graph(store):
	return CallGraph(store)


def edges_from(graph, caller):
	return {(e["callee"], e["kind"]) for e in graph.edges(["demo"]) if e["caller"] == caller}


@pytest.mark.parametrize(
	"caller, callee, kind",
	[
		("demo.dispatch.make_storage", "demo.dispatch.Storage.__init__", "call"),  # Cls() runs __init__
		(f"{TASK}.DemoTask.retire", "demo.controllers.base_document.BaseDocument.archive", "inherited"),
		(
			"demo.dispatch.use_engine",
			"demo.dispatch.Engine.run",
			"instance",
		),  # engine = Engine(); engine.run()
		("demo.dispatch.use_engine_inline", "demo.dispatch.Engine.run", "instance"),  # Engine().run()
		(f"{TASK}.close_task", f"{TASK}.DemoTask.finish", "doc_method"),
		("demo.dispatch.save_task", f"{TASK}.DemoTask.validate", "lifecycle"),
		("demo.dispatch.save_task", "demo.api.validate_any", "doc_event"),  # doc_events "*"
		("demo.dispatch.submit_note", "demo.api.on_submit_any", "doc_event"),
		("demo.dispatch.Engine.conditions", "demo.dispatch.task_query", "hook"),
		("demo.dispatch.use_proxy", "demo.dispatch.build_query", "proxy"),  # qb: LocalProxy["Builder"]
		("demo.dispatch.Storage.store", "demo.dispatch.DiskStorage.write", "override"),
		("demo.dispatch.start_jobs", "demo.dispatch.job", "callback"),  # frappe.enqueue(job)
		("demo.dispatch.safe_globals", "demo.dispatch.sandboxed", "callback"),  # {"run": sandboxed}
		("demo.dispatch.start_jobs", "demo.dispatch.by_string", "dispatch"),  # frappe.enqueue("a.b.c")
		("demo.dispatch.configure", "demo.dispatch.setup", "dotted"),
		("demo.dispatch.use_property", "demo.dispatch.Conn.session", "property"),  # Conn().session
		("demo.dispatch.set_property", "demo.dispatch.Conn.session", "property"),  # @session.setter
		("demo.dispatch.Conn.reuse", "demo.dispatch.Conn.session", "property"),  # self.session
		("demo.dispatch.Conn.session", "demo.dispatch.Conn.open_session", "call"),
		("demo.dispatch.use_with", "demo.dispatch.Lock.__enter__", "instance"),  # with Lock():
		("demo.dispatch.use_with", "demo.dispatch.Lock.__exit__", "instance"),
		("demo.dispatch.use_with_bound", "demo.dispatch.Lock.__exit__", "instance"),  # with lock:
		("demo.dispatch.use_async_with", "demo.dispatch.Lock.__aenter__", "instance"),
		("demo.dispatch.ChildParser.__init__", "demo.dispatch.Parser.__init__", "super"),
		("demo.dispatch.ChildParser.save", "demo.dispatch.Parser.save", "super"),  # super(C, self)
		("demo.dispatch.GrandParser.parse", "demo.dispatch.Parser.parse", "super"),  # skips a base
	],
)
def test_edge_kinds(graph, caller, callee, kind):
	assert (callee, kind) in edges_from(graph, caller)


def test_override_keeps_the_base_method(graph):
	assert ("demo.dispatch.Storage.write", "call") in edges_from(graph, "demo.dispatch.Storage.store")


@pytest.mark.parametrize(
	"caller, kinds",
	[
		("demo.dispatch.rebound", {"instance"}),  # rebinding the name forgets the type
		("demo.dispatch.unknown_doc", {"lifecycle", "doc_event"}),  # DocType not a literal
		("demo.dispatch.read_plain", {"property"}),  # Storage().ready is not a property
	],
)
def test_no_edge_without_a_type(graph, caller, kinds):
	assert not {k for _, k in edges_from(graph, caller)} & kinds


def test_hook_edges_only_reach_functions(graph):
	edges = edges_from(graph, "demo.dispatch.run_handlers")
	assert ("demo.dispatch.setup", "hook") in edges
	assert not {c for c, _ in edges if c.startswith("/")}  # a path registered under the same key


def test_lifecycle_runs_only_its_events(graph):
	# frappe.delete_doc runs on_trash / on_change / after_delete, never validate
	assert ("demo.api.validate_any", "doc_event") not in edges_from(graph, "demo.dispatch.drop_task")


def test_wide_overrides_are_skipped(store, monkeypatch):
	monkeypatch.setattr(callgraph, "MAX_OVERRIDES", 0)  # as if Storage were Document
	edges = edges_from(CallGraph(store), "demo.dispatch.Storage.store")
	assert not {k for _, k in edges} & {"override"}


def test_paths_cross_edge_kinds(graph):
	routes = ShortestPaths(graph).find("demo.dispatch.use_engine", "demo.dispatch.task_query")["routes"]
	assert [e["kind"] for e in routes[0]] == ["instance", "call", "hook"]


def test_paths_returns_every_shortest_route(graph):
	# fan_out -> left -> left_inner -> Hub.meet -> end, and the same length through right: both
	# routes meet at Hub.meet, so the search must keep the second edge into it (#14).
	found = ShortestPaths(graph).find("demo.dispatch.fan_out", "demo.dispatch.Hub.end", limit=10)
	routes = [[e["callee"].rsplit(".", 1)[-1] for e in route] for route in found["routes"]]
	assert routes == [
		["left", "left_inner", "meet", "end"],
		["right", "right_inner", "meet", "end"],
	]
	assert found["more"] == 0


def test_paths_counts_the_routes_past_the_limit(graph):
	found = ShortestPaths(graph).find("demo.dispatch.fan_out", "demo.dispatch.Hub.end", limit=1)
	assert len(found["routes"]) == 1
	assert found["more"] == 1


def test_paths_none_when_unreachable(graph):
	found = ShortestPaths(graph).find("demo.dispatch.Hub.end", "demo.dispatch.fan_out")
	assert found == {"routes": [], "more": 0}
