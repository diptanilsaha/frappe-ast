"""JS libraries that apps ship as source, frappe-ui first.

frappe-ui reaches an app as `<pkg>/node_modules/frappe-ui` (installed by yarn) or as
the `<app>/frappe-ui` git submodule. Apps pin different versions, so each version
is indexed once, under the label `frappe-ui@<version>`. Two copies with the same
version but different source (a git build vs the npm release) get `@<version>~<hash>`.
"""

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from frappe_ast.workspace import App

LIBRARY_PACKAGES = ("frappe-ui",)
DEP_KEYS = ("dependencies", "devDependencies", "peerDependencies")
MAX_PACKAGE_DEPTH = 3  # apps/<app>/<dir>/<dir>/package.json


@dataclass
class Frontend:
	"""A package.json inside an app that depends on a library."""

	app: str
	path: str  # bench-relative folder of the package.json
	package: str
	declared: str  # version spec in package.json
	installed: str = ""  # library label, "" when node_modules is missing
	installed_version: str = ""

	@property
	def stale(self) -> bool:
		"""An exact pin that node_modules does not match: run yarn install."""
		pinned = self.declared.lstrip("=")
		return bool(self.installed_version) and pinned[:1].isdigit() and pinned != self.installed_version


@dataclass
class Library:
	package: str
	version: str
	root: Path  # folder with the library's package.json
	fingerprint: str = ""
	used_by: list[str] = field(default_factory=list)  # bench-relative frontend folders
	label: str = ""

	@property
	def source(self) -> Path:
		return self.root / "src"


class LibraryFinder:
	def __init__(self, bench_root: Path):
		self.bench_root = bench_root

	def find(self, apps: list[App]) -> tuple[list[Library], list[Frontend]]:
		libraries: dict[str, Library] = {}
		frontends: list[Frontend] = []
		for app in apps:
			for pkg_dir, deps in self._packages(app):
				for package in LIBRARY_PACKAGES:
					if package in deps:
						frontends.append(self._frontend(app, pkg_dir, package, deps[package], libraries))
			for package in LIBRARY_PACKAGES:
				self._add(libraries, app.root / package, used_by=None)  # git submodule
		return list(libraries.values()), frontends

	def _frontend(self, app: App, pkg_dir: Path, package: str, declared: str, libraries: dict) -> Frontend:
		rel = str(pkg_dir.relative_to(self.bench_root))
		frontend = Frontend(app.name, rel, package, declared)
		installed = _installed_copy(pkg_dir, app.root, package)
		if installed:
			library = self._add(libraries, installed, used_by=rel)
			if library:
				frontend.installed, frontend.installed_version = library.label, library.version
		return frontend

	def _add(self, libraries: dict, root: Path, used_by: str | None) -> Library | None:
		meta = _read_json(root / "package.json")
		if not meta or not (root / "src").is_dir():
			return None  # e.g. a submodule that was never checked out
		package, version = meta.get("name", root.name), meta.get("version", "0")
		fingerprint = _fingerprint(root / "src")
		library = libraries.get(f"{package}@{version}")
		if library is None or library.fingerprint != fingerprint:
			label = f"{package}@{version}" if library is None else f"{package}@{version}~{fingerprint[:6]}"
			library = libraries.setdefault(
				label, Library(package, version, root.resolve(), fingerprint, label=label)
			)
		if used_by and used_by not in library.used_by:
			library.used_by.append(used_by)
		return library

	def _packages(self, app: App):
		"""Yield (folder, deps) for package.json files outside node_modules."""
		for depth in range(MAX_PACKAGE_DEPTH):
			for path in app.root.glob("/".join(["*"] * depth + ["package.json"])):
				if "node_modules" in path.parts:
					continue
				meta = _read_json(path)
				deps = {k: v for key in DEP_KEYS for k, v in (meta or {}).get(key, {}).items()}
				if deps:
					yield path.parent, deps


def _installed_copy(pkg_dir: Path, app_root: Path, package: str) -> Path | None:
	"""node_modules lookup from the package up to the app root (yarn workspaces hoist)."""
	for folder in [pkg_dir, *pkg_dir.parents]:
		candidate = folder / "node_modules" / package
		if candidate.is_dir():
			return candidate
		if folder == app_root:
			break
	return None


def _fingerprint(src: Path) -> str:
	"""Hash of (relative path, size) for every file: cheap, and changes with the code."""
	digest = hashlib.sha1()
	for dirpath, dirnames, filenames in os.walk(src):
		dirnames.sort()
		for name in sorted(filenames):
			path = Path(dirpath) / name
			digest.update(f"{path.relative_to(src)}:{path.stat().st_size}".encode())
	return digest.hexdigest()


def _read_json(path: Path) -> dict | None:
	try:
		return json.loads(path.read_text())
	except (OSError, ValueError):
		return None
