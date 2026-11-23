"""`form`: the scripts a desk form loads and what runs, in desk order, when an event fires."""

from frappe_ast.queries.form import FormReport

TASK_JS = "apps/demo/demo/demo/doctype/demo_task/demo_task.js"
BASE_JS = "apps/demo/demo/public/js/base_controller.js"


def runs(store, event: str) -> list[str]:
	return FormReport(store).run("Demo Task", event)["runs"]


def test_scripts_and_controller_chain(store):
	report = FormReport(store).run("Demo Task", "status")
	assert report["scripts"][0] == f"{TASK_JS}  (form)"
	assert [c.split()[0] for c in report["controller"]] == [
		"demo.TaskController",
		"demo.BaseController",
		"frappe.ui.form.Controller",
	]


def test_handlers_run_in_desk_order(store):
	# frappe.ui.form.on handlers first, then the most derived cscript method (TaskController, not Base).
	status = runs(store, "status")
	assert [r.split("  ")[0] for r in status] == ['on("Demo Task")', "cscript.status"]
	assert "demo.TaskController" in status[1]
	# The form's own script, shared public/js code that registers on the form, then cscript.
	refresh = runs(store, "refresh")
	assert [r.split("  ")[0] for r in refresh] == [
		'on("Demo Task")',
		'on("Demo Task")',
		"cscript.refresh",
		"cscript.custom_refresh (assigned)",
	]
	assert "public/js/shapes.js" in refresh[1] and BASE_JS in refresh[2]


def test_event_summary(store):
	summary = FormReport(store).run("Demo Task")["events"]["Demo Task"]
	assert "cscript only: custom_refresh" in summary  # assigned handlers count, helper methods do not
	assert not any("helper_not_an_event" in line for line in summary)


def test_shared_setups(store):
	# The 3-argument form in the form script, then frappe.ui.form.on(this.frm.doctype) in its JS class.
	assert [r.rsplit(":", 1)[0].split("  ")[-1] for r in runs(store, "cost")] == [TASK_JS, BASE_JS]
	# demo.setup.task_triggers("Demo Task") registers frappe.ui.form.on(doctype, {note}).
	assert runs(store, "note")[0].split("  ")[-1].startswith(BASE_JS)
	# The JS class registers a handler on the child table while it runs on the form.
	(title,) = runs(store, "title")
	assert title.startswith('[Demo Note.title, table items] on("Demo Note")') and BASE_JS in title
