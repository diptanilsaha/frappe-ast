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
		return _dict_doctype(first)
	return ""


def _dict_doctype(node: ast.Dict) -> str:
	for key, value in zip(node.keys, node.values):
		if isinstance(key, ast.Constant) and key.value == "doctype" and isinstance(value, ast.Constant):
			return str(value.value)
	return ""


# Server -> browser events; the JS side listens with frappe.realtime.on(event).
REALTIME_APIS = {"frappe.publish_realtime", "frappe.realtime.publish_realtime"}


def realtime_event(node: ast.Call) -> str:
	"""The `event` of publish_realtime(event, message, ...), positional or keyword."""
	value = node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == "event"), None)
	return value.value if isinstance(value, ast.Constant) and isinstance(value.value, str) else ""


# A server method inside a URL: "/api/method/app.api.x?y=1", get_url("/api/method/..."), and the
# constant part of f"/api/method/app.api.x?y={y}". A path cut by an interpolation does not match.
API_URL_RE = re.compile(r"(?:^|/)api/(?:v2/)?method/([a-z_]\w*(?:\.\w+)+)(?=$|[?#/])")
