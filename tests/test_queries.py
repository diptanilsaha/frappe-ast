import json

from frappe_ast.cli import main
from frappe_ast.queries.check import Checker
from frappe_ast.queries.doctype import DoctypeReport
from frappe_ast.queries.impact import Impact
from frappe_ast.queries.lookup import Lookup
from frappe_ast.queries.method import MethodTrace
from frappe_ast.workspace import Bench

TASK_JS = "apps/demo/demo/demo/doctype/demo_task/demo_task.js"


def test_method_trace(store):
	trace = MethodTrace(store).run("demo.api.validate_any")
	assert trace["found"]
	assert {h["key"] for h in trace["hooks"]} == {"doc_events"}
	assert any(r["via"] == "frappe.enqueue" for r in trace["string_refs"])


def test_method_trace_follows_reexport(store):
	trace = MethodTrace(store).run("demo.api.close")
	assert trace["definition"]["qualname"] == "demo.demo.doctype.demo_task.demo_task.close_task"
	assert trace["python_callers"][0]["scope"] == "demo.api.validate_any"
	# frm.call({method}) in the form script and useDoctype(...).runMethod in the frontend.
	assert [c["path"] for c in trace["client_calls"]] == [TASK_JS, "apps/demo/frontend/src/data/tasks.ts"]


def test_method_trace_override(store):
	trace = MethodTrace(store).run("demo.api.get_count")
	assert any(r["path"].endswith("Tasks.vue") for r in trace["string_refs"])
	# frappe is not in this bench, but the override is still reported.
	missing = MethodTrace(store).run("frappe.client.get_count")
	assert not missing["found"] and missing["overridden_by"][0]["value"] == "demo.api.get_count"


def test_doctype_report(store):
	report = DoctypeReport(store).run("demo task")
	assert report["found"] and report["definition"]["name"] == "Demo Task"
	assert report["controller"]["lifecycle"] == ["validate:7"]
	assert report["controller"]["whitelisted"] == ["finish:14"]
	assert {e["value"] for e in report["hooks"]["doc_events"]} >= {
		"demo.api.validate_any",
		"demo.api.on_submit_any",
	}
	assert report["schema"]["mandatory"] == ["status"]
	assert "Demo Task.items (Table)" in DoctypeReport(store).run("Demo Note")["linked_from"]


def test_doctype_report_json_schema(store):
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


def test_cross_checks(store):
	findings = Checker(store, {"demo", "frappe"}).run()
	got = {(f["rule"], f["message"]) for f in findings}
	assert ("FX201", "doc_events > Demo Task > on_submit: demo.api.missing_handler does not resolve") in got
	assert ("FX202", "demo.api.gone does not exist") in got
	assert ("FX203", "demo.api.not_exposed is not @frappe.whitelist()") in got
	assert ("FX204", "DemoTask.private_helper is not @frappe.whitelist()") in got
	assert ("FX204", "demo.demo.doctype.demo_task.demo_task.no_such_function does not exist") in got
	fx205 = {f["message"].split("'")[1] for f in findings if f["rule"] == "FX205"}
	assert fx205 == {"ghost_field"}
	# frappe-ui calls name the DocType themselves (apps/demo/frontend/src/data/tasks.ts).
	ui = {
		(f["rule"], f["message"]) for f in findings if f["path"].endswith("tasks.ts") and f["rule"] != "FX202"
	}
	# `vanished` is not reported: Document (frappe) is not in this bench, so it may be inherited.
	assert ui == {
		("FX204", "DemoTask.private_helper is not @frappe.whitelist()"),
		# priority comes from create_custom_fields, color from fixtures/custom_field.json.
		("FX207", "Demo Task has no field statsu (custom field, or a typo?)"),
	}
	# Overloaded def: the whitelisted implementation wins; frappe.client.* overrides are not "missing".
	assert not any("demo.api.make" in m or "close_task does not" in m for _, m in got)


def test_impact(store, bench_root):
	api = bench_root / "apps/demo/demo/api.py"
	original = api.read_text()
	api.write_text(original.replace('\tfrappe.throw("Plain message")', '\tfrappe.throw(_("Changed"))'))
	try:
		bench = Bench(bench_root)
		from frappe_ast.indexer import Indexer

		Indexer(bench, store, jobs=1).run(bench.apps(), log=lambda _: None)
		result = Impact(store, bench.root).run(bench.apps(), "HEAD")
	finally:
		api.write_text(original)
	(file,) = result["files"]
	(symbol,) = file["symbols"]
	assert symbol["symbol"].startswith("function demo.api.validate_any")
	assert {h["key"] for h in symbol["hooks"]} == {"doc_events"}


def test_cli_outline_json(bench_root, capsys):
	code = main(["outline", str(bench_root / TASK_JS), "--json"])
	out = json.loads(capsys.readouterr().out)
	assert code == 0
	assert out["frappe"]["form_events"][0].endswith("Demo Task:refresh")


def test_frappe_ui_calls_reach_the_method_trace(store):
	finish = MethodTrace(store).run("demo.demo.doctype.demo_task.demo_task.DemoTask.finish")
	assert {(c["path"].rsplit("/", 1)[-1], c["via"]) for c in finish["client_calls"]} == {
		("demo_task.js", "frm.call"),
		("tasks.ts", "runDocMethod"),
		("tasks.ts", "useDoc"),
	}
	# call(API.open) with API exported from api.ts; createResource({url: COUNT_URL}) in the same file.
	(const_call,) = MethodTrace(store).run("demo.api.open_tasks")["constant_calls"]
	assert const_call["constant"] == "API.open" and const_call["path"].endswith("tasks.ts")
	count = MethodTrace(store).run("demo.api.get_count")
	assert any(r["via"] == "createResource(COUNT_URL)" for r in count["string_refs"])


def test_doctype_report_client_side(store):
	report = DoctypeReport(store).run("Demo Task")
	assert any(m.startswith("close_task (module_method") for m in report["client_methods"])
	fields = report["client_fields"]
	assert "statsu:1" in fields["read_by_client"] and "items.note" not in fields["read_by_client"]
	assert sorted(c.split()[0] for c in fields["custom_fields"]) == ["color", "priority"]


def test_refs_finds_python_imports(store):
	lookup = Lookup(store, 20)
	names = lookup.refs("frappe.api.ApiVersion")["refs"]
	assert [(r["path"], r["kind"]) for r in names] == [("apps/demo/demo/api.py", "import_name")]
	modules = lookup.refs("typing", kind="import")["refs"]
	assert any(r["path"] == "apps/demo/demo/api.py" for r in modules)
