"""The JS/TS and Vue analyzers: desk form scripts, shared desk JS, built method paths, frontends."""

from pathlib import Path

TASK_JS = "apps/demo/demo/demo/doctype/demo_task/demo_task.js"
BASE_JS = "apps/demo/demo/public/js/base_controller.js"
SHAPES_JS = "apps/demo/demo/public/js/shapes.js"
TASKS_VUE = "apps/demo/frontend/src/pages/Tasks.vue"


def test_form_script_server_calls(analyze):
	facts = analyze(TASK_JS)
	assert "Demo Task:refresh" in facts.targets("form_event")
	# frm.call("x") and frappe.call({method, doc}) run a Document method. The form's own doc has no
	# prefix. Another doc is "X:" when a local frappe.get_doc("X") names it, and "?:" otherwise
	# (`doc: connected_app`, or a `.then((doc) => ...)` callback).
	assert facts.targets("doc_method") == {
		"finish",
		"private_helper",
		"?:authorize",
		"?:finish",
		"Demo Note:finish",
	}
	# frm.call({method}) without `doc` runs a function of the controller module.
	assert facts.targets("module_method") == {"close_task", "no_such_function"}
	assert {"demo.api.open_tasks", "demo.api.not_exposed", "demo.api.gone"} <= facts.targets("method")
	assert facts.targets("listview") == {"Demo Task"}
	assert facts.targets("form_trigger") == {"recalculate"}


def test_api_urls_are_dotted_refs(analyze):
	facts = analyze(TASK_JS)
	# open_url_post, window.open with a query string, and a template string.
	assert [r.via for r in facts.refs("dotted") if r.target == "demo.api.make"] == ["url", "url", "url"]
	# fetch("/api/method/x") is a server call and a URL string.
	assert {
		r.kind
		for r in facts.refs("method") + facts.refs("dotted")
		if r.target == "demo.api.open_tasks" and r.via in ("url", "fetch")
	} == {"method", "dotted"}


def test_frappe_call_doc_targets_in_order(analyze):
	# frm.doc, a `doc` parameter of a class method, connected_app, a callback's doc, frappe.get_doc("Demo Note").
	doc_calls = sorted(
		(r.line, r.target) for r in analyze(TASK_JS).refs("doc_method") if r.via == "frappe.call"
	)
	assert [t for _, t in doc_calls] == ["finish", "finish", "?:authorize", "?:finish", "Demo Note:finish"]


def test_form_controller_and_fields(analyze):
	facts = analyze(TASK_JS)
	assert facts.targets("form_controller") == {"demo.TaskController"}
	assert facts.targets("cscript_event") == {"custom_refresh"}
	assert {"statsu", "items.note", "items.nope", "*.note", "progress", "ghost", "picker"} <= facts.targets(
		"form_field"
	)
	assert facts.targets("script_field") == {"progress", "picker"}  # assigned, or made with make_control
	assert facts.targets("realtime") == {"task_done"}
	(cls,) = [s for s in facts.symbols if s.qualname == "demo.TaskController"]
	assert cls.kind == "class" and cls.signature == "(demo.BaseController)"
	assert {s.qualname for s in facts.symbols if s.parent == "demo.TaskController"} == {
		"demo.TaskController.status",
		"demo.TaskController.helper_not_an_event",
	}


def test_shared_form_setups(analyze):
	base = analyze(BASE_JS)
	assert {"demo.setup.task_triggers", "demo.utils.close", "demo.Notes.save"} <= {
		s.qualname for s in base.symbols
	}
	# frappe.ui.form.on(this.frm.doctype) is "@self"; on the setup function's parameter it is "$".
	assert base.targets("setup_event") == {"@self:cost", "$:note"}
	assert base.targets("form_event") == {"Demo Note:title"}
	task = analyze(TASK_JS)
	assert ("Demo Task", "demo.setup.task_triggers") in task.pairs("setup_call")
	assert task.targets("js_new") == {"demo.Notes"}
	assert "Demo Task:cost" in task.targets("form_event")  # the 3-argument form


def test_built_method_paths(analyze):
	methods = analyze(SHAPES_JS).pairs("method")
	assert {
		# A constant prefix, in a template or a concatenation.
		("demo.api.make", "frappe.xcall(METHOD)"),
		("demo.api.open_tasks", "frappe.xcall(METHOD)"),
		# A `var` prefix, and a variable assigned in branches: one ref per value.
		("demo.api.close", "frappe.call(method_prefix)"),
		("demo.api.make", "frappe.call(method_prefix)"),
		("demo.api.open_tasks", "frappe.call(method_prefix)"),
	} <= methods
	# A built path outside a server call: {query: `${this.method_path}.open_tasks`}.
	assert ("demo.api.open_tasks", "string") in analyze(SHAPES_JS).pairs("dotted")


def test_wrappers_that_pass_the_method_name(analyze):
	facts = analyze(SHAPES_JS)
	assert {
		("demo.api.make", "api"),  # frappe.xcall(`${METHOD}.${method}`)
		("demo.api.get_count", "this.call"),  # `${this.method_path}.${method}`, a class method
		("demo.api.get_count", "booking"),  # let url = `/api/method/demo.api.${method}`; fetch(url)
	} <= facts.pairs("method")
	assert ("close_task", "call_action") in facts.pairs("module_method")  # frm.call({method})


def test_other_server_call_shapes(analyze):
	facts = analyze(SHAPES_JS)
	assert {
		("demo.demo.page.task_board.task_board.get_board", "frappe.call"),  # {module, page, method}
		("frappe.handler.logout", "fetch"),  # a bare name that frappe.handler runs
	} <= facts.pairs("method")
	# run_doc_method named directly, with dt, or with docs.
	assert {("Demo Task:finish", "run_doc_method"), ("archive", "run_doc_method")} <= facts.pairs(
		"doc_method"
	)


def test_frappe_ui_handles(analyze):
	facts = analyze("apps/demo/frontend/src/data/archive.ts")
	# createListResource is a DocType handle. One imported from another file is "<variable>:<method>".
	assert facts.pairs("doc_method") >= {
		("Demo Task:finish", "runDocMethod"),
		("Demo Task:finish", "run_doc_method"),
	}
	assert facts.pairs("handle_method") == {("taskList:archive", "runDocMethod")}
	assert ("Demo Task", "taskList") in analyze("apps/demo/frontend/src/data/taskList.ts").pairs(
		"doctype_handle"
	)


def test_frappe_react_sdk(analyze):
	facts = analyze("apps/demo/frontend/src/Importer.tsx")
	assert ("demo.api.get_count", "useFrappeGetCall") in facts.pairs("method")
	assert ("Demo Task", "useFrappeGetDocList") in facts.pairs("doctype")
	# orderBy is {field, order}: `status` is a field, `order` is not.
	assert "Demo Task.status" in facts.targets("field") and "Demo Task.order" not in facts.targets("field")
	# call({docs, method}) from useFrappePostCall("run_doc_method"): a doc of unknown DocType.
	assert ("?:finish", "run_doc_method") in facts.pairs("doc_method")


def test_vue_script_and_template(analyze, bench_root):
	facts = analyze(TASKS_VUE)
	# `method: 'POST'` next to `url` must not hide the url.
	assert facts.targets("method") == {"demo.api.get_count", "demo.api.open_tasks"}
	assert facts.targets("doctype") == {"Demo Task"}
	assert {"TaskList", "Button"} <= facts.targets("component")
	assert {c.callee for c in facts.calls if c.caller == "<template>"} == {"openTask", "save"}
	(open_task,) = [s for s in facts.symbols if s.name == "openTask"]
	assert (
		Path(bench_root / TASKS_VUE)
		.read_text()
		.splitlines()[open_task.line - 1]
		.startswith("function openTask")
	)


def test_vue_template_method_props(analyze):
	# api-url="..." and :fallback="'...'". title="demo.api.x" counts too: it is a path into the app.
	template = {r.target for r in analyze(TASKS_VUE).refs("dotted") if r.via == "template"}
	assert template == {"demo.api.get_count", "demo.api.open_tasks", "demo.api.x"}
