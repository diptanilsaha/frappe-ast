"""`frappe-ast component <Name>`: where a Vue component is defined and who renders it."""

import json
import re
from collections import Counter

from frappe_ast.queries.component_api import ComponentApi
from frappe_ast.queries.resolver import SYMBOL_COLS
from frappe_ast.render import symbol_line
from frappe_ast.store import Store


class ComponentReport:
	def __init__(self, store: Store, limit: int = 20):
		self.store = store
		self.limit = limit
		self.frontends = json.loads(store.get_meta("frontends") or "[]")

	def run(self, name: str, app: str = "") -> dict:
		"""With `app`, keep its usages and the definitions its frontends build against."""
		pascal = _pascal(name)
		names = sorted({pascal, _kebab(pascal)})
		usages = [u for u in self._usages(names) if not app or u["app"] == app]
		definitions = self._definitions(pascal)
		if app:
			allowed = {app} | {f["installed"] for f in self.frontends if f["app"] == app}
			definitions = [d for d in definitions if d["app"] in allowed]
		return {
			"query": pascal,
			"definitions": [f"{d['app']}  {symbol_line(d)}" for d in definitions],
			"api": {d["path"]: ComponentApi(self.store).lines(d) for d in definitions[:3]},
			"imported_from": self._import_sources(pascal, app),
			"resolves_to": self._resolution(usages),
			"usage_count": len(usages),
			"usages_by_app": dict(Counter(u["app"] for u in usages).most_common()),
			"usages": usages[: self.limit],
		}

	def _definitions(self, pascal: str) -> list[dict]:
		return self.store.rows(
			f"select {SYMBOL_COLS} from symbols s join files f on f.id = s.file_id "
			"where s.kind = 'component' and s.name = ? order by f.app like 'frappe-ui@%', f.app, f.path",
			pascal,
		)

	def _import_sources(self, pascal: str, app: str) -> dict:
		rows = self.store.rows(
			"select r.via from refs r join files f on f.id = r.file_id "
			"where r.kind = 'import_name' and r.target = ? and (? = '' or f.app = ?)",
			pascal,
			app,
			app,
		)
		return dict(Counter(r["via"] for r in rows).most_common(self.limit))

	def _usages(self, names: list[str]) -> list[dict]:
		marks = ",".join("?" * len(names))
		return self.store.rows(
			"select f.path, r.line, f.app from refs r join files f on f.id = r.file_id "
			f"where r.kind = 'component' and r.target in ({marks}) order by f.app, f.path, r.line",
			*names,
		)

	def _resolution(self, usages: list[dict]) -> list[str]:
		"""For each frontend that renders the component, the frappe-ui copy it builds against."""
		used = {self._frontend_of(u["path"]) for u in usages} - {None}
		return [
			f"{f['path']} -> {f['installed'] or 'frappe-ui not installed (run yarn install)'}"
			for f in self.frontends
			if f["path"] in used
		]

	def _frontend_of(self, path: str) -> str | None:
		matches = [f["path"] for f in self.frontends if path.startswith(f["path"] + "/")]
		return max(matches, key=len) if matches else None


def _pascal(name: str) -> str:
	if "-" not in name:
		return name[:1].upper() + name[1:]
	return "".join(part.capitalize() for part in name.split("-"))


def _kebab(pascal: str) -> str:
	return re.sub(r"(?<!^)(?=[A-Z])", "-", pascal).lower()
