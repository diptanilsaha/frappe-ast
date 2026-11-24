import frappe
from demo.controllers.base_document import BaseDocument


class DemoTask(BaseDocument):
	def validate(self):
		self.set_title()

	def set_title(self):
		pass

	@frappe.whitelist()
	def finish(self):
		frappe.db.commit()

	def private_helper(self):
		pass

	def retire(self):
		self.archive()


@frappe.whitelist()
def close_task(name):
	frappe.get_doc("Demo Task", name).finish()
