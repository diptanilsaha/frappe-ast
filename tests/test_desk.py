"""Desk JS (form controllers, frm field calls, realtime) and standard records (Report, Number Card)."""

from frappe_ast.js.analyzer import JsAnalyzer
from frappe_ast.queries.check import Checker
from frappe_ast.queries.form import FormReport
from frappe_ast.queries.lookup import Lookup
from frappe_ast.queries.record import RecordReport

TASK_JS = "apps/demo/demo/demo/doctype/demo_task/demo_task.js"
BASE_JS = "apps/demo/demo/public/js/base_controller.js"


def refs(facts, kind):
	return {r.target for r in facts.refs if r.kind == kind}


def test_desk_js_facts(bench_root):
	facts = JsAnalyzer({"demo"}).analyze(TASK_JS, (bench_root / TASK_JS).read_text(), "js")
	assert refs(facts, "form_controller") == {"demo.TaskController"}
	assert refs(facts, "cscript_event") == {"custom_refresh"}
	assert {"statsu", "items.note", "items.nope", "*.note", "progress", "ghost", "picker"} <= refs(
		facts, "form_field"
	)
	assert refs(facts, "script_field") == {"progress", "picker"}
	assert refs(facts, "realtime") == {"task_done"}
	(cls,) = [s for s in facts.symbols if s.qualname == "demo.TaskController"]
	assert cls.kind == "class" and cls.signature == "(demo.BaseController)"
	assert {s.qualname for s in facts.symbols if s.parent == "demo.TaskController"} == {
		"demo.TaskController.status",
		"demo.TaskController.helper_not_an_event",
	}


def test_form_report_runs_in_desk_order(store):
	report = FormReport(store).run("Demo Task", "status")
	assert report["scripts"][0] == f"{TASK_JS}  (form)"
	assert [c.split()[0] for c in report["controller"]] == [
		"demo.TaskController",
		"demo.BaseController",
		"frappe.ui.form.Controller",
	]
	# frappe.ui.form.on handler first, then the most derived cscript method (TaskController, not Base).
	assert [r.split("  ")[0] for r in report["runs"]] == ['on("Demo Task")', "cscript.status"]
	assert "demo.TaskController" in report["runs"][1]
	refresh = FormReport(store).run("Demo Task", "refresh")["runs"]
	assert [r.split("  ")[0] for r in refresh] == [
		'on("Demo Task")',
		"cscript.refresh",
		"cscript.custom_refresh (assigned)",
	]
	assert BASE_JS in refresh[1]
	summary = FormReport(store).run("Demo Task")["events"]["Demo Task"]
	assert "cscript only: custom_refresh" in summary  # assigned handlers count, helpers do not
	assert not any("helper_not_an_event" in line for line in summary)


def test_js_shared_form_setups(bench_root):
	facts = JsAnalyzer({"demo"}).analyze(BASE_JS, (bench_root / BASE_JS).read_text(), "js")
	names = {s.qualname for s in facts.symbols}
	assert {"demo.setup.task_triggers", "demo.utils.close", "demo.Notes.save"} <= names
	events = {(r.kind, r.target) for r in facts.refs if r.kind in ("setup_event", "form_event")}
	assert events == {
		("setup_event", "@self:cost"),
		("form_event", "Demo Note:title"),
		("setup_event", "$:note"),
	}
	task = JsAnalyzer({"demo"}).analyze(TASK_JS, (bench_root / TASK_JS).read_text(), "js")
	assert ("setup_call", "Demo Task", "demo.setup.task_triggers") in {
		(r.kind, r.target, r.via) for r in task.refs
	}
	assert ("js_new", "demo.Notes") in {(r.kind, r.target) for r in task.refs}
	assert ("form_event", "Demo Task:cost") in {(r.kind, r.target) for r in task.refs}


def test_form_report_shared_setups(store):
	# 3-arg frappe.ui.form.on in the form script, then frappe.ui.form.on(this.frm.doctype) in its JS class.
	cost = FormReport(store).run("Demo Task", "cost")["runs"]
	assert [r.rsplit(":", 1)[0].split("  ")[-1] for r in cost] == [TASK_JS, BASE_JS]
	# demo.setup.task_triggers("Demo Task") registers frappe.ui.form.on(doctype, {note}).
	assert [r.split("  ")[-1] for r in FormReport(store).run("Demo Task", "note")["runs"]][0].startswith(
		BASE_JS
	)
	# The JS class registers a handler on the child table while it runs on the form.
	title = FormReport(store).run("Demo Task", "title")["runs"]
	assert title[0].startswith('[Demo Note.title, table items] on("Demo Note")') and BASE_JS in title[0]


def test_desk_field_and_record_checks(store):
	got = {(f["rule"], f["message"]) for f in Checker(store, {"demo"}).run(rules={"FX207", "FX208"})}
	desk = {m for r, m in got if r == "FX207" and "(custom" in m and "Demo Task" in m}
	assert desk == {
		"Demo Task has no field statsu (custom field, or a typo?)",
		"Demo Task table items has no field nope (custom field, or a typo?)",
		"Demo Task has no field ghost (custom field, or a typo?)",
		"Demo Task has no field colour (custom field, or a typo?)",
	}
	fx208 = [m for r, m in got if r == "FX208"]
	# `execute = OpenTasksReport.execute` is an entry point; the misnamed registration is not.
	assert len(fx208) == 1 and "registers report 'Open Taskz', but the report is 'Open Tasks'" in fx208[0]


def test_record_report_and_realtime(store):
	report = RecordReport(store).run("open tasks")
	assert report["record"].startswith("report Open Tasks (report_type=Script Report")
	assert any(c.endswith("open_tasks.py  OpenTasksReport:2 execute:8") for c in report["code"])
	assert report["linked_from"][0].endswith("open_tasks.js:5 frappe.set_route")
	assert report["targets"] == ["doctype Demo Note", "doctype Demo Task"]
	events = Lookup(store).refs("task_done", "realtime")["refs"]
	assert {r["access"] for r in events} == {"publish", "listen"}
