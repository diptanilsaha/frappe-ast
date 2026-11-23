"""Read DocType definitions from <module>/doctype/<name>/<name>.json."""

import json
from dataclasses import dataclass, field
from pathlib import Path

LINK_TYPES = {"Link", "Dynamic Link", "Table", "Table MultiSelect"}
FIELD_KEYS = (
	"fieldname",
	"fieldtype",
	"label",
	"options",
	"reqd",
	"read_only",
	"hidden",
	"default",
	"fetch_from",
	"fetch_if_empty",
	"depends_on",
	"mandatory_depends_on",
	"read_only_depends_on",
	"collapsible_depends_on",
	"link_filters",
	"unique",
	"set_only_once",
	"in_list_view",
	"in_standard_filter",
	"in_global_search",
	"search_index",
	"allow_on_submit",
	"no_copy",
	"ignore_user_permissions",
	"is_virtual",
	"translatable",
	"non_negative",
	"length",
	"precision",
	"permlevel",
	"description",
)
# DocType-level properties of the JSON. List-valued parts (links, actions, states) are read separately.
FLAG_KEYS = (
	"istable",
	"issingle",
	"is_submittable",
	"is_tree",
	"is_virtual",
	"custom",
	"autoname",
	"naming_rule",
	"title_field",
	"show_title_field_in_link",
	"search_fields",
	"sort_field",
	"sort_order",
	"image_field",
	"timeline_field",
	"nsm_parent_field",
	"document_type",
	"track_changes",
	"track_seen",
	"track_views",
	"editable_grid",
	"quick_entry",
	"is_calendar_and_gantt",
	"allow_rename",
	"allow_import",
	"allow_copy",
	"allow_auto_repeat",
	"allow_events_in_timeline",
	"in_create",
	"read_only",
	"hide_toolbar",
	"max_attachments",
	"make_attachments_public",
	"protect_attached_files",
	"queue_in_background",
	"show_name_in_global_search",
	"translated_doctype",
	"default_print_format",
	"restrict_to_domain",
	"email_append_to",
	"sender_field",
	"subject_field",
	"has_web_view",
	"allow_guest_to_view",
	"route",
	"is_published_field",
	"beta",
)
RIGHTS = (
	"select",
	"read",
	"write",
	"create",
	"delete",
	"submit",
	"cancel",
	"amend",
	"report",
	"export",
	"import",
	"share",
	"print",
	"email",
)


@dataclass
class DocTypeDef:
	name: str
	app: str
	module: str
	path: str
	flags: dict = field(default_factory=dict)  # istable, issingle, is_submittable, ...
	fields: list[dict] = field(default_factory=list)
	permissions: list[dict] = field(default_factory=list)
	links: list[dict] = field(default_factory=list)  # dashboard connections, actions, states

	@property
	def controller_path(self) -> str:
		return self.path.removesuffix(".json") + ".py"

	@property
	def form_js_path(self) -> str:
		return self.path.removesuffix(".json") + ".js"


class DocTypeReader:
	def iter_app(self, app_name: str, package: Path, bench_root: Path):
		for path in package.glob("*/doctype/*/*.json"):
			if path.stem != path.parent.name:
				continue
			text = self._read(path)
			doc = self._load(text)
			if isinstance(doc, dict) and doc.get("doctype") == "DocType" and doc.get("name"):
				yield self._build(doc, app_name, str(path.relative_to(bench_root)), text)

	def _read(self, path: Path) -> str:
		try:
			return path.read_text()
		except UnicodeDecodeError:
			return ""

	def _load(self, text: str) -> dict | None:
		try:
			return json.loads(text)
		except json.JSONDecodeError:
			return None

	def _build(self, doc: dict, app: str, path: str, text: str) -> DocTypeDef:
		fields = [
			{k: f[k] for k in FIELD_KEYS if f.get(k) not in (None, "", 0)} for f in doc.get("fields", [])
		]
		_add_lines(fields, text)
		return DocTypeDef(
			name=doc["name"],
			app=app,
			module=doc.get("module", ""),
			path=path,
			flags={k: doc[k] for k in FLAG_KEYS if doc.get(k)},
			fields=fields,
			permissions=[_permission(p) for p in doc.get("permissions", [])],
			links=_links(doc),
		)


def _add_lines(fields: list[dict], text: str):
	"""Each field's line in the JSON, so a Button field can be reported as path:line."""
	at = 0
	for f in fields:
		found = text.find(f'"fieldname": {json.dumps(f.get("fieldname", ""))}', at)
		if found >= 0:
			f["line"], at = text.count("\n", 0, found) + 1, found


def _permission(perm: dict) -> dict:
	return {
		"role": perm.get("role", ""),
		"permlevel": perm.get("permlevel") or 0,
		"if_owner": int(bool(perm.get("if_owner"))),
		"rights": [k for k in RIGHTS if perm.get(k)],
	}


def _links(doc: dict) -> list[dict]:
	"""Connections (`links`), form `actions` and list-view `states`, as one row kind each."""
	rows = [
		{
			"kind": "connection",
			"target": link.get("link_doctype", ""),
			"value": link.get("link_fieldname", ""),
			"group": link.get("group", ""),
			"extra": link.get("table_fieldname") or link.get("parent_doctype") or "",
		}
		for link in doc.get("links") or []
		if not link.get("hidden")
	]
	rows += [
		{
			"kind": "action",
			"target": action.get("label", ""),
			"value": action.get("action", ""),
			"group": action.get("group", ""),
			"extra": action.get("action_type", ""),
		}
		for action in doc.get("actions") or []
		if not action.get("hidden")
	]
	rows += [
		{
			"kind": "state",
			"target": state.get("title", ""),
			"value": state.get("color", ""),
			"group": "",
			"extra": "",
		}
		for state in doc.get("states") or []
	]
	return rows
