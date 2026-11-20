"""Resolve a component's `(props: X, emits: Y, slots: Z)` hint to the declarations of X, Y, Z."""

import re
from pathlib import PurePath

from frappe_ast.store import Store

HINT_RE = re.compile(r"(props|emits|slots): (\w+)")


class ComponentApi:
	def __init__(self, store: Store):
		self.store = store

	def lines(self, definition: dict) -> list[str]:
		"""One line per part: `props DialogProps {open?, title?} path:line`."""
		out = []
		for part, name in HINT_RE.findall(definition.get("signature") or ""):
			if name == "inline":
				out.append(f"{part} inline in {definition['path']}")
				continue
			decl = self._declaration(name, definition)
			where = f"{decl['path']}:{decl['line']}" if decl else "(declaration not indexed)"
			out.append(f"{part} {name} {decl['signature'] if decl else ''} {where}".replace("  ", " "))
		return out

	def _declaration(self, name: str, definition: dict) -> dict | None:
		"""Same index unit as the component, nearest folder first: types.ts beside the .vue."""
		rows = self.store.rows(
			"select f.path, s.line, s.signature from symbols s join files f on f.id = s.file_id "
			"where s.name = ? and s.kind in ('type', 'props') and f.app = ?",
			name,
			definition["app"],
		)
		folder = PurePath(definition["path"]).parent.parts
		return max(rows, key=lambda r: _shared_prefix(PurePath(r["path"]).parts, folder), default=None)


def _shared_prefix(a: tuple, b: tuple) -> int:
	count = 0
	for x, y in zip(a, b):
		if x != y:
			break
		count += 1
	return count
