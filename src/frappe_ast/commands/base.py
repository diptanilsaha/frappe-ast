"""Shared command plumbing: lazy bench/store/renderer, and the Command base class."""

import sys
from pathlib import Path

from frappe_ast.indexer import Indexer
from frappe_ast.render import Renderer
from frappe_ast.store import Store
from frappe_ast.workspace import Bench


class Context:
	"""Lazy bench, store and renderer shared by commands."""

	def __init__(self, args):
		self.args = args
		self.render = Renderer(args.json)
		self._bench = None
		self._store = None

	@property
	def bench(self) -> Bench:
		if self._bench is None:
			self._bench = Bench(self.args.bench) if self.args.bench else Bench.find()
		return self._bench

	def store(self, refresh: bool = True) -> Store:
		if self._store is None:
			self._store = Store.for_bench(self.bench.root, self.args.db)
			if refresh and not self.args.no_refresh:
				self.refresh()
		return self._store

	def refresh(self, apps: list[str] | None = None) -> dict:
		first = self._store.get_meta("indexed_at") is None
		if first:
			print(f"frappe-ast: building index for {self.bench.root} (first run)", file=sys.stderr)
		log = (
			(lambda msg: print(f"frappe-ast: {msg}", file=sys.stderr))
			if first or apps
			else (lambda msg: None)
		)
		return Indexer(self.bench, self._store).run(self.bench.apps(apps), log=log)

	@property
	def app_names(self) -> set[str]:
		return {a.name for a in self.bench.apps()}


class Command:
	help = ""

	def add_parser(self, sub, name: str, common):
		parser = sub.add_parser(name, help=self.help, description=self.help, parents=[common])
		self.arguments(parser)

	def arguments(self, parser):
		pass

	def run(self, ctx: Context, args) -> int | None:
		raise NotImplementedError


def bench_prefix(bench: Bench, path: str) -> str:
	if not path:
		return ""
	target = Path(path).resolve()
	try:
		return str(target.relative_to(bench.root))
	except ValueError:
		return path
