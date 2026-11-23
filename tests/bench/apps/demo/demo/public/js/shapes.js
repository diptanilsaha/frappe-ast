const METHOD = "demo.api";

function call_action(frm, method) {
	frm.call({ method, freeze: true });
}

function api(method, args) {
	return frappe.xcall(`${METHOD}.${method}`, args);
}

demo.Party = class Party {
	constructor() {
		this.method_path = "demo.api";
	}
	call(method) {
		return frappe.xcall(`${this.method_path}.${method}`);
	}
	load() {
		return this.call("get_count");
	}
	query() {
		return { query: `${this.method_path}.open_tasks` };
	}
};

var method_prefix = "demo.api.";

function update_board(action) {
	var method;
	if (action === "add") {
		method = "make";
	} else {
		method = "open_tasks";
	}
	return frappe.call({ method: method_prefix + method });
}

async function booking(method, args) {
	let url = `/api/method/demo.api.${method}`;
	return fetch(url, args);
}

frappe.ui.form.on("Demo Task", {
	refresh(frm) {
		booking("get_count", {});
		frappe.call({ method: method_prefix + "close" });
		call_action(frm, "close_task");
		api("make", {});
		frappe.xcall(`${METHOD}.make`);
		frappe.xcall(METHOD + ".open_tasks");
		frappe.xcall("run_doc_method", { dt: "Demo Task", dn: frm.doc.name, method: "finish" });
		frappe.call({ method: "run_doc_method", args: { docs: frm.doc, method: "archive" } });
		frappe.call({ module: "demo.demo", page: "task_board", method: "get_board" });
		fetch("/api/method/logout");
		frappe.xcall("demo.api.close");
	},
});
