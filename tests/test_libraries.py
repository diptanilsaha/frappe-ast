from frappe_ast.libraries import LibraryFinder
from frappe_ast.queries.apps import AppGraph
from frappe_ast.queries.check import Checker
from frappe_ast.queries.component import ComponentReport
from frappe_ast.queries.lookup import Lookup
from frappe_ast.workspace import Bench

FUI = "apps/demo/frontend/node_modules/frappe-ui"


def test_finds_installed_frappe_ui(bench_root):
	bench = Bench(bench_root)
	libraries, frontends = LibraryFinder(bench.root).find(bench.apps())
	(library,) = libraries
	assert library.label == "frappe-ui@9.9.9"
	assert library.used_by == ["apps/demo/frontend"]
	assert frontends[0].declared == "^9.9.0" and frontends[0].installed == "frappe-ui@9.9.9"


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


def test_component_report(store):
	report = ComponentReport(store).run("button")
	(definition,) = report["definitions"]
	assert definition.startswith("frappe-ui@9.9.9  component Button(props: buttonProps)")
	assert ComponentReport(store).run("Button", app="other")["definitions"] == []
	assert report["imported_from"] == {"frappe-ui": 1}
	assert report["resolves_to"] == ["apps/demo/frontend -> frappe-ui@9.9.9"]
	assert report["usages_by_app"] == {"demo": 1}
	(api,) = report["api"].values()
	assert api == [f"props buttonProps {{label, theme}} {FUI}/src/components/Button/types.ts:1"]


def test_component_api_types(store):
	(api,) = ComponentReport(store).run("Dialog")["api"].values()
	assert api == [
		f"props DialogProps {{open?, title}} {FUI}/src/components/Button/types.ts:2",
		f"emits inline in {FUI}/src/components/Dialog/Dialog.vue",
	]


def test_app_graph_and_missing_required_app(store, bench_root):
	(app,) = AppGraph(store, Bench(bench_root)).run()["apps"]
	assert app["requires"] == ["missing_app"] and app["missing"] == ["missing_app"]
	assert app["frontends"] == ["apps/demo/frontend: frappe-ui ^9.9.0 -> frappe-ui@9.9.9"]
	findings = Checker(store, {"demo"}).run(rules={"FX206"})
	assert [f["message"].split(",")[0] for f in findings] == ["demo requires missing_app"]


def test_same_version_different_source_and_stale_pin(tmp_path):
	import json

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
	assert labels[0] == "frappe-ui@1.0.0" and labels[1].startswith("frappe-ui@1.0.0~") and len(labels) == 2
	by_path = {f.path: f for f in frontends}
	assert by_path["apps/x/a"].installed == by_path["apps/x/c"].installed != by_path["apps/x/b"].installed
	assert [f.path for f in frontends if f.stale] == ["apps/x/c"]
