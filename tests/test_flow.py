"""The flow query: filters over the argument-flow facts of the whole bench."""

import pytest

from frappe_ast.queries.flow import FlowQuery


@pytest.fixture(scope="module")
def flows(store):
	return FlowQuery(store)


def test_flows_out_of_a_function(flows):
	rows = flows.rows("demo.argflow.from_request")
	assert {(r["source"], r["callee"], r["arg"]) for r in rows} == {
		("request.headers:Host", "frappe.cache.set_value", "1"),
		("form_dict:task", "frappe.db.get_value", "1"),
	}


def test_flows_into_a_callee(flows):
	rows = flows.rows(to="frappe.db.get_value", source="param", direct=True)
	assert {(r["caller"], r["source"]) for r in rows} == {("demo.argflow.load", "param:name")}
	assert rows[0]["path"].endswith("argflow.py:16")


def test_follow_into_bench_functions(flows):
	rows = flows.follow("demo.argflow.outer", source="param:name")
	deepest = {(r["callee"], r["arg"], r["via"], len(r["through"])) for r in rows}
	assert ("frappe.get_doc", "1", ".strip", 1) in deepest  # outer -> inner(docname)
	assert ("frappe.db.delete", "1", ".strip", 2) in deepest  # outer -> inner -> remove(task)
	assert not any(r["callee"] == "frappe.db.delete" and r["arg"] != "1" for r in rows)


def test_follow_through_an_instance_and_kwargs(flows):
	rows = flows.follow("demo.argflow.outer", to="frappe.make_get_request")
	assert [(r["source"], r["through"][0].split("  ")[1]) for r in rows] == [
		("param:kwargs", "demo.argflow.Loader.fetch (url)")
	]


def test_follow_stops_at_depth(flows):
	rows = flows.follow("demo.argflow.outer", to="frappe.db.delete", depth=1)
	assert rows == []
