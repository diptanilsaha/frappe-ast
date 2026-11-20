"""Locate a bench, its apps, and the source files the analyzers read."""

import os
from dataclasses import dataclass
from pathlib import Path

from frappe_ast.records import record_kind

SKIP_DIRS = {
	"node_modules",
	".git",
	"__pycache__",
	"dist",
	"build",
	".venv",
	"env",
	"public/dist",
	"locale",
	"translations",
	".pytest_cache",
	"coverage",
}
LANG_BY_SUFFIX = {".py": "python", ".js": "js", ".mjs": "js", ".ts": "ts", ".tsx": "tsx", ".vue": "vue"}
MAX_FILE_BYTES = 1_500_000  # skip bundled or generated blobs


@dataclass(frozen=True)
class App:
	name: str
	root: Path  # apps/<name>

	@property
	def package(self) -> Path:
		return self.root / self.name

	def module_names(self) -> list[str]:
		modules_txt = self.package / "modules.txt"
		if not modules_txt.exists():
			return []
		return [line.strip() for line in modules_txt.read_text().splitlines() if line.strip()]


class Bench:
	def __init__(self, root: Path):
		self.root = root.resolve()

	@classmethod
	def find(cls, start: Path | None = None) -> "Bench":
		"""Walk up from `start` to the first folder that has apps/ and sites/."""
		here = (start or Path.cwd()).resolve()
		for folder in [here, *here.parents]:
			if (folder / "apps").is_dir() and (folder / "sites").is_dir():
				return cls(folder)
		raise SystemExit(f"No bench found at or above {here}. Pass --bench <path>.")

	def apps(self, only: list[str] | None = None) -> list[App]:
		names = self._installed_app_names()
		if only:
			names = [n for n in names if n in only]
		return [App(n, self.root / "apps" / n) for n in names if (self.root / "apps" / n / n).is_dir()]

	def app_for(self, path: Path) -> App | None:
		try:
			rel = path.resolve().relative_to(self.root / "apps")
		except ValueError:
			return None
		name = rel.parts[0]
		return App(name, self.root / "apps" / name)

	def relpath(self, path: Path) -> str:
		return str(path.resolve().relative_to(self.root))

	def _installed_app_names(self) -> list[str]:
		apps_txt = self.root / "sites" / "apps.txt"
		if apps_txt.exists():
			listed = [line.strip() for line in apps_txt.read_text().splitlines() if line.strip()]
		else:
			listed = []
		on_disk = sorted(p.name for p in (self.root / "apps").iterdir() if p.is_dir())
		# apps.txt order first (frappe leads), then apps that are only on disk.
		return listed + [n for n in on_disk if n not in listed]


def iter_source_files(root: Path):
	"""Yield (path, lang) for every analyzable file under `root` (an app or a library's src)."""
	for dirpath, dirnames, filenames in os.walk(root):
		dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
		if _is_build_output(dirpath):
			dirnames[:] = []
			continue
		for filename in filenames:
			lang = lang_for(filename, dirpath)
			if not lang or filename.endswith((".min.js", ".bundle.min.js", ".d.ts")):
				continue
			path = Path(dirpath) / filename
			if path.stat().st_size <= MAX_FILE_BYTES:
				yield path, lang


def lang_for(filename: str, dirpath: str = "") -> str:
	if filename in ("custom_field.json", "custom_fields.json"):
		return "fixture"  # fixtures/custom_field.json: custom fields the app ships
	if filename.endswith(".json") and record_kind((*Path(dirpath).parts, filename)):
		return "record"  # report/<name>/<name>.json and the other standard records
	return LANG_BY_SUFFIX.get(Path(filename).suffix, "")


def python_module(app: App, path: Path) -> str:
	"""apps/erpnext/erpnext/stock/utils.py -> erpnext.stock.utils"""
	try:
		rel = path.resolve().relative_to(app.root.resolve())
	except ValueError:
		return ""
	parts = list(rel.with_suffix("").parts)
	if parts[-1] == "__init__":
		parts = parts[:-1]
	return ".".join(parts)


def _is_build_output(dirpath: str) -> bool:
	return f"{os.sep}public{os.sep}dist" in dirpath or f"{os.sep}public{os.sep}frontend" in dirpath
