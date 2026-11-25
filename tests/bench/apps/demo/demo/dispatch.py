import frappe
from werkzeug.routing import Rule


class Engine:
	def __init__(self, doctype):
		self.doctype = doctype

	def run(self):
		return self.conditions()

	def conditions(self):
		return frappe.get_hooks("permission_query_conditions")


def use_engine():
	engine = Engine("Demo Task")
	return engine.run()


def use_engine_inline():
	return Engine("Demo Task").run()


def save_task(name):
	doc = frappe.get_doc("Demo Task", name)
	doc.save()


def submit_note(name):
	frappe.get_doc("Demo Note", name).submit()


def drop_task(name):
	frappe.delete_doc("Demo Task", name)


def unknown_doc(doctype, name):
	doc = frappe.get_doc(doctype, name)
	doc.save()


def rebound(name):
	engine = Engine("Demo Task")
	engine = make_engine()
	engine.run()


def make_engine():
	return None


def task_query(user):
	return ""


def long_sql():
	return frappe.db.sql("""select name
		from `tabDemo Task`""")


class Builder:
	pass


def build_query():
	return frappe.get_hooks("permission_query_conditions")


Builder.get_query = build_query

qb: LocalProxy["Builder"] = local("qb")


def use_proxy():
	return qb.get_query()


def route_handler():
	return frappe.db.sql("select 1")


url_rules = [Rule("/demo", endpoint=route_handler)]


def job():
	pass


def by_string():
	pass


def start_jobs():
	frappe.enqueue(job)
	frappe.enqueue("demo.dispatch.by_string")


def sandboxed():
	pass


def safe_globals():
	return {"run": sandboxed}


class Storage:
	def __init__(self):
		self.ready = True

	def store(self):
		return self.write()

	def write(self):
		raise NotImplementedError


class DiskStorage(Storage):
	def write(self):
		return frappe.db.sql("select 1")


def make_storage():
	return Storage()


def check_request():
	pass


def format_task(name):
	return name


def setup():
	pass


def configure():
	return {"handler": "demo.dispatch.setup"}


def run_handlers():
	return frappe.get_hooks("demo_handlers")


class Conn:
	@property
	def session(self):
		return self.open_session()

	@session.setter
	def session(self, value):
		self.close_session()

	def open_session(self):
		return "s"

	def close_session(self):
		pass

	def reuse(self):
		return self.session


def use_property():
	return Conn().session


def set_property():
	conn = Conn()
	conn.session = None


def read_plain():
	return Storage().ready


class Lock:
	def __enter__(self):
		self.acquire()
		return self

	def __exit__(self, *exc):
		self.release()

	async def __aenter__(self):
		return self

	async def __aexit__(self, *exc):
		pass

	def acquire(self):
		pass

	def release(self):
		pass


def use_with():
	with Lock():
		pass


def use_with_bound():
	lock = Lock()
	with lock:
		pass


async def use_async_with():
	async with Lock():
		pass


class Parser:
	def __init__(self, content):
		self.parse(content)

	def parse(self, content):
		pass

	def save(self):
		pass


class ChildParser(Parser):
	def __init__(self, content, extra):
		self.extra = extra
		super().__init__(content)

	def save(self):
		super(ChildParser, self).save()


class GrandParser(ChildParser):
	def parse(self, content):
		super().parse(content)
