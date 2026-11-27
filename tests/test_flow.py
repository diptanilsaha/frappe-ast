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


def test_each_flow_joins_one_call(store):
	# A flow names its call by position, so two calls on one line never share a receiver.
	(row,) = store.rows(
		"select count(*) n from (select w.rowid from flows w join calls c on c.file_id = w.file_id "
		"and c.caller = w.caller and c.line = w.line and c.col = w.col and c.callee = w.callee "
		"group by w.rowid having count(*) > 1)"
	)
	assert row["n"] == 0


def test_follow_drops_a_value_the_callee_does_not_return(flows, lines):
	# Finder.find returns self.rows: the rows read from it carry neither key nor limit (#13). echo
	# returns its parameter, and str is outside the bench, so both keep what they are given.
	at = {(r["path"].rsplit(":", 1)[1], r["source"]) for r in flows.follow("demo.argflow.search")}
	assert (str(lines["find"]), "param:limit") in at
	assert not {(str(lines["row"]), "param:key"), (str(lines["row"]), "param:limit")} & at
	assert (str(lines["echo"]), "param:key") in at
	assert (str(lines["builtin"]), "param:limit") in at


def test_follow_crosses_a_store_on_self(flows, lines):
	# find stores limit on self, and query reads it: the read is part of limit's flow (#12).
	rows = [
		r
		for r in flows.follow("demo.argflow.search", source="param:limit")
		if r["path"] == f"{ARGFLOW}:{lines['stored_read']}"
	]
	(row,) = rows
	assert row["callee"] == "frappe.get_all" and row["arg"] == "limit"
	assert [step.split("  ", 1)[1] for step in row["through"]] == [
		"demo.argflow.Finder.find (limit)",
		"demo.argflow.Finder.query (self.limit)",
	]


def test_store_on_self_is_a_row(flows, lines):
	rows = flows.rows("demo.argflow.Finder.find")
	assert ("find", "param:limit", "demo.argflow.Finder.limit", "=") in facts(rows)


def test_filling_a_container_carries_what_went_in(flows, lines):
	(row,) = flows.rows("demo.argflow.fill", to="frappe.get_all")
	assert (row["source"], row["arg"], row["via"]) == ("param:name", "filters", "container")


@pytest.mark.parametrize(
	"function, tag, source",
	[
		("demo.argflow.report", "criterion", "param:start"),  # a comparison builds the criterion
		("demo.argflow.show", "made", "param:title"),  # row.title = title, then return row
		("demo.argflow.match", "constructed", "param:doctype"),  # Query(doctype) returns the instance
		("demo.argflow.show_groups", "grouped", "param:rows"),  # grouped[k].append(row)
	],
)
def test_a_returned_value_keeps_what_was_put_in_it(flows, lines, function, tag, source):
	at = {(r["path"], r["source"]) for r in flows.rows(function)}
	assert (f"{ARGFLOW}:{lines[tag]}", source) in at
