"""Index lookups behind find, callers, callees, refs, endpoints and hooks."""

from frappe_ast.queries.resolver import SYMBOL_COLS, Resolver
from frappe_ast.store import Store


class Lookup:
	def __init__(self, store: Store, limit: int = 50):
		self.store = store
		self.resolver = Resolver(store)
		self.limit = limit

	def find(self, pattern: str, kind: str = "", app: str = "", lang: str = "") -> list[dict]:
		"""Exact name, dotted suffix, or a glob with `*`."""
		if "*" in pattern:
			where, params = "(s.qualname glob ? or s.name glob ?)", [pattern, pattern]
		elif "." in pattern or ":" in pattern:
			where, params = "(s.qualname = ? or s.qualname like ?)", [pattern, "%." + pattern]
		else:
			where, params = "s.name = ?", [pattern]
		where += " and s.kind != 'alias'"
		for column, value in (("s.kind", kind), ("f.app", app), ("f.lang", lang)):
			if value:
				where += f" and {column} = ?"
				params.append(value)
		return self.store.rows(
			f"select {SYMBOL_COLS} from symbols s join files f on f.id = s.file_id where {where} "
			"order by f.app = 'frappe' desc, f.path, s.line limit ?",
			*params,
			self.limit,
		)

	def callers(self, target: str) -> dict:
		"""Resolved callers of a dotted path, plus same-name calls that could not be resolved."""
		symbol = self.resolver.symbol(target)
		qualname = symbol["qualname"] if symbol else target
		name = qualname.rsplit(".", 1)[-1]
		resolved = self.store.rows(
			"select f.path, c.line, c.caller as scope, c.callee from calls c join files f on f.id = c.file_id "
			"where c.resolved = ? order by f.path, c.line limit ?",
			qualname,
			self.limit,
		)
		unresolved = self.store.rows(
			"select f.path, c.line, c.caller as scope, c.callee from calls c join files f on f.id = c.file_id "
			# A self.x call on an inherited x resolves to a symbol that does not exist.
			"where (c.resolved = '' or not exists (select 1 from symbols t where t.qualname = c.resolved)) and (c.callee = ? or c.callee like ?) order by f.path, c.line limit ?",
			name,
			"%." + name,
			self.limit,
		)
		return {
			"target": qualname,
			"defined": bool(symbol),
			"resolved": resolved,
			"unresolved_same_name": unresolved,
		}

	def callees(self, target: str) -> list[dict]:
		return self.store.rows(
			"select f.path, c.line, c.callee, c.resolved from calls c join files f on f.id = c.file_id "
			"where c.caller = ? order by c.line limit ?",
			target,
			self.limit * 4,
		)

	def refs(self, target: str, kind: str = "") -> dict:
		kind_sql, params = (" and r.kind = ?", [kind]) if kind else ("", [])
		refs = self.store.rows(
			"select f.path, r.kind, r.via, r.access, r.line, r.scope from refs r join files f on f.id = r.file_id "
			f"where r.target = ?{kind_sql} order by f.path, r.line limit ?",
			target,
			*params,
			self.limit,
		)
		hooks = self.store.rows(
			"select app, key, p1, p2, p3, value, line from hooks where value = ? or p1 = ?", target, target
		)
		return {"target": target, "refs": refs, "hooks": hooks}

	def endpoints(self, app: str = "", guest_only: bool = False, path_prefix: str = "") -> list[dict]:
		where, params = "s.whitelisted = 1", []
		if app:
			where += " and f.app = ?"
			params.append(app)
		if guest_only:
			where += " and s.allow_guest = 1"
		if path_prefix:
			where += " and f.path like ?"
			params.append(path_prefix + "%")
		return self.store.rows(
			f"select {SYMBOL_COLS} from symbols s join files f on f.id = s.file_id where {where} "
			"order by f.path, s.line limit ?",
			*params,
			self.limit * 20,
		)

	def hooks(self, app: str = "", key: str = "", match: str = "") -> list[dict]:
		where, params = "1 = 1", []
		for column, value in (("app", app), ("key", key)):
			if value:
				where += f" and {column} = ?"
				params.append(value)
		if match:
			where += " and (value like ? or p1 like ? or p2 like ?)"
			params += [f"%{match}%"] * 3
		return self.store.rows(
			f"select * from hooks where {where} order by app, key, p1, p2 limit ?", *params, self.limit * 20
		)
