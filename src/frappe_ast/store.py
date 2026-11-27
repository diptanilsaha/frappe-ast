"""SQLite index of one bench. One row per extracted fact."""

import hashlib
import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from frappe_ast.model import FileFacts

SCHEMA_VERSION = "8"
SCHEMA = """
create table if not exists meta (key text primary key, value text);
create table if not exists files (
	id integer primary key, path text unique, app text, lang text, module text,
	mtime real, size integer, error text
);
create table if not exists symbols (
	file_id integer, name text, qualname text, kind text, line integer, end_line integer,
	signature text, parent text, doc text, decorators text,
	whitelisted integer, allow_guest integer, http_methods text
);
create table if not exists calls (
	file_id integer, caller text, callee text, resolved text, line integer, receiver text, arg text,
	col integer
);
create table if not exists refs (
	file_id integer, kind text, target text, via text, line integer, scope text, access text,
	col integer
);
create table if not exists flows (
	file_id integer, caller text, callee text, resolved text, line integer, arg text, source text,
	via text, col integer, returns text
);
create table if not exists findings (
	file_id integer, rule text, severity text, message text, line integer, scope text
);
create table if not exists hooks (app text, key text, p1 text, p2 text, p3 text, value text, line integer);
create table if not exists doctypes (name text, app text, module text, path text, flags text);
create table if not exists fields (
	doctype text, idx integer, fieldname text, fieldtype text, label text, options text, attrs text
);
create table if not exists permissions (
	doctype text, idx integer, role text, permlevel integer, if_owner integer, rights text
);
create table if not exists doctype_links (
	doctype text, kind text, target text, value text, grp text, extra text
);
create index if not exists ix_hooks_value on hooks(value);
create index if not exists ix_fields_dt on fields(doctype);
create index if not exists ix_fields_opt on fields(options);
create index if not exists ix_perms_dt on permissions(doctype);
create index if not exists ix_perms_role on permissions(role);
create index if not exists ix_dtlinks_dt on doctype_links(doctype);
create index if not exists ix_dtlinks_target on doctype_links(kind, target);
"""
FACT_TABLES = ("symbols", "calls", "refs", "flows", "findings")
FACT_INDEXES = """
create index if not exists ix_sym_qual on symbols(qualname);
create index if not exists ix_sym_name on symbols(name);
create index if not exists ix_sym_file on symbols(file_id);
create index if not exists ix_calls_callee on calls(callee);
create index if not exists ix_calls_resolved on calls(resolved);
create index if not exists ix_calls_file on calls(file_id);
create index if not exists ix_calls_caller on calls(caller);
create index if not exists ix_refs_target on refs(kind, target);
create index if not exists ix_refs_file on refs(file_id);
create index if not exists ix_refs_scope on refs(scope);
create index if not exists ix_flows_caller on flows(caller);
create index if not exists ix_findings_file on findings(file_id);
"""
DOCTYPE_TABLES = ("fields", "permissions", "doctype_links")


class Store:
	def __init__(self, path: Path):
		path.parent.mkdir(parents=True, exist_ok=True)
		self.path = path
		self.db = sqlite3.connect(path)
		self.db.row_factory = sqlite3.Row
		self.db.execute("pragma journal_mode=wal")
		self._migrate()

	@classmethod
	def for_bench(cls, bench_root: Path, override: str | None = None) -> "Store":
		if override or os.environ.get("FRAPPE_AST_DB"):
			return cls(Path(override or os.environ["FRAPPE_AST_DB"]).expanduser())
		digest = hashlib.sha1(str(bench_root).encode()).hexdigest()[:8]
		cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "frappe-ast"
		return cls(cache / f"{bench_root.name}-{digest}.sqlite")

	def rows(self, sql: str, *params) -> list[dict]:
		return [dict(r) for r in self.db.execute(sql, params)]

	def iter_rows(self, sql: str, *params) -> Iterator[dict]:
		"""Rows one at a time, for scans of a whole table that `rows` would hold in memory at once."""
		for r in self.db.execute(sql, params):
			yield dict(r)

	def file_stamps(self) -> dict[str, tuple[int, float, int, str]]:
		return {
			r["path"]: (r["id"], r["mtime"], r["size"], r["app"])
			for r in self.db.execute("select * from files")
		}

	def replace_file(self, facts: FileFacts, app: str, mtime: float, size: int):
		self.delete_file(facts.path)
		cur = self.db.execute(
			"insert into files (path, app, lang, module, mtime, size, error) values (?,?,?,?,?,?,?)",
			(facts.path, app, facts.lang, facts.module, mtime, size, facts.error),
		)
		self._insert_facts(cur.lastrowid, facts)

	@contextmanager
	def bulk(self):
		"""Load facts into an empty index with the fact indexes dropped, then build each once:
		cheaper than updating every index per row."""
		for name in _index_names(FACT_INDEXES):
			self.db.execute(f"drop index if exists {name}")
		try:
			yield
		finally:
			self.db.executescript(FACT_INDEXES)

	def delete_file(self, path: str):
		row = self.db.execute("select id from files where path = ?", (path,)).fetchone()
		if not row:
			return
		for table in FACT_TABLES:
			self.db.execute(f"delete from {table} where file_id = ?", (row["id"],))
		self.db.execute("delete from files where id = ?", (row["id"],))

	def clear_files(self):
		for table in ("files", *FACT_TABLES):
			self.db.execute(f"delete from {table}")

	def replace_app_meta(self, app: str, hooks: list, doctypes: list):
		self.db.execute("delete from hooks where app = ?", (app,))
		self._delete_doctypes("app = ?", [app])
		self.db.executemany(
			"insert into hooks values (?,?,?,?,?,?,?)",
			[(h.app, h.key, *_pad(h.path, 3), h.value, h.line) for h in hooks],
		)
		for dt in doctypes:
			self.db.execute(
				"insert into doctypes values (?,?,?,?,?)",
				(dt.name, dt.app, dt.module, dt.path, json.dumps(dt.flags)),
			)
			self.db.executemany(
				"insert into fields values (?,?,?,?,?,?,?)",
				[_field_row(dt.name, i, f) for i, f in enumerate(dt.fields)],
			)
			self.db.executemany(
				"insert into permissions values (?,?,?,?,?,?)",
				[
					(dt.name, i, p["role"], p["permlevel"], p["if_owner"], " ".join(p["rights"]))
					for i, p in enumerate(dt.permissions)
				],
			)
			self.db.executemany(
				"insert into doctype_links values (?,?,?,?,?,?)",
				[(dt.name, x["kind"], x["target"], x["value"], x["group"], x["extra"]) for x in dt.links],
			)

	def drop_apps_except(self, apps: list[str]):
		"""Forget apps (and library labels) that are no longer in the bench."""
		marks = ",".join("?" * len(apps))
		for path in [
			r["path"] for r in self.rows(f"select path from files where app not in ({marks})", *apps)
		]:
			self.delete_file(path)
		self.db.execute(f"delete from hooks where app not in ({marks})", apps)
		self._delete_doctypes(f"app not in ({marks})", apps)

	def set_meta(self, key: str, value: str):
		self.db.execute("insert or replace into meta values (?, ?)", (key, value))

	def get_meta(self, key: str) -> str | None:
		row = self.db.execute("select value from meta where key = ?", (key,)).fetchone()
		return row["value"] if row else None

	def commit(self):
		self.db.commit()

	def _delete_doctypes(self, where: str, params: list):
		for table in DOCTYPE_TABLES:
			self.db.execute(
				f"delete from {table} where doctype in (select name from doctypes where {where})", params
			)
		self.db.execute(f"delete from doctypes where {where}", params)

	def _insert_facts(self, file_id: int, facts: FileFacts):
		self.db.executemany(
			"insert into symbols values (?,?,?,?,?,?,?,?,?,?,?,?,?)",
			[
				(
					file_id,
					s.name,
					s.qualname,
					s.kind,
					s.line,
					s.end_line,
					s.signature,
					s.parent,
					s.doc,
					json.dumps(s.decorators) if s.decorators else "",
					int(s.whitelisted),
					int(s.allow_guest),
					s.http_methods,
				)
				for s in facts.symbols
			],
		)
		self.db.executemany(
			"insert into calls values (?,?,?,?,?,?,?,?)",
			[
				(file_id, c.caller, c.callee, c.resolved, c.line, c.receiver, c.arg, c.col)
				for c in facts.calls
			],
		)
		self.db.executemany(
			"insert into refs values (?,?,?,?,?,?,?,?)",
			[(file_id, r.kind, r.target, r.via, r.line, r.scope, r.access, r.col) for r in facts.refs],
		)
		self.db.executemany(
			"insert into flows values (?,?,?,?,?,?,?,?,?,?)",
			[
				(file_id, f.caller, f.callee, f.resolved, f.line, f.arg, f.source, f.via, f.col, f.returns)
				for f in facts.flows
			],
		)
		self.db.executemany(
			"insert into findings values (?,?,?,?,?,?)",
			[(file_id, f.rule, f.severity, f.message, f.line, f.scope) for f in facts.findings],
		)

	def _migrate(self):
		self.db.execute("create table if not exists meta (key text primary key, value text)")
		if self.get_meta("schema") != SCHEMA_VERSION:
			# Drop, not delete: `create table if not exists` keeps an old table's columns.
			for table in ("files", *FACT_TABLES, "hooks", "doctypes", *DOCTYPE_TABLES):
				self.db.execute(f"drop table if exists {table}")
			self.db.execute("delete from meta")
		self.db.executescript(SCHEMA + FACT_INDEXES)
		self.set_meta("schema", SCHEMA_VERSION)
		self.commit()


def _pad(path: tuple, size: int) -> list[str]:
	return [*path[:size], *[""] * (size - len(path[:size]))]


def _field_row(doctype: str, idx: int, f: dict) -> tuple:
	core = ("fieldname", "fieldtype", "label", "options")
	attrs = {k: v for k, v in f.items() if k not in core}
	return (
		doctype,
		idx,
		f.get("fieldname", ""),
		f.get("fieldtype", ""),
		f.get("label", ""),
		str(f.get("options", "")),
		json.dumps(attrs),
	)


def _index_names(ddl: str) -> list[str]:
	return [line.split()[5] for line in ddl.splitlines() if line.startswith("create index")]
