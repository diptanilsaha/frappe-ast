frappe.ui.form.on("Demo Task", {
	refresh(frm) {
		frm.call("finish");
		frm.call("private_helper");
		frm.call({ method: "close_task", args: { name: frm.doc.name } });
		frm.call({ method: "no_such_function" });
		frappe.call({ method: "demo.api.open_tasks", args: {} });
		frappe.xcall("demo.api.not_exposed");
		frappe.call("demo.api.gone");
		frm.trigger("recalculate");
		frm.events.helper(frm);
	},
	status(frm) {},
	recalculate(frm) {},
	helper(frm) {},
	ghost_field(frm) {},
});
frappe.listview_settings["Demo Task"] = { add_fields: ["status"] };

demo.TaskController = class TaskController extends demo.BaseController {
	status(doc) {
		frappe.call({ method: "finish", doc: doc });
	}
	helper_not_an_event() {}
};
cur_frm.script_manager.make(demo.TaskController);
cur_frm.cscript.custom_refresh = function (doc) {};

frappe.ui.form.on("Demo Task", {
	setup(frm) {
		frm.set_value("statsu", "Open");
		frm.set_query("note", "items", () => ({}));
		frm.set_query("nope", "items", () => ({}));
		frm.add_fetch("note", "title", "title");
		frm.doc.progress = 1;
		if (frm.doc.progress && frm.doc.ghost) {}
		frappe.ui.form.make_control({ df: { fieldname: "picker", fieldtype: "HTML" } });
		frm.toggle_display("picker", true);
		frappe.realtime.on("task_done", () => {});
	},
});

frappe.ui.form.on("Demo Task", {
	onload(frm) {
		frappe.call({ method: "finish", doc: frm.doc });
		frappe.call({ method: "authorize", doc: connected_app });
		get_doc().then((doc) => frappe.call({ method: "finish", doc: doc }));
		const note = frappe.get_doc("Demo Note");
		frappe.call({ method: "finish", doc: note });
		open_url_post("/api/method/demo.api.make", { x: 1 });
		window.open("/api/method/demo.api.make?x=" + frm.doc.name);
		window.location.href = `/api/method/demo.api.make?x=${frm.doc.name}`;
		fetch("/api/method/demo.api.open_tasks");
	},
});

demo.setup.task_triggers("Demo Task");
// "color" is a custom field the app ships (fixtures/custom_field.json): not an FX205.
frappe.ui.form.on("Demo Task", "color", () => {});
frappe.ui.form.on("Demo Task", "cost", (frm) => {
	demo.utils.close(frm);
	new demo.Notes({ frm });
});
