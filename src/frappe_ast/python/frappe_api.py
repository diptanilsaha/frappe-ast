"""Frappe server APIs that name a DocType or a dotted method path.

Keys are absolute dotted names after import resolution, so
`from frappe import get_doc; get_doc("X")` matches too.
"""

import ast
import re

# API -> access kind
DOCTYPE_APIS = {
	"frappe.get_doc": "read",
	"frappe.get_cached_doc": "read",
	"frappe.get_last_doc": "read",
	"frappe.get_single": "read",
	"frappe.get_all": "read",
	"frappe.get_list": "read",
	"frappe.get_value": "read",
	"frappe.get_cached_value": "read",
	"frappe.get_meta": "meta",
	"frappe.get_single_value": "read",
	"frappe.has_permission": "permission",
	"frappe.db.get_value": "read",
	"frappe.db.get_values": "read",
	"frappe.db.get_all": "read",
	"frappe.db.get_list": "read",
	"frappe.db.exists": "read",
	"frappe.db.count": "read",
	"frappe.db.get_single_value": "read",
	"frappe.db.get_cached_value": "read",
	"frappe.db.get_singles_dict": "read",
	"frappe.qb.DocType": "query",
	"frappe.qb.from_": "query",
	"frappe.qb.get_query": "query",
	"frappe.query_builder.DocType": "query",
	"frappe.new_doc": "write",
	"frappe.copy_doc": "write",
	"frappe.db.set_value": "write",
	"frappe.db.set_single_value": "write",
	"frappe.rename_doc": "write",
	"frappe.model.rename_doc.rename_doc": "write",
	"frappe.delete_doc": "delete",
	"frappe.delete_doc_if_exists": "delete",
	"frappe.db.delete": "delete",
	"frappe.model.mapper.get_mapped_doc": "map",
}

# APIs whose first argument is a dotted path to a Python callable.
DOTTED_APIS = {
	"frappe.enqueue",
	"frappe.get_attr",
	"frappe.call",
	"frappe.utils.background_jobs.enqueue",
}

# Document lifecycle methods the framework calls by name.
LIFECYCLE_METHODS = [
	"autoname",
	"before_naming",
	"before_insert",
	"after_insert",
	"before_validate",
	"validate",
	"before_save",
	"on_update",
	"before_submit",
	"on_submit",
	"before_update_after_submit",
	"on_update_after_submit",
	"before_cancel",
	"on_cancel",
	"on_trash",
	"after_delete",
	"on_change",
	"before_rename",
	"after_rename",
	"before_print",
	"onload",
	"before_discard",
	"on_discard",
	"db_insert",
	"db_update",
	"get_list",
	"get_count",
	"get_stats",
	"load_from_db",
]

# Mapped doc: get_mapped_doc(from_doctype, name, {"From DT": {"doctype": "To DT"}})
_FIRST_ARG_APIS_WITH_KWARG = {"doctype", "dt"}


def doctype_arg(node: ast.Call, api: str) -> str:
	"""Return the DocType name a call targets, or "" when it is not a literal."""
	first = node.args[0] if node.args else None
	if first is None:
		first = next((kw.value for kw in node.keywords if kw.arg in _FIRST_ARG_APIS_WITH_KWARG), None)
	if isinstance(first, ast.Constant) and isinstance(first.value, str):
		return first.value
	if isinstance(first, ast.Dict) and api in ("frappe.get_doc", "frappe.new_doc"):
		return dict_doctype(first)
	return ""


def dict_doctype(node: ast.Dict) -> str:
	for key, value in zip(node.keys, node.values):
		if isinstance(key, ast.Constant) and key.value == "doctype" and isinstance(value, ast.Constant):
			return str(value.value)
	return ""


# Calls that set a document's fields by name: each string key of the dict, or the name, is a
# write to that field. Methods on the document (`super().__init__(d)` in a virtual DocType's
# load_from_db, `self.update(d)`, `doc.db_set("status", s)`), and APIs whose dict names its
# DocType under "doctype" (`frappe.get_doc({"doctype": "ToDo", ...})`).
FIELD_DICT_METHODS = {"__init__", "update", "db_set"}
FIELD_NAME_METHODS = {"db_set", "set"}
FIELD_DICT_APIS = {"frappe.get_doc"}
DICT_BUILDERS = {"dict", "frappe._dict"}  # dict({...}) holds the keys of its literal


# Server -> browser events; the JS side listens with frappe.realtime.on(event).
REALTIME_APIS = {"frappe.publish_realtime", "frappe.realtime.publish_realtime"}


def realtime_event(node: ast.Call) -> str:
	"""The `event` of publish_realtime(event, message, ...), positional or keyword."""
	value = node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == "event"), None)
	return value.value if isinstance(value, ast.Constant) and isinstance(value.value, str) else ""


# A server method inside a URL: "/api/method/app.api.x?y=1", get_url("/api/method/..."), and the
# constant part of f"/api/method/app.api.x?y={y}". A path cut by an interpolation does not match.
# A dotted path to a function of a bench app: at least module.module.name.
DOTTED_RE = re.compile(r"^[a-z_][a-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*){2,}$")

API_URL_RE = re.compile(r"(?:^|/)api/(?:v2/)?method/([a-z_]\w*(?:\.\w+)+)(?=$|[?#/])")


# Calls that return a document of the DocType named by their first argument. A variable bound to
# one (`doc = frappe.get_doc("Sales Order", name)`) is a document of that DocType.
DOC_LOADER_APIS = (
	"frappe.get_doc",
	"frappe.get_cached_doc",
	"frappe.get_lazy_doc",
	"frappe.get_last_doc",
	"frappe.new_doc",
	"frappe.get_single",
)

# What the document layer runs, by the Document method (or frappe API) that starts it. From
# frappe/model/document.py (insert, run_before_save_methods, run_post_save_methods, discard),
# delete_doc.py and rename_doc.py. Each name is a controller method and a doc_events event.
LIFECYCLE_EVENTS = {
	"insert": (
		"before_insert",
		"before_validate",
		"validate",
		"before_save",
		"on_update",
		"on_change",
		"after_insert",
	),
	"save": (
		"before_validate",
		"validate",
		"before_save",
		"before_update_after_submit",
		"on_update",
		"on_update_after_submit",
		"on_change",
	),
	"submit": ("validate", "before_submit", "on_update", "on_submit", "on_change"),
	"cancel": ("before_cancel", "on_cancel", "on_change"),
	"delete": ("on_trash", "on_change", "after_delete"),
	"rename": ("before_rename", "after_rename"),
	"discard": ("before_discard", "on_discard"),
}

# frappe APIs that run a DocType's lifecycle for the DocType named by their first argument.
LIFECYCLE_APIS = {"frappe.delete_doc": "delete", "frappe.rename_doc": "rename"}

# Reading hooks.py at run time: a function that calls one of these with a literal key dispatches
# to every callback registered under that key.
HOOK_READER_APIS = ("frappe.get_hooks",)

# What makes a function reachable from outside the process, by how it gets there. A function is a
# root (an entry point) when one of these applies; see queries/surface.py.
ROUTE_APIS = ("werkzeug.routing.Rule", "werkzeug.routing.rules.Rule")  # Rule(path, endpoint=fn)
REQUEST_ROOTS = ("frappe.app.application",)  # the WSGI entry: auth, session, CSRF, routing
WWW_DIRS = ("/www/", "/templates/pages/")  # get_context of a website route
SURFACE_HOOKS = {
	"doc_events": "doc_event",
	"scheduler_events": "scheduler",
	"has_permission": "permission",
	"permission_query_conditions": "permission",
	"has_website_permission": "permission",
	"before_request": "request",
	"after_request": "request",
	"auth_hooks": "request",
	"on_session_creation": "request",
	"on_login": "request",
	"on_logout": "request",
	"extend_bootinfo": "request",
	"boot_session": "request",
	"website_context": "request",
	"update_website_context": "request",
	"website_path_resolver": "request",
	"page_renderer": "request",
	"get_website_user_home_page": "request",
	"role_home_page": "request",
	"override_whitelisted_methods": "api",
	"standard_queries": "api",
	"jinja": "template",
	"webform_list_context": "www",
}  # any other hook value that names a function is a root of kind "hook"


# Request input, for argument flow (python/flows.py): an attribute path that holds what the client
# sent, by label, and calls that return a part of it (a header by name, the JSON body).
REQUEST_SOURCES = {
	"frappe.form_dict": "form_dict",
	"frappe.local.form_dict": "form_dict",
	"frappe.request": "request",
	"frappe.local.request": "request",
}
REQUEST_SOURCE_CALLS = {
	"frappe.get_request_header": "request.headers",
	"frappe.request.get_json": "request.json",
	"frappe.request.get_data": "request.data",
}
