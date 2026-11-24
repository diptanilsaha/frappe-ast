"""The attack surface: a root per kind, what the roots reach, and the route to one function."""

import pytest

from frappe_ast.queries.surface import Surface

TASK = "demo.demo.doctype.demo_task.demo_task"


@pytest.fixture(scope="module")
def surface(store):
	return Surface(store)


@pytest.mark.parametrize(
	"function, kind",
	[
		("demo.api.get_count", "api"),
		("demo.api.open_tasks", "guest_api"),
		("demo.dispatch.route_handler", "route"),  # Rule("/demo", endpoint=route_handler)
		("demo.dispatch.check_request", "request"),  # before_request
		("demo.www.demo_page.get_context", "www"),
		(f"{TASK}.DemoTask.validate", "doc_lifecycle"),
		("demo.api.validate_any", "doc_event"),
		("demo.dispatch.task_query", "permission"),  # permission_query_conditions
		("demo.api.daily", "scheduler"),
		("demo.dispatch.format_task", "template"),  # jinja
		("demo.dispatch.setup", "hook"),  # after_install: any other hook
	],
)
def test_root_kinds(surface, function, kind):
	assert kind in surface.roots()[function]


def test_called_functions_are_not_roots(surface):
	roots = surface.roots()
	assert "demo.dispatch.job" not in roots  # passed to enqueue by a function no root reaches
	assert f"{TASK}.DemoTask.set_title" not in roots  # a controller method, not a lifecycle one


def test_reached_keeps_depth_and_every_kind(surface):
	run = surface.reached()["demo.dispatch.Engine.run"]
	assert run["depth"] == 2  # get_context -> use_engine -> Engine.run
	assert "www" in run["kinds"]
	assert "demo.dispatch.job" not in surface.reached()


def test_reach_gives_the_route(surface):
	routes = surface.reach("demo.dispatch.Engine.run")
	www = next(r for r in routes if r["root"] == "demo.www.demo_page.get_context")
	assert www["kinds"] == ["www"]
	assert [e["callee"] for e in www["steps"]] == ["demo.dispatch.use_engine", "demo.dispatch.Engine.run"]
	assert surface.reach("demo.dispatch.job") == []
