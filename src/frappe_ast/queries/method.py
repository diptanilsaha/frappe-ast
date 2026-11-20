"""`frappe-ast method <dotted>`: everything that defines, exposes or calls a server method."""

from pathlib import PurePath

from frappe_ast.queries.resolver import Resolver
from frappe_ast.store import Store


class MethodTrace:
	def __init__(self, store: Store, limit: int = 50):
		self.store = store
		self.resolver = Resolver(store)
		self.limit = limit

	def run(self, dotted: str) -> dict:
		symbol = self.resolver.symbol(dotted)
		if symbol is None:
			return {
				"query": dotted,
				"found": False,
				"similar": self.resolver.similar(dotted),
				"overridden_by": self._overrides([dotted]),
				"hooks": self._hooks(dotted),
			}
		qualname = symbol["qualname"]
		names = sorted({dotted, qualname})
		return {
			"query": dotted,
			"found": True,
			"definition": symbol,
			"doctype": self._doctype(symbol),
			"overridden_by": self._overrides(names),
			"hooks": self._hooks(*names),
			"python_callers": self._python_callers(qualname),
			"maybe_callers": self._maybe_callers(symbol),
			"string_refs": self._string_refs(names),
			"client_calls": self._client_calls(symbol),
			"constant_calls": self._constant_calls(names),
			"callees": self._callees(qualname),
		}

	def _doctype(self, symbol: dict) -> str:
		return self.resolver.doctype_for_file(symbol["path"]) if symbol["kind"] == "method" else ""

	def _overrides(self, names: list[str]) -> list[dict]:
		marks = ",".join("?" * len(names))
		return self.store.rows(
			f"select app, value, line from hooks where key = 'override_whitelisted_methods' and p1 in ({marks})",
			*names,
		)

	def _hooks(self, *names: str) -> list[dict]:
		marks = ",".join("?" * len(names))
		return self.store.rows(
			f"select app, key, p1, p2, p3, value, line from hooks where value in ({marks}) limit ?",
			*names,
			self.limit,
		)

	def _python_callers(self, qualname: str) -> list[dict]:
		return self.store.rows(
			"select f.path, c.line, c.caller as scope, c.callee from calls c join files f on f.id = c.file_id "
			"where c.resolved = ? order by f.path, c.line limit ?",
			qualname,
			self.limit,
		)

	def _maybe_callers(self, symbol: dict) -> list[dict]:
		"""Unresolved `<obj>.name(...)` calls. For methods, the receiver type is unknown."""
		if symbol["kind"] != "method":
			return []
		return self.store.rows(
			"select f.path, c.line, c.caller as scope, c.callee from calls c join files f on f.id = c.file_id "
			# A self.x call on an inherited x resolves to a symbol that does not exist.
			"where (c.resolved = '' or not exists (select 1 from symbols t where t.qualname = c.resolved)) and c.callee like ? and f.lang = 'python' order by f.path, c.line limit ?",
			"%." + symbol["name"],
			self.limit,
		)

	def _string_refs(self, names: list[str]) -> list[dict]:
		marks = ",".join("?" * len(names))
		rows = self.store.rows(
			"select f.path, f.lang, r.kind, r.via, r.line, r.scope from refs r join files f on f.id = r.file_id "
			f"where r.kind in ('method', 'dotted') and r.target in ({marks}) order by f.path, r.line, r.kind desc",
			*names,
		)
		return _dedupe_lines(rows)[: self.limit]

	def _client_calls(self, symbol: dict) -> list[dict]:
		"""frm.call("x") next to the controller, and frappe-ui calls that name "<DocType>:x"."""
		kind = {"method": "doc_method", "function": "module_method"}.get(symbol["kind"])
		doctype = self.resolver.doctype_for_file(symbol["path"])
		if not kind or not doctype:
			return []
		folder = str(PurePath(symbol["path"]).parent)
		return self.store.rows(
			"select f.path, r.line, r.via, r.scope from refs r join files f on f.id = r.file_id "
			"where r.kind = ? and ((r.target = ? and f.path like ?) or r.target = ?) "
			"order by f.path, r.line limit ?",
			kind,
			symbol["name"],
			folder + "/%",
			f"{doctype}:{symbol['name']}",
			self.limit,
		)

	def _constant_calls(self, names: list[str]) -> list[dict]:
		"""call(API.get) where API.get = "<this method>" is a constant in another file of the app."""
		marks = ",".join("?" * len(names))
		return self.store.rows(
			"select f.path, r.line, r.via, r.target as constant, r.scope from symbols s "
			"join files cf on cf.id = s.file_id join refs r on r.kind = 'method_const' and r.target = s.qualname "
			"join files f on f.id = r.file_id and f.app = cf.app "
			f"where s.kind = 'const' and s.signature in ({marks}) order by f.path, r.line limit ?",
			*[f' = "{name}"' for name in names],
			self.limit,
		)

	def _callees(self, qualname: str) -> list[dict]:
		return self.store.rows(
			"select c.line, c.callee, c.resolved from calls c where c.caller = ? "
			"and c.callee not in ('frappe.whitelist', '_') order by c.line limit ?",
			qualname,
			self.limit,
		)


def _dedupe_lines(rows: list[dict]) -> list[dict]:
	"""A frappe.call string is both a `method` and a `dotted` ref; keep one per line."""
	seen, unique = set(), []
	for row in rows:
		key = (row["path"], row["line"])
		if key not in seen:
			seen.add(key)
			unique.append(row)
	return unique
