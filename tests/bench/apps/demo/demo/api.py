import frappe
from frappe import _
from frappe.utils import cint
from demo.demo.doctype.demo_task.demo_task import close_task as close
from typing import overload


@frappe.whitelist()
def get_count(doctype):
	return frappe.db.count(doctype)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def open_tasks(status):
	frappe.db.set_value("Demo Task", "T-1", "status", status)
	return frappe.db.sql(f"select name from `tabDemo Task` where status = '{status}'")


def validate_any(doc, method=None):
	close(doc.name)
	frappe.throw("Plain message")


def on_submit_any(doc, method=None):
	for row in doc.items:
		frappe.get_doc("Demo Note", row.note)


def api_version(kind):
	from frappe.api import ApiVersion

	match kind:
		case ApiVersion.V1:
			return 1


def daily():
	frappe.enqueue("demo.api.validate_any")
	frappe.publish_realtime("task_done", {"ok": 1})


@overload
def make(x: int) -> int: ...


@frappe.whitelist()
def make(x):
	return x


def not_exposed():
	pass


def install():
	fields = [{"fieldname": "priority", "fieldtype": "Data"}]
	create_custom_fields({"Demo Task": fields})


def callback_url():
	return frappe.utils.get_url("/api/method/demo.api.open_tasks?status=Open")


def make_url(x):
	return f"/api/method/demo.api.make?x={x}"
