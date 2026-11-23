"""`method`: every place that calls, hooks, exposes or replaces a server function."""

from support import calls

from frappe_ast.queries.patches import instance_names

TASK = "demo.demo.doctype.demo_task.demo_task"
ARCHIVE = "demo.controllers.base_document.BaseDocument.archive"
TASK_JS = "apps/demo/demo/demo/doctype/demo_task/demo_task.js"
BASE_JS = "apps/demo/demo/public/js/base_controller.js"
SHAPES_JS = "apps/demo/demo/public/js/shapes.js"


def test_hooks_and_enqueue(trace):
	report = trace("demo.api.validate_any")
	assert report["found"]
	assert {h["key"] for h in report["hooks"]} == {"doc_events"}
	assert any(r["via"] == "frappe.enqueue" for r in report["string_refs"])


def test_reexport_paths(trace):
	# demo.api re-exports close_task as close. Callers of either name count for the definition.
	report = trace("demo.api.close")
	assert report["definition"]["qualname"] == f"{TASK}.close_task"
	assert report["python_callers"][0]["scope"] == "demo.api.validate_any"
	assert any(r["path"] == SHAPES_JS for r in report["string_refs"])  # frappe.xcall("demo.api.close")
	# frm.call({method}), the call_action wrapper, and useDoctype(...).runMethod.
	assert [c["path"] for c in report["client_calls"]] == [
		TASK_JS,
		SHAPES_JS,
		"apps/demo/frontend/src/data/tasks.ts",
	]
	assert ("shapes.js", "call_action") in calls(report["client_calls"])


def test_doc_method_callers(trace):
	finish = calls(trace(f"{TASK}.DemoTask.finish")["client_calls"])
	assert {
		("demo_task.js", "frm.call"),
		("demo_task.js", "frappe.call"),  # {method, doc: frm.doc}, and a local frappe.get_doc
		("tasks.ts", "runDocMethod"),  # useDoctype("Demo Task")
		("tasks.ts", "useDoc"),
		("shapes.js", "run_doc_method"),
		("archive.ts", "runDocMethod"),  # createListResource handle
		("archive.ts", "run_doc_method"),  # createResource({url: "run_doc_method", makeParams})
	} <= finish
	# Nothing else in the form script or the useDoctype frontend file reaches it.
	assert {c for c in finish if c[0] in ("demo_task.js", "tasks.ts")} == {
		("demo_task.js", "frm.call"),
		("demo_task.js", "frappe.call"),
		("tasks.ts", "runDocMethod"),
		("tasks.ts", "useDoc"),
	}


def test_shared_desk_js_runs_on_the_form(trace):
	# BaseDocument.archive, called from shared desk JS that the Demo Task form loads.
	archive = trace(ARCHIVE)["client_calls"]
	assert [c["scope"] for c in archive if c["path"] == BASE_JS] == [
		"demo.BaseController.setup.form:@self",  # frappe.ui.form.on(this.frm.doctype) in the class
		"demo.BaseController.archive",  # the JS class that demo_task.js installs as frm.cscript
		"demo.setup.task_triggers.form:$.note",  # a setup function, called with "Demo Task"
		"demo.utils.close",  # a $.extend(demo.utils) helper the form calls
		"demo.Notes.save",  # a class the form creates with `new demo.Notes({frm})`
	]
	assert {
		("shapes.js", "run_doc_method"),  # {method: "run_doc_method", args: {docs: frm.doc, method}}
		("archive.ts", "runDocMethod"),  # a handle exported from another file
		("demo_task.json", "Button"),  # a Button field whose options name the method
	} <= calls(archive)


def test_maybe_calls(trace):
	# A helper nothing calls, and a doc from a callback: the DocType is unknown.
	maybe = trace(f"{TASK}.DemoTask.finish")["maybe_client_calls"]
	assert [c["scope"] for c in maybe if c["path"] in (TASK_JS, BASE_JS)] == [
		"form:Demo Task.onload",
		"demo.mark_done",
	]
	assert ("Importer.tsx", "run_doc_method") in calls(maybe)  # call({docs: data.doc}) in frappe-react-sdk
	# Not whitelisted: no maybe.
	assert not trace(f"{TASK}.DemoTask.private_helper")["maybe_client_calls"]


def test_string_refs(trace):
	make = trace("demo.api.make")["string_refs"]
	assert [r["via"] for r in make if r["path"] == TASK_JS] == ["url", "url", "url"]
	assert any(r["path"].endswith("api.py") and r["via"] == "url" for r in make)  # a Python f-string
	assert any(r["path"].endswith("Tasks.vue") for r in trace("demo.api.get_count")["string_refs"])
	# frappe.call({module, page, method}).
	assert [r["path"] for r in trace("demo.demo.page.task_board.task_board.get_board")["string_refs"]] == [
		SHAPES_JS
	]


def test_constant_calls(trace):
	# call(API.open) with API exported from api.ts, and createResource({url: COUNT_URL}) in the same file.
	(const_call,) = trace("demo.api.open_tasks")["constant_calls"]
	assert const_call["constant"] == "API.open" and const_call["path"].endswith("tasks.ts")
	assert any(r["via"] == "createResource(COUNT_URL)" for r in trace("demo.api.get_count")["string_refs"])


def test_override_of_a_missing_function(trace):
	# frappe is not in this bench, but the override is still reported.
	missing = trace("frappe.client.get_count")
	assert not missing["found"] and missing["overridden_by"][0]["value"] == "demo.api.get_count"


def test_monkey_patches(trace):
	make = trace("demo.api.make")
	assert [(r["path"], r["scope"], r["replacement"]) for r in make["patched_by"]] == [
		("apps/demo/demo/overrides.py", "demo.overrides.TaskOverride.validate", "demo.overrides.our_make"),
		("apps/demo_fr/demo_fr/overrides.py", "demo_fr.overrides.install", "demo_fr.overrides.our_make"),
	]
	# A wrapper that the patching function defines.
	assert [r["replacement"] for r in trace("demo.api.get_count")["patched_by"]] == [
		"demo.overrides.install.counted"
	]
	assert [r["target"] for r in trace("demo.overrides.install.counted")["patches"]] == ["demo.api.get_count"]
	# The replacement's own trace says what it replaces: a patch and a regional override.
	assert {r["target"] for r in trace("demo.overrides.our_make")["patches"]} == {
		"demo.api.make",
		"demo.api.open_tasks",
	}


def test_regional_overrides(trace):
	# erpnext's @allow_regional runs the hook's function for companies in that country.
	(regional,) = trace("demo.api.open_tasks")["patched_by"]
	assert (regional["region"], regional["replacement"]) == ("India", "demo.overrides.our_make")


def test_patches_through_process_globals():
	# frappe/recorder.py: `frappe.db.sql = record_sql` replaces Database.sql on the live connection.
	assert instance_names(["frappe.database.database.Database.sql"]) == [
		"frappe.database.database.Database.sql",
		"frappe.db.sql",
		"frappe.db.__class__.sql",
	]
