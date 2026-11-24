"""The flow query: filters over the argument-flow facts, and following them across calls."""

import pytest
from support import tagged_lines

from frappe_ast.queries.flow import FlowQuery

ARGFLOW = "apps/demo/demo/argflow.py"


@pytest.fixture(scope="module")
def flows(store):
	return FlowQuery(store)


@pytest.fixture(scope="module")
def lines(bench_root):
	return tagged_lines(bench_root / ARGFLOW)


def facts(rows):
	return {(r["caller"].rsplit(".", 1)[-1], r["source"], r["callee"], r["arg"]) for r in rows}


@pytest.mark.parametrize(
	"query, expected",
	[
		(  # out of one function
			{"function": "demo.argflow.from_request"},
			{
				("from_request", "request.headers:Host", "frappe.cache.set_value", "1"),
				("from_request", "form_dict:task", "frappe.db.get_value", "1"),
			},
		),
		(  # into one callee, from any function, unchanged parameters only
			{"to": "frappe.db.get_value", "source": "param", "direct": True},
			{("load", "param:name", "frappe.db.get_value", "1")},
		),
		(  # by source key
			{"source": "form_dict:limit", "to": "frappe.db.sql"},
			{("load", "form_dict:limit", "frappe.db.sql", "0")},
		),
		(  # --direct drops a value that went through a call
			{"source": "form_dict:limit", "to": "frappe.db.sql", "direct": True},
			set(),
		),
	],
)
def test_rows(flows, query, expected):
	assert facts(flows.rows(**query)) == expected


def test_rows_carry_the_location(flows, lines):
	(row,) = flows.rows(to="frappe.db.get_value", source="param", direct=True)
	assert row["path"] == f"{ARGFLOW}:{lines['branch']}"


@pytest.mark.parametrize(
	"tag, callee, through",
	[
		("inner", "frappe.get_doc", ["demo.argflow.inner (docname)"]),  # outer -> inner
		("remove", "frappe.db.delete", ["demo.argflow.inner (docname)", "demo.argflow.remove (task)"]),
	],
)
def test_follow_binds_arguments_to_parameters(flows, lines, tag, callee, through):
	(row,) = flows.follow("demo.argflow.outer", to=callee)
	assert row["path"] == f"{ARGFLOW}:{lines[tag]}"
	assert (row["source"], row["arg"], row["via"]) == ("param:name", "1", ".strip")  # outer's own step
	assert [step.split("  ", 1)[1] for step in row["through"]] == through


def test_follow_through_an_instance_into_kwargs(flows, lines):
	(row,) = flows.follow("demo.argflow.outer", to="frappe.make_get_request")
	assert row["path"] == f"{ARGFLOW}:{lines['fetch']}"
	assert row["source"] == "param:kwargs"  # kwargs.get("url") binds to Loader.fetch(url)
	assert row["through"][0].endswith("demo.argflow.Loader.fetch (url)")


def test_follow_keeps_the_flows_of_the_function_itself(flows):
	assert facts(flows.rows("demo.argflow.outer")) <= facts(flows.follow("demo.argflow.outer"))


def test_follow_stops_at_depth(flows):
	assert flows.follow("demo.argflow.outer", to="frappe.db.delete", depth=1) == []
	assert flows.follow("demo.argflow.outer", to="frappe.db.delete", depth=2)
