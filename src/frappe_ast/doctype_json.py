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
	"depends_on",
	"mandatory_depends_on",
	"read_only_depends_on",
	"unique",
	"in_list_view",
	"allow_on_submit",
	"no_copy",
	"permlevel",
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

	@property
	def controller_path(self) -> str:
		return self.path.removesuffix(".json") + ".py"

	@property
	def form_js_path(self) -> str:
		return self.path.removesuffix(".json") + ".js"


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
	"track_changes",
	"editable_grid",
	"quick_entry",
	"is_calendar_and_gantt",
)


class DocTypeReader:
	def iter_app(self, app_name: str, package: Path, bench_root: Path):
		for path in package.glob("*/doctype/*/*.json"):
			if path.stem != path.parent.name:
				continue
			doc = self._load(path)
			if doc and doc.get("doctype") == "DocType" and doc.get("name"):
				yield self._build(doc, app_name, str(path.relative_to(bench_root)))

	def _load(self, path: Path) -> dict | None:
		try:
			return json.loads(path.read_text())
		except (json.JSONDecodeError, UnicodeDecodeError):
			return None

	def _build(self, doc: dict, app: str, path: str) -> DocTypeDef:
		return DocTypeDef(
			name=doc["name"],
			app=app,
			module=doc.get("module", ""),
			path=path,
			flags={k: doc[k] for k in FLAG_KEYS if doc.get(k)},
			fields=[
				{k: f[k] for k in FIELD_KEYS if f.get(k) not in (None, "", 0)} for f in doc.get("fields", [])
			],
			permissions=[_permission(p) for p in doc.get("permissions", [])],
		)


def _permission(perm: dict) -> dict:
	rights = [k for k in ("read", "write", "create", "delete", "submit", "cancel", "amend") if perm.get(k)]
	return {"role": perm.get("role", ""), "permlevel": perm.get("permlevel", 0), "rights": rights}
