"""The DocType JSON part of `doctype <Name>`: fields, permission rows, connections, actions, states."""

import json
from collections import Counter

from frappe_ast.store import Store

FIELD_FLAGS = (
	"reqd",
	"read_only",
	"hidden",
	"unique",
	"set_only_once",
	"allow_on_submit",
	"no_copy",
	"in_list_view",
	"in_standard_filter",
	"search_index",
	"ignore_user_permissions",
	"is_virtual",
	"fetch_if_empty",
)
FIELD_CONDITIONS = (
	"depends_on",
	"mandatory_depends_on",
	"read_only_depends_on",
	"collapsible_depends_on",
	"fetch_from",
	"link_filters",
	"default",
	"permlevel",
)


class DoctypeSchema:
	def __init__(self, store: Store):
		self.store = store

	def schema(self, name: str, with_fields: bool) -> dict:
		fields = self.store.rows("select * from fields where doctype = ? order by idx", name)
		data_fields = [f for f in fields if f["fieldname"]]
		schema = {
			"field_count": len(data_fields),
			"by_type": dict(Counter(f["fieldtype"] for f in data_fields).most_common()),
			"links": [f"{f['fieldname']} -> {f['options']}" for f in data_fields if f["fieldtype"] == "Link"],
			"child_tables": [
				f"{f['fieldname']} -> {f['options']}"
				for f in data_fields
				if f["fieldtype"] in ("Table", "Table MultiSelect")
			],
			"mandatory": [f["fieldname"] for f in data_fields if '"reqd": 1' in f["attrs"]],
			"permlevels": self._permlevels(data_fields),
		}
		if with_fields:
			schema["fields"] = [_field_line(f) for f in data_fields]
		return schema

	def permissions(self, name: str) -> list[str]:
		"""One line per permission row in the JSON, in file order. Custom DocPerm rows live in the database."""
		rows = self.store.rows("select * from permissions where doctype = ? order by idx", name)
		return [
			f"{r['role']} level {r['permlevel']}{' if_owner' if r['if_owner'] else ''}: {r['rights'] or '(none)'}"
			for r in rows
		]

	def links(self, name: str) -> dict:
		rows = self.store.rows("select * from doctype_links where doctype = ?", name)
		by_kind: dict[str, list[str]] = {}
		for r in rows:
			by_kind.setdefault(r["kind"], []).append(_link_line(r))
		return {
			"connections": by_kind.get("connection", []),
			"actions": by_kind.get("action", []),
			"states": by_kind.get("state", []),
		}

	def _permlevels(self, fields: list[dict]) -> dict:
		"""Fields behind a permlevel above 0: only roles with a row at that level can see or edit them."""
		levels: dict[str, list[str]] = {}
		for f in fields:
			level = json.loads(f["attrs"]).get("permlevel")
			if level:
				levels.setdefault(str(level), []).append(f["fieldname"])
		return {level: " ".join(names) for level, names in sorted(levels.items())}


def _field_line(f: dict) -> str:
	attrs = json.loads(f["attrs"])
	flags = [k for k in FIELD_FLAGS if attrs.get(k)]
	cond = {k: attrs[k] for k in FIELD_CONDITIONS if k in attrs}
	text = f"{f['fieldname']} {f['fieldtype']}"
	if f["options"]:
		text += f"[{f['options'][:60]}]"
	if flags:
		text += " " + ",".join(flags)
	if cond:
		text += " " + " ".join(f"{k}={v}" for k, v in cond.items())
	return text


def _link_line(r: dict) -> str:
	if r["kind"] == "connection":
		via = f" via {r['extra']}" if r["extra"] else ""
		group = f" [{r['grp']}]" if r["grp"] else ""
		return f"{r['target']}.{r['value']}{via}{group}"
	if r["kind"] == "action":
		return f"{r['target']}: {r['extra']} {r['value']}" + (f" [{r['grp']}]" if r["grp"] else "")
	return f"{r['target']} ({r['value']})" if r["value"] else r["target"]
