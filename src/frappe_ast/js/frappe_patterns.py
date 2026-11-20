"""Frappe client-side API patterns: desk (frappe.*) and frappe-ui (Vue apps)."""

import re

# Calls whose first argument, or `method`/`url` key, is a server method path.
SERVER_CALLS = {
	"frappe.call",
	"frappe.xcall",
	"frappe.model.open_mapped_doc",
	"createResource",
	"useCall",
	"call",
	"frappeRequest",
	"fetch",
	"$.ajax",
}
# DocType resources whose optional `url` swaps in a custom server method.
URL_OPTION_CALLS = {"createListResource", "createDocumentResource", "useList", "useDoc"}
# frm.call("method") runs a whitelisted method on the form's controller.
DOC_METHOD_CALLS = {"frm.call", "this.frm.call", "cur_frm.call", "me.frm.call", "frm.doc.call"}

# Calls whose first argument, or `doctype` key, is a DocType name. Value is access kind.
DOCTYPE_CALLS = {
	"frappe.db.get_value": "read",
	"frappe.db.get_list": "read",
	"frappe.db.get_doc": "read",
	"frappe.db.count": "read",
	"frappe.db.exists": "read",
	"frappe.db.get_single_value": "read",
	"frappe.db.set_value": "write",
	"frappe.db.insert": "write",
	"frappe.db.delete_doc": "delete",
	"frappe.new_doc": "write",
	"frappe.get_list": "read",
	"frappe.get_doc": "read",
	"frappe.get_meta": "meta",
	"frappe.model.with_doctype": "meta",
	"frappe.model.get_new_doc": "write",
	"frappe.model.get_doc": "read",
	"frappe.model.add_child": "write",
	"frappe.views.list_view": "render",
	"createListResource": "read",
	"createDocumentResource": "read",
	"useList": "read",
	"useDoc": "read",
	"useDoctype": "read",
	"useNewDoc": "write",
	"getMeta": "meta",
}
ROUTE_CALLS = {"frappe.set_route", "frappe.router.set_route"}
FORM_EVENT_CALLS = {"frappe.ui.form.on"}
# frappe.listview_settings["X"] = {...} and friends.
REGISTRIES = {
	"frappe.listview_settings": "listview",
	"frappe.query_reports": "report",
	"frappe.treeview_settings": "treeview",
	"frappe.views.calendar": "calendar",
	"frappe.dashboards.chart_sources": "chart_source",
	"frappe.pages": "page",
}

_API_PREFIX = re.compile(r"^/?api/(v2/)?method/")


def normalize_method(path: str) -> str:
	"""'/api/v2/method/gameplan.api.x' -> 'gameplan.api.x'; drops query strings."""
	path = _API_PREFIX.sub("", path.strip())
	return path.split("?")[0]


def looks_like_method(path: str) -> bool:
	"""A dotted path, or an /api/method URL. `/api/v2/method/<DocType>/x` is not: see URL_METHODS."""
	if re.match(r"^[a-z_][\w]*(\.[\w]+)+$", path):
		return True
	return bool(_API_PREFIX.match(path)) and re.match(r"^[\w.]+$", normalize_method(path)) is not None


# frappe-ui calls that name a DocType and run its controller methods (see js/frappe_ui.py).
# Option holding {key: method} and the key that names the method when the value is an object.
DOC_METHOD_MAPS = {"useDoc": ("methods", "name"), "createDocumentResource": ("whitelistedMethods", "method")}
# useDoctype(X).<member>.submit({method}) and the ref kind it makes.
DOCTYPE_HANDLES = {"useDoctype"}
DOCTYPE_RUNNERS = {"runDocMethod": "doc_method", "runMethod": "module_method"}
# REST API v2 URLs, `*` standing for a template substitution.
URL_METHODS = (
	(re.compile(r"/api/v2/document/([^/*]+)/[^/]+/method/(\w+)"), "doc_method"),
	(re.compile(r"/api/v2/method/([^/.*]+)/(\w+)"), "module_method"),
)

# Server methods that take the DocType as data, in params / args / doc. Value is access kind.
CLIENT_METHODS = {
	"frappe.client.get_list": "read",
	"frappe.client.get_count": "read",
	"frappe.client.get": "read",
	"frappe.client.get_value": "read",
	"frappe.client.get_single_value": "read",
	"frappe.client.set_value": "write",
	"frappe.client.insert": "write",
	"frappe.client.insert_many": "write",
	"frappe.client.save": "write",
	"frappe.client.rename_doc": "write",
	"frappe.client.submit": "write",
	"frappe.client.cancel": "write",
	"frappe.client.delete": "delete",
	"frappe.client.bulk_update": "write",
	"frappe.client.get_password": "read",
	"frappe.client.has_permission": "read",
	"frappe.desk.reportview.get": "read",
	"frappe.desk.reportview.get_count": "read",
	"frappe.desk.form.load.getdoc": "read",
}
# Keys next to `doctype` that name its fields: arrays of names, a name, or an object keyed by name.
FIELD_KEYS = ("fields", "fieldname", "field", "filters", "orderBy", "order_by", "groupBy", "group_by")
# Variables that hold a method path at run time, not a constant: `url: props.url`.
DYNAMIC_ROOTS = {"props", "options", "opts", "this", "args", "params", "config", "settings"}
# Methods that take a whole doc: every key of it is a field.
DOC_FIELD_METHODS = {"frappe.client.insert", "frappe.client.save", "frappe.client.submit"}
NON_FIELD_KEYS = {"doctype", "name", "__islocal", "__unsaved", "docstatus"}
# frappe.db.get_value("X", name, "field"): position of the fieldname argument.
FIELD_ARGS = {"frappe.db.get_value": 2, "frappe.db.set_value": 2, "frappe.db.get_single_value": 1}

# Desk forms (see js/desk.py). The receivers that hold the current form.
FORM_OBJECTS = {"frm", "this.frm", "cur_frm", "me.frm", "this.form", "listview.frm"}
# frm.<method>(fieldname, ...) and the access kind. A list or {field: value} works too.
FORM_FIELD_CALLS = {
	"set_value": "write",
	"set_query": "read",
	"set_df_property": "meta",
	"toggle_display": "meta",
	"toggle_reqd": "meta",
	"toggle_enable": "meta",
	"refresh_field": "read",
	"get_field": "read",
	"add_fetch": "read",
}
# frm.<method>(field, table_field, ...): a field of the child table, given second.
CHILD_FIELD_CALLS = {"set_query"}
# Ways a form script installs its JS controller class as frm.cscript.
CONTROLLER_CALLS = {"cur_frm.script_manager.make", "frm.script_manager.make", "this.frm.script_manager.make"}
CSCRIPT_EXTENDS = {"extend_cscript", "$.extend", "jQuery.extend", "Object.assign"}
CSCRIPT_OBJECTS = {"cur_frm.cscript", "frm.cscript", "this.frm.cscript", "me.frm.cscript"}
# Server -> browser events. Python publishes with frappe.publish_realtime.
REALTIME_LISTEN = {"frappe.realtime.on", "frappe.realtime.off"}
