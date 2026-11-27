"""`doctype`, `record` and `refs`: what a DocType or standard record is made of, and who names it."""

from support import tagged_lines

from frappe_ast.queries.doctype import DoctypeReport
from frappe_ast.queries.lookup import Lookup
from frappe_ast.queries.record import RecordReport


def test_doctype_report(store):
	report = DoctypeReport(store).run("demo task")  # names match without case
	assert report["found"] and report["definition"]["name"] == "Demo Task"
	assert report["controller"]["lifecycle"] == ["validate:6"]
	assert report["controller"]["whitelisted"] == ["finish:13"]
	assert {"demo.api.validate_any", "demo.api.on_submit_any"} <= {
		e["value"] for e in report["hooks"]["doc_events"]
	}
	assert report["schema"]["mandatory"] == ["status"]
	assert "Demo Task.items (Table)" in DoctypeReport(store).run("Demo Note")["linked_from"]


def test_json_schema(store):
	report = DoctypeReport(store).run("Demo Task", with_fields=True)
	assert report["definition"]["flags"]["search_fields"] == "status"
	assert report["permissions"] == [
		"System Manager level 0: read write",
		"Demo User level 0 if_owner: read create",
		"System Manager level 1: read write",
	]
	assert report["schema"]["permlevels"] == {"1": "cost"}
	assert "cost Currency set_only_once default=0 permlevel=1" in report["schema"]["fields"]
	assert report["connections"] == ["Demo Note.task [Notes]"]
	assert report["actions"] == ["Close: Server Action demo.api.close"]
	assert report["states"] == ["Open (Blue)"]


def test_client_side(store):
	report = DoctypeReport(store).run("Demo Task")
	assert any(m.startswith("close_task (module_method") for m in report["client_methods"])
	fields = report["client_fields"]
	assert "statsu:1" in fields["read_by_client"] and "items.note" not in fields["read_by_client"]
	# priority from create_custom_fields, color from fixtures/custom_field.json.
	assert sorted(c.split()[0] for c in fields["custom_fields"]) == ["color", "priority"]


def test_record_report(store):
	report = RecordReport(store).run("open tasks")
	assert report["record"].startswith("report Open Tasks (report_type=Script Report")
	assert any(c.endswith("open_tasks.py  OpenTasksReport:1 execute:7") for c in report["code"])
	assert report["linked_from"][0].endswith("open_tasks.js:4 frappe.set_route")
	assert report["targets"] == ["doctype Demo Note", "doctype Demo Task"]


def test_refs(store):
	lookup = Lookup(store, 20)
	names = lookup.refs("frappe.api.ApiVersion")["refs"]
	assert [(r["path"], r["kind"]) for r in names] == [("apps/demo/demo/api.py", "import_name")]
	assert any(r["path"] == "apps/demo/demo/api.py" for r in lookup.refs("typing", kind="import")["refs"])
	# frappe.publish_realtime in Python and frappe.realtime.on in JS.
	assert {r["access"] for r in lookup.refs("task_done", "realtime")["refs"]} == {"publish", "listen"}


def test_refs_fold_uses_in_one_function(store, bench_root):
	lines = tagged_lines(bench_root / "apps/demo/demo/dispatch.py")
	(row,) = Lookup(store, 20).refs("total", "attribute")["refs"]
	assert row["line"] == lines["total_first"]
	assert row["lines"] == [lines["total_first"], lines["total_again"]]
	assert row["col"] > 0


def test_refs_text_prints_every_line_of_a_folded_row(cli, bench_root):
	lines = tagged_lines(bench_root / "apps/demo/demo/dispatch.py")
	out = cli("refs", "total", "--kind", "attribute")
	assert f"apps/demo/demo/dispatch.py:{lines['total_first']},{lines['total_again']}  " in out
