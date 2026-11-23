"""The synthetic bench in tests/bench, indexed once per session.

tests/bench holds two apps. `demo` exercises every analyzer: a DocType with its controller and
form script, shared desk JS, a Report and a Number Card, a Vue frontend with a frappe-ui copy in
node_modules. `demo_fr` patches demo the way erpnext_france patches erpnext. Each case in the
bench is the smallest form of a shape that a real app writes.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
from support import Facts

from frappe_ast.analyze import FileAnalyzer
from frappe_ast.indexer import Indexer
from frappe_ast.queries.method import MethodTrace
from frappe_ast.store import Store
from frappe_ast.workspace import Bench, lang_for

BENCH = Path(__file__).parent / "bench"
APPS = {"demo", "demo_fr", "frappe"}


@pytest.fixture(scope="session")
def bench_root(tmp_path_factory) -> Path:
	root = tmp_path_factory.mktemp("bench")
	shutil.copytree(BENCH, root, dirs_exist_ok=True)
	for app in ("demo", "demo_fr"):  # `impact` diffs each app against its git HEAD
		repo = root / "apps" / app
		_git(repo, "init", "-q")
		_git(repo, "add", ".")
		_git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
	return root


@pytest.fixture(scope="session")
def store(bench_root, tmp_path_factory) -> Store:
	store = Store(tmp_path_factory.mktemp("db") / "index.sqlite")
	bench = Bench(bench_root)
	Indexer(bench, store, jobs=1).run(bench.apps(), log=lambda _: None)
	return store


@pytest.fixture(scope="session")
def analyze(bench_root):
	"""analyze("apps/demo/...") -> the facts of one bench file, as the indexer reads it."""
	bench, analyzer = Bench(bench_root), FileAnalyzer(APPS)

	def run(rel: str) -> Facts:
		path = bench_root / rel
		lang = lang_for(path.name, str(path.parent))
		return Facts(analyzer.analyze(path, rel, lang, bench.app_for(path)))

	return run


@pytest.fixture(scope="session")
def trace(store):
	"""trace("dotted.path") -> the `method` report."""
	return MethodTrace(store).run


def _git(repo: Path, *args: str):
	subprocess.run(["git", "-C", str(repo), *args], check=True)
