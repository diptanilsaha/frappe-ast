demo.BaseController = class BaseController extends frappe.ui.form.Controller {
	setup() {
		frappe.ui.form.on(this.frm.doctype, "cost", (frm) => frm.call("archive"));
		frappe.ui.form.on("Demo Note", "title", () => {});
	}
	refresh(doc) {}
	status(doc) {}
	archive() {
		this.frm.call("archive");
	}
};

demo.mark_done = function (frm) {
	frm.call("finish");
	frm.call("private_helper");
};

demo.setup = {
	task_triggers(doctype) {
		frappe.ui.form.on(doctype, {
			note(frm) {
				frm.call("archive");
			},
		});
	},
};

$.extend(demo.utils, {
	close(frm) {
		frm.call("archive");
	},
});

demo.Notes = class Notes {
	constructor(opts) {
		this.frm = opts.frm;
	}
	save() {
		this.frm.call("archive");
	}
};
