import frappe
from frappe.model.document import Document


class BaseDocument(Document):
	@frappe.whitelist()
	def archive(self):
		pass
