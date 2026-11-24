import frappe
from frappe.utils import cint


@frappe.whitelist()
def load(doctype, name, **kwargs):
	doc = frappe.get_doc(doctype, name)  # @loaded
	doc.update(kwargs)  # @update
	limit = cint(frappe.form_dict.get("limit"))
	frappe.db.sql(f"select name from `tab{doctype}` limit {limit}")  # @sql
	frappe.get_list("Demo Task", filters={"name": name})  # @filters
	if name:
		target = name
	else:
		target = "T-1"
	frappe.db.get_value("Demo Task", target)  # @branch
	for row in kwargs.get("rows"):
		frappe.get_doc("Demo Note", row)  # @loop
	if isinstance(name, str):
		frappe.db.exists("Demo Task", name.strip())  # @method
	name = "fixed"
	frappe.db.get_value("Demo Note", name)  # @rebound


def from_request():
	host = frappe.get_request_header("Host")
	frappe.cache.set_value("host", host)  # @header
	frappe.db.get_value("Demo Task", frappe.form_dict["task"])  # @form_key


class Loader:
	def fetch(self, url):
		return frappe.make_get_request(url)  # @fetch


@frappe.whitelist()
def outer(name, **kwargs):
	inner(name.strip(), limit=5)  # @outer
	Loader().fetch(kwargs.get("url"))


def inner(docname, limit=None):
	frappe.get_doc("Demo Task", docname)  # @inner
	remove(docname)


def remove(task):
	frappe.db.delete("Demo Task", task)  # @remove
