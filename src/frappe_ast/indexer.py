"""Build or refresh the bench index. Only changed files are parsed again."""

import hashlib
import json
import re
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from frappe_ast.analyze import FileAnalyzer
from frappe_ast.doctype_json import DocTypeReader
from frappe_ast.libraries import LibraryFinder
from frappe_ast.python.hooks import HooksReader
from frappe_ast.store import Store
from frappe_ast.workspace import App, Bench, iter_source_files

_worker: FileAnalyzer | None = None

LIBRARY_TEST_RE = re.compile(r"\.(test|spec|cy|stories|story)\.\w+$|\.playground\.vue$")


class Indexer:
	def __init__(self, bench: Bench, store: Store, jobs: int = 0):
		self.bench = bench
		self.store = store
		self.jobs = jobs

	def run(self, apps: list[App], log=print) -> dict:
		started = time.time()
		app_names = [a.name for a in self.bench.apps()]
		libraries, frontends = LibraryFinder(self.bench.root).find(self.bench.apps())
		self.store.drop_apps_except(app_names + [lib.label for lib in libraries])
		if self.store.get_meta("analyzer") != analyzer_version():
			self.store.clear_files()
			self.store.set_meta("analyzer", analyzer_version())
		stamps = self.store.file_stamps()
		# Libraries are indexed whole on every run: they are shared by apps.
		units = [(a.name, a.root) for a in apps] + [(lib.label, lib.source) for lib in libraries]
		todo, seen = self._plan(units, stamps)
		unit_names = {name for name, _ in units}
		removed = [p for p, stamp in stamps.items() if p not in seen and stamp[3] in unit_names]
		for path in removed:
			self.store.delete_file(path)
		self._analyze(todo, app_names, log)
		for app in apps:
			self._index_app_meta(app)
		self._save_library_meta(libraries, frontends)
		self.store.set_meta("bench", str(self.bench.root))
		self.store.set_meta("indexed_at", str(int(time.time())))
		self.store.commit()
		return {
			"parsed": len(todo),
			"removed": len(removed),
			"files": len(seen),
			"seconds": round(time.time() - started, 1),
		}

	def _plan(self, units: list[tuple[str, Path]], stamps: dict):
		todo, seen = [], set()
		for unit, root in units:
			for path, lang in iter_source_files(root):
				if "@" in unit and LIBRARY_TEST_RE.search(path.name):
					continue  # a library's own tests mock methods no bench has
				rel = self.bench.relpath(path)
				seen.add(rel)
				stat = path.stat()
				known = stamps.get(rel)
				if not known or known[1] != stat.st_mtime or known[2] != stat.st_size:
					todo.append((str(self.bench.root), unit, rel, lang, stat.st_mtime, stat.st_size))
		return todo, seen

	def _analyze(self, todo: list, app_names: list[str], log):
		if not todo:
			return
		log(f"parsing {len(todo)} files")
		if len(todo) < 200 or self.jobs == 1:
			_init_worker(app_names)
			results = map(_analyze_one, todo)
			self._save(todo, results)
			return
		with ProcessPoolExecutor(self.jobs or None, initializer=_init_worker, initargs=(app_names,)) as pool:
			self._save(todo, pool.map(_analyze_one, todo, chunksize=64))

	def _save(self, todo, results):
		for (_, app, _, _, mtime, size), facts in zip(todo, results):
			self.store.replace_file(facts, app, mtime, size)

	def _save_library_meta(self, libraries, frontends):
		rows = [
			{"label": lib.label, "path": self.bench.relpath(lib.root), "used_by": lib.used_by}
			for lib in libraries
		]
		self.store.set_meta("libraries", json.dumps(rows))
		self.store.set_meta("frontends", json.dumps([{**f.__dict__, "stale": f.stale} for f in frontends]))

	def _index_app_meta(self, app: App):
		hooks = HooksReader().read(app.name, app.package / "hooks.py")
		doctypes = list(DocTypeReader().iter_app(app.name, app.package, self.bench.root))
		self.store.replace_app_meta(app.name, hooks, doctypes)


def analyzer_version() -> str:
	"""Hash of this package's source: an analyzer change forces a full reparse."""
	digest = hashlib.sha1()
	for path in sorted(Path(__file__).parent.rglob("*.py")):
		digest.update(path.read_bytes())
	return digest.hexdigest()[:12]


def _init_worker(app_names: list[str]):
	global _worker
	_worker = FileAnalyzer(set(app_names))


def _analyze_one(item):
	root, app_name, rel, lang, _, _ = item
	path = Path(root) / rel
	return _worker.analyze(path, rel, lang, App(app_name, Path(root) / "apps" / app_name))
