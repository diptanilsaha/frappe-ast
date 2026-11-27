"""Indexing a bench: which files go in, frappe-ui copies, apps, components, `impact` and the CLI."""

import json

from frappe_ast.cli import main
from frappe_ast.indexer import Indexer
from frappe_ast.libraries import LibraryFinder
from frappe_ast.queries.apps import AppGraph
from frappe_ast.queries.component import ComponentReport
from frappe_ast.queries.impact import Impact
from frappe_ast.queries.lookup import Lookup
from frappe_ast.store import FACT_INDEXES, _index_names
from frappe_ast.workspace import Bench

FUI = "apps/demo/frontend/node_modules/frappe-ui"


def test_build_output_is_not_indexed(store):
	# A Vite build committed under public/<ui>/assets: hashed names, not source.
	assert not store.rows("select 1 from files where path like '%/public/board/assets/%'")


def test_frappe_ui_copy_is_found(bench_root):
	bench = Bench(bench_root)
	(library,), frontends = LibraryFinder(bench.root).find(bench.apps())
	assert library.label == "frappe-ui@9.9.9" and library.used_by == ["apps/demo/frontend"]
	assert (frontends[0].declared, frontends[0].installed) == ("^9.9.0", "frappe-ui@9.9.9")


def test_frappe_ui_source_is_indexed(store):
	paths = {r["path"] for r in store.rows("select path from files where app = 'frappe-ui@9.9.9'")}
	# The library's own tests are left out: they mock methods no bench has.
	assert paths == {
		f"{FUI}/src/components/Button/Button.vue",
		f"{FUI}/src/components/Button/types.ts",
		f"{FUI}/src/components/Dialog/Dialog.vue",
		f"{FUI}/src/resources/resources.js",
	}
	(found,) = Lookup(store).find("createResource", lang="js")
	assert found["path"] == f"{FUI}/src/resources/resources.js"


def test_same_version_different_source_and_stale_pin(tmp_path):
	(tmp_path / "sites").mkdir()
	app = tmp_path / "apps" / "x"
	(app / "x").mkdir(parents=True)
	for folder, version, code, pin in (
		("a", "1.0.0", "a", "1.0.0"),
		("b", "1.0.0", "bb", "1.0.0"),
		("c", "1.0.0", "a", "2.0.0"),
	):
		(app / folder).mkdir()
		(app / folder / "package.json").write_text(json.dumps({"dependencies": {"frappe-ui": pin}}))
		lib = app / folder / "node_modules" / "frappe-ui"
		(lib / "src").mkdir(parents=True)
		(lib / "package.json").write_text(json.dumps({"name": "frappe-ui", "version": version}))
		(lib / "src" / "index.js").write_text(code)
	bench = Bench(tmp_path)
	libraries, frontends = LibraryFinder(bench.root).find(bench.apps())
	labels = sorted(lib.label for lib in libraries)
	# Same version, other files: a second unit with a `~<hash>` label.
	assert len(labels) == 2 and labels[0] == "frappe-ui@1.0.0" and labels[1].startswith("frappe-ui@1.0.0~")
	by_path = {f.path: f for f in frontends}
	assert by_path["apps/x/a"].installed == by_path["apps/x/c"].installed != by_path["apps/x/b"].installed
	assert [f.path for f in frontends if f.stale] == ["apps/x/c"]  # pins 2.0.0, has 1.0.0


def test_components(store):
	report = ComponentReport(store).run("button")
	(definition,) = report["definitions"]
	assert definition.startswith("frappe-ui@9.9.9  component Button(props: buttonProps)")
	assert ComponentReport(store).run("Button", app="other")["definitions"] == []
	assert report["imported_from"] == {"frappe-ui": 1}
	assert report["resolves_to"] == ["apps/demo/frontend -> frappe-ui@9.9.9"]
	assert report["usages_by_app"] == {"demo": 1}
	assert list(report["api"].values()) == [
		[f"props buttonProps {{label, theme}} {FUI}/src/components/Button/types.ts:1"]
	]
	assert list(ComponentReport(store).run("Dialog")["api"].values()) == [
		[
			f"props DialogProps {{open?, title}} {FUI}/src/components/Button/types.ts:2",
			f"emits inline in {FUI}/src/components/Dialog/Dialog.vue",
		]
	]


def test_apps(store, bench_root):
	demo, demo_fr = AppGraph(store, Bench(bench_root)).run()["apps"]
	assert demo["requires"] == ["missing_app"] and demo["missing"] == ["missing_app"]
	assert demo["frontends"] == ["apps/demo/frontend: frappe-ui ^9.9.0 -> frappe-ui@9.9.9"]
	assert demo_fr["requires"] == [] and demo_fr["frontends"] == []


def test_impact(store, bench_root):
	api = bench_root / "apps/demo/demo/api.py"
	original = api.read_text()
	api.write_text(original.replace('\tfrappe.throw("Plain message")', '\tfrappe.throw(_("Changed"))'))
	try:
		bench = Bench(bench_root)
		Indexer(bench, store, jobs=1).run(bench.apps(), log=lambda _: None)
		result = Impact(store, bench.root).run(bench.apps(), "HEAD")
	finally:
		api.write_text(original)
		Indexer(bench, store, jobs=1).run(bench.apps(), log=lambda _: None)
	(file,) = result["files"]
	(symbol,) = file["symbols"]
	assert symbol["symbol"].startswith("function demo.api.validate_any")
	assert {h["key"] for h in symbol["hooks"]} == {"doc_events"}


def test_cli_outline_json(bench_root, capsys):
	code = main(["outline", str(bench_root / "apps/demo/demo/demo/doctype/demo_task/demo_task.js"), "--json"])
	assert code == 0
	assert json.loads(capsys.readouterr().out)["frappe"]["form_events"][0].endswith("Demo Task:refresh")


def test_a_first_index_builds_every_fact_index(store):
	# The first index loads facts with the indexes dropped (Store.bulk) and builds them at the end.
	built = {r["name"] for r in store.rows("select name from sqlite_master where type = 'index'")}
	assert set(_index_names(FACT_INDEXES)) <= built
