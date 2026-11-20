"""`frappe-ast record <name>`: a standard Report, Page, Web Form, ... and the code beside its JSON.

FX208 (check): the wiring between the JSON and its code is broken.
  - A standard Script Report whose .py has no module-level execute().
  - A report .js that registers frappe.query_reports under a name other than the report's.
  - A page .js that registers frappe.pages under a name other than the page's.
"""

import re
from pathlib import PurePath

from frappe_ast.queries.finding import finding
from frappe_ast.records import RECORD_KINDS
from frappe_ast.store import Store

KIND_MARKS = ",".join(f"'{k}'" for k in RECORD_KINDS)
REGISTRY_KIND = {"report": "report", "page": "page"}  # record kind -> registry ref kind in its .js


class RecordReport:
	def __init__(self, store: Store, limit: int = 20):
		self.store = store
		self.limit = limit

	def run(self, name: str, kind: str = "") -> dict:
		matches = self.find(name, kind)
		if not matches:
			return {"query": name, "found": False, "similar": self._similar(name)}
		record = matches[0]
		folder = str(PurePath(record["path"]).parent)
		return {
			"query": name,
			"found": True,
			"record": f"{record['kind']} {record['name']} {record['signature']}  {record['path']}",
			"other_matches": [f"{m['kind']} {m['name']}  {m['path']}" for m in matches[1:]],
			"code": self._code(folder, record["path"]),
			"targets": self._targets(record),
			"linked_from": self._linked_from(record),
			"problems": [f["message"] for f in RecordChecks(self.store).for_record(record)],
		}

	def find(self, name: str, kind: str = "") -> list[dict]:
		rows = self.store.rows(
			"select s.name, s.kind, s.signature, f.path, f.app from symbols s join files f on f.id = s.file_id "
			f"where f.lang = 'record' and s.kind in ({KIND_MARKS}) and lower(s.name) = lower(?) "
			"and (? = '' or s.kind = ?) order by s.name = ? desc",
			name,
			kind,
			kind,
			name,
		)
		return rows

	def _code(self, folder: str, json_path: str) -> list[str]:
		"""Sibling files with their top-level functions and registrations."""
		out = []
		for row in self.store.rows(
			"select id, path, lang from files where path like ? and path != ? and path not like ? order by path",
			folder + "/%",
			json_path,
			folder + "/%/%",
		):
			names = [
				f"{s['name']}:{s['line']}"
				for s in self.store.rows(
					"select name, line from symbols where file_id = ? and parent = '' "
					"and kind in ('function', 'class', 'alias') order by line",
					row["id"],
				)
			]
			regs = [
				f"{r['kind']}[{r['target']}]:{r['line']}"
				for r in self.store.rows(
					"select kind, target, line from refs where file_id = ? and kind in ('report', 'page')",
					row["id"],
				)
			]
			out.append(f"{row['path']}  " + " ".join(regs + names[: self.limit]))
		return out

	def _targets(self, record: dict) -> list[str]:
		# The JSON's targets, plus Link filters in the report's .js (frappe.query_reports[...].filters).
		rows = self.store.rows(
			"select distinct r.kind, r.target from refs r join files f on f.id = r.file_id "
			"where ((f.path = ? and r.kind in ('doctype', 'record_link')) "
			"or (f.path = ? and r.kind = 'doctype' and r.access = 'filter')) order by r.kind, r.target limit ?",
			record["path"],
			record["path"].removesuffix(".json") + ".js",
			self.limit,
		)
		return [f"{r['kind']} {r['target']}" for r in rows]

	def _linked_from(self, record: dict) -> list[str]:
		rows = self.store.rows(
			"select f.path, r.line, r.via from refs r join files f on f.id = r.file_id "
			"where r.kind = 'record_link' and r.target = ? order by f.path limit ?",
			f"{record['kind']}:{record['name']}",
			self.limit,
		)
		return [f"{r['path']}:{r['line']} {r['via']}" for r in rows]

	def _similar(self, name: str) -> list[str]:
		rows = self.store.rows(
			f"select s.kind, s.name from symbols s where s.kind in ({KIND_MARKS}) and s.name like ? limit 10",
			f"%{name}%",
		)
		return [f"{r['kind']} {r['name']}" for r in rows]


class RecordChecks:
	def __init__(self, store: Store):
		self.store = store

	def run(self, prefix: str) -> list[dict]:
		out = []
		for record in self.store.rows(
			"select s.name, s.kind, s.signature, f.path from symbols s join files f on f.id = s.file_id "
			"where f.lang = 'record' and s.kind in ('report', 'page') and f.path like ?",
			prefix + "%",
		):
			out += self.for_record(record)
		return out

	def for_record(self, record: dict) -> list[dict]:
		out = []
		base = record["path"].removesuffix(".json")
		if (
			record["kind"] == "report"
			and _attr(record, "report_type") == "Script Report"
			and _attr(record, "is_standard") == "Yes"
		):
			py = self._file(base + ".py")
			if py is None:
				out.append(
					_fx208(f"Script Report {record['name']} has no {PurePath(base).name}.py", record["path"])
				)
			elif not self._has_function(py, "execute"):
				out.append(
					_fx208(f"Script Report {record['name']}: {py['path']} defines no execute()", py["path"])
				)
		js = self._file(base + ".js")
		kind = REGISTRY_KIND.get(record["kind"])
		if js is not None and kind:
			keys = self._registrations(js, kind)
			expected = {record["name"], _attr(record, "page_name")} - {""}
			for key, line in keys:
				if key not in expected:
					msg = f"{js['path']} registers {kind} '{key}', but the {record['kind']} is '{record['name']}'"
					out.append(finding("FX208", "error", msg, js["path"], line))
		return out

	def _file(self, path: str) -> dict | None:
		rows = self.store.rows("select id, path from files where path = ?", path)
		return rows[0] if rows else None

	def _has_function(self, file: dict, name: str) -> bool:
		return bool(self.store.rows(
			"select 1 from symbols where file_id = ? and name = ? and parent = '' and kind in ('function', 'alias')",
			file["id"], name,
		))  # fmt: skip

	def _registrations(self, file: dict, kind: str) -> list[tuple[str, int]]:
		rows = self.store.rows(
			"select target, line from refs where file_id = ? and kind = ?", file["id"], kind
		)
		return [(r["target"], r["line"]) for r in rows]


def _attr(record: dict, key: str) -> str:
	match = re.search(rf"\b{key}=([^,)]*)", record.get("signature") or "")
	return match.group(1).strip() if match else ""


def _fx208(message: str, path: str) -> dict:
	return finding("FX208", "error", message, path, 1)
