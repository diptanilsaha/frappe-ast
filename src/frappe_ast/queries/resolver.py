"""Resolve dotted paths and DocType names against the index."""

from pathlib import PurePath

from frappe_ast.store import Store

SYMBOL_COLS = """s.name, s.qualname, s.kind, s.line, s.end_line, s.signature, s.parent, s.doc,
	s.decorators, s.whitelisted, s.allow_guest, s.http_methods, f.path, f.app, f.lang, f.module"""


class Resolver:
	def __init__(self, store: Store):
		self.store = store

	def symbol(self, dotted: str, follow_alias: bool = True) -> dict | None:
		"""Python symbol for a dotted path. Follows re-exports (`alias` rows)."""
		for _ in range(6):
			rows = self.store.rows(
				f"select {SYMBOL_COLS} from symbols s join files f on f.id = s.file_id "
				"where s.qualname = ? and f.lang = 'python' "
				# @overload stubs share a qualname; the real (whitelisted, last) def wins.
				"order by s.kind = 'alias', s.whitelisted desc, s.line desc",
				dotted,
			)
			if not rows:
				return None
			row = rows[0]
			if row["kind"] != "alias" or not follow_alias or not row["signature"]:
				return row
			dotted = row["signature"]
		return None

	def module_exists(self, dotted: str) -> bool:
		return bool(self.store.rows("select 1 from files where module = ? limit 1", dotted))

	def resolves(self, dotted: str) -> bool:
		return self.symbol(dotted) is not None or self.module_exists(dotted)

	def similar(self, dotted: str, limit: int = 5) -> list[str]:
		name = dotted.rsplit(".", 1)[-1]
		rows = self.store.rows(
			"select distinct s.qualname from symbols s join files f on f.id = s.file_id "
			"where s.name = ? and f.lang = 'python' and s.kind != 'alias' limit ?",
			name,
			limit,
		)
		return [r["qualname"] for r in rows]

	def doctype(self, name: str) -> dict | None:
		rows = self.store.rows("select * from doctypes where name = ?", name)
		if rows:
			return rows[0]
		rows = self.store.rows("select * from doctypes where lower(name) = lower(?)", name)
		return rows[0] if rows else None

	def controller_class(self, doctype: dict) -> dict | None:
		"""The Document subclass in <name>.py next to the DocType JSON."""
		py_path = doctype["path"].removesuffix(".json") + ".py"
		expected = doctype["name"].replace(" ", "").replace("-", "")
		rows = self.store.rows(
			f"select {SYMBOL_COLS} from symbols s join files f on f.id = s.file_id "
			"where f.path = ? and s.kind = 'class' and s.parent = ''",
			py_path,
		)
		exact = [r for r in rows if r["name"] == expected]
		return (exact or rows or [None])[0]

	def doctype_for_file(self, path: str) -> str:
		"""DocType whose folder holds `path` (controller, form JS, list JS, tests)."""
		folder = str(PurePath(path).parent)
		rows = self.store.rows("select name from doctypes where path like ?", folder + "/%.json")
		return rows[0]["name"] if rows else ""

	def find_method(self, cls: dict, name: str, depth: int = 0) -> tuple[dict | None, bool]:
		"""Look up `name` on `cls` and its bases. Returns (method, complete).

		`complete` is False when some base class is outside the index, so a miss
		does not prove the method is absent.
		"""
		method = self.symbol(f"{cls['qualname']}.{name}")
		if method is not None or depth > 8:
			return method, method is not None
		complete = True
		for base in _bases(cls["signature"]):
			base_cls = self._base_class(cls["module"], base)
			if base_cls is None:
				complete = complete and base in ("object", "Generic")
				continue
			method, base_complete = self.find_method(base_cls, name, depth + 1)
			if method is not None:
				return method, True
			complete = complete and base_complete
		return None, complete

	def _base_class(self, module: str, base: str) -> dict | None:
		head, _, rest = base.partition(".")
		local = self.symbol(f"{module}.{head}")
		if local is None:
			local = self.symbol(base)
			return local if local and local["kind"] == "class" else None
		target = self.symbol(f"{local['qualname']}.{rest}") if rest else local
		return target if target and target["kind"] == "class" else None


def _bases(signature: str) -> list[str]:
	inner = signature.strip()[1:-1]
	return [b.strip().split("[")[0] for b in inner.split(",") if b.strip() and "=" not in b]
