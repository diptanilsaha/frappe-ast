import frappe
from frappe.model.document import Document


class VirtualSettings(Document):
	def load_from_db(self):
		values = frappe._dict({"page_size": 50, "name": self.doctype})  # @load
		super(Document, self).__init__(values)

	def reset(self):
		self.update({"page_size": 20})  # @update
		self.db_set("page_size", 10)  # @db_set

	def keep(self):
		values = {"page_size": 5}
		values = self.defaults()
		super().__init__(values)  # rebound: the name no longer holds the literal

	def defaults(self):
		return {}


def make_task(owner):
	frappe.get_doc({"doctype": "Demo Task", "assigned_to": owner}).insert()  # @new_task


def unknown_doc(data):
	frappe.get_doc({"assigned_to": data}).insert()  # no doctype: no DocType to write to
