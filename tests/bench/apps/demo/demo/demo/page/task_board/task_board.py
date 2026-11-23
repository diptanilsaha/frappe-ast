import frappe


@frappe.whitelist()
def get_board():
	return []
