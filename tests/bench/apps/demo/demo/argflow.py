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


class Finder:
	def __init__(self):
		self.rows = []

	def find(self, key, limit=None):
		self.limit = limit  # @store
		self.query()
		return self.rows

	def query(self):
		frappe.get_all("Demo Task", limit=self.limit)  # @stored_read


def echo(text):
	return text.strip()


def search(key, limit):
	rows = Finder().find(key, limit=limit)  # @find
	for row in rows:
		frappe.msgprint(row.title)  # @row
	frappe.msgprint(echo(key))  # @echo
	frappe.msgprint(str(limit))  # @builtin


def fill(name):
	filters = {}
	filters["name"] = name
	frappe.get_all("Demo Task", filters=filters)  # @filled


def build_query(start):
	query = frappe.qb.from_("Demo Task")
	return query.where(frappe.qb.Field("date") >= start)


def report(start):
	frappe.db.sql(build_query(start))  # @criterion


def make_row(title):
	row = frappe._dict()
	row.title = title
	return row


def show(title):
	frappe.msgprint(make_row(title))  # @made


class Query:
	def __init__(self, doctype):
		self.doctype = doctype

	def conditions(self):
		return f"`tab{self.doctype}`"


def match(doctype):
	frappe.db.sql(Query(doctype).conditions())  # @constructed


def group(rows):
	from collections import defaultdict

	grouped = defaultdict(list)
	for row in rows:
		grouped[row[0]].append(row)
	return grouped


def show_groups(rows):
	frappe.msgprint(group(rows))  # @grouped


class Cache:
	def fill(self, key, value):
		self.rows[key] = value

	def show(self):
		frappe.msgprint(self.rows)  # @cached


def countdown(n):
	yield -n


def show_countdown(n):
	frappe.msgprint(countdown(n))  # @yielded
