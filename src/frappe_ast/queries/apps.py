"""`frappe-ast apps`: apps, versions, required_apps, frontends and the JS libraries they use."""

import json
import re

from frappe_ast.store import Store
from frappe_ast.workspace import Bench

VERSION_RE = re.compile(r"""^__version__\s*=\s*["']([^"']+)""", re.M)


class AppGraph:
	def __init__(self, store: Store, bench: Bench):
		self.store = store
		self.bench = bench

	def run(self) -> dict:
		names = [a.name for a in self.bench.apps()]
		requires = self.requirements()
		frontends = json.loads(self.store.get_meta("frontends") or "[]")
		files = {r["app"]: r["n"] for r in self.store.rows("select app, count(*) n from files group by app")}
		apps = []
		for name in names:
			needs = requires.get(name, [])
			apps.append(
				{
					"app": name,
					"version": self._version(name),
					"files": files.get(name, 0),
					"requires": needs,
					"missing": [r for r in needs if r not in names],
					"required_by": [other for other, deps in requires.items() if name in deps],
					"frontends": [_frontend_line(f) for f in frontends if f["app"] == name],
				}
			)
		libraries = json.loads(self.store.get_meta("libraries") or "[]")
		for lib in libraries:
			lib["files"] = files.get(lib["label"], 0)
		return {"apps": apps, "libraries": libraries}

	def requirements(self) -> dict[str, list[str]]:
		"""app -> required app names. `frappe/erpnext` and git URLs reduce to `erpnext`."""
		out: dict[str, list[str]] = {}
		for row in self.store.rows("select app, value from hooks where key = 'required_apps' order by line"):
			out.setdefault(row["app"], []).append(app_name_of(row["value"]))
		return out

	def _version(self, name: str) -> str:
		init = self.bench.root / "apps" / name / name / "__init__.py"
		try:
			match = VERSION_RE.search(init.read_text())
		except OSError:
			return ""
		return match.group(1) if match else ""


def app_name_of(requirement: str) -> str:
	return requirement.rstrip("/").split("/")[-1].removesuffix(".git")


def _frontend_line(f: dict) -> str:
	installed = f["installed"] or "not installed (run yarn install)"
	line = f"{f['path']}: {f['package']} {f['declared']} -> {installed}"
	if f["stale"]:
		line += (
			f" (STALE: declares {f['declared']}, node_modules has {f['installed_version']}; run yarn install)"
		)
	return line
