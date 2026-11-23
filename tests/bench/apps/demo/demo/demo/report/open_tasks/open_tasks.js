frappe.query_reports["Open Taskz"] = {
	filters: [{ fieldname: "note", fieldtype: "Link", options: "Demo Note" }],
};
frappe.set_route("query-report", "Open Tasks");
