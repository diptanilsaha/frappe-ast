from pathlib import Path

from frappe_ast.js.analyzer import JsAnalyzer
from frappe_ast.js.vue import VueAnalyzer
from frappe_ast.python.analyzer import PythonAnalyzer
from frappe_ast.python.hooks import HooksReader

APPS = {"demo", "frappe"}


def refs(facts, kind):
	return {r.target for r in facts.refs if r.kind == kind}


def test_python_whitelist_imports_and_doctype_refs(bench_root):
	path = bench_root / "apps/demo/demo/api.py"
	facts = PythonAnalyzer(APPS).analyze(str(path), path.read_text(), "demo.api")
	symbols = {s.qualname: s for s in facts.symbols}
	guest = symbols["demo.api.open_tasks"]
	assert guest.whitelisted and guest.allow_guest and guest.http_methods == "POST"
	assert not symbols["demo.api.not_exposed"].whitelisted
	assert symbols["demo.api.close"].kind == "alias"
	resolved = {c.resolved for c in facts.calls if c.caller == "demo.api.validate_any"}
	assert "demo.demo.doctype.demo_task.demo_task.close_task" in resolved
	assert {"Demo Task", "Demo Note"} <= refs(facts, "doctype")
	assert "demo.api.validate_any" in refs(facts, "method")


def test_python_relative_import_in_package():
	source = "from . import utils\nfrom .sub import thing\n"
	facts = PythonAnalyzer(APPS).analyze("demo/pkg/__init__.py", source, "demo.pkg")
	targets = {s.name: s.signature for s in facts.symbols if s.kind == "alias"}
	assert targets == {"utils": "demo.pkg.utils", "thing": "demo.pkg.sub.thing"}


def test_python_import_refs(bench_root):
	path = bench_root / "apps/demo/demo/api.py"
	facts = PythonAnalyzer(APPS).analyze(str(path), path.read_text(), "demo.api")
	assert {"frappe", "frappe.utils", "typing", "frappe.api"} <= refs(facts, "import")
	assert {"frappe.utils.cint", "frappe.api.ApiVersion"} <= refs(facts, "import_name")
	inner = next(r for r in facts.refs if r.target == "frappe.api.ApiVersion")
	assert (inner.via, inner.scope, inner.access) == ("frappe.api", "demo.api.api_version", "import")


def test_python_relative_import_refs():
	source = "from . import utils\nfrom .sub import thing\nimport a.b as ab\n"
	facts = PythonAnalyzer(APPS).analyze("demo/pkg/__init__.py", source, "demo.pkg")
	assert refs(facts, "import") == {"demo.pkg", "demo.pkg.sub", "a.b"}
	assert refs(facts, "import_name") == {"demo.pkg.utils", "demo.pkg.sub.thing"}


def test_python_checks(bench_root):
	path = bench_root / "apps/demo/demo/api.py"
	facts = PythonAnalyzer(APPS).analyze(str(path), path.read_text(), "demo.api")
	by_rule = {(f.rule, f.severity) for f in facts.findings}
	assert ("FA101", "warning") in by_rule  # `status` parameter in an f-string
	assert ("FA103", "info") in by_rule
	assert ("FA105", "warning") in by_rule
	assert ("FA106", "info") in by_rule
	assert ("FA107", "info") in by_rule


def test_hooks_fold_computed_keys(bench_root):
	entries = HooksReader().read("demo", bench_root / "apps/demo/demo/hooks.py")
	events = {(e.path, e.value) for e in entries if e.key == "doc_events"}
	assert (("Demo Task", "on_submit"), "demo.api.on_submit_any") in events
	assert (("Demo Note", "on_submit"), "demo.api.missing_handler") in events
	assert (("Demo Note", "on_update"), "demo.api.validate_any") in events
	assert not any(e.value.startswith("<expr") for e in entries)


def test_js_form_events_and_calls(bench_root):
	path = bench_root / "apps/demo/demo/demo/doctype/demo_task/demo_task.js"
	facts = JsAnalyzer(APPS).analyze(str(path), path.read_text(), "js")
	assert "Demo Task:refresh" in refs(facts, "form_event")
	assert refs(facts, "doc_method") == {"finish", "private_helper"}
	assert refs(facts, "module_method") == {"close_task", "no_such_function"}
	assert {"demo.api.open_tasks", "demo.api.not_exposed", "demo.api.gone"} <= refs(facts, "method")
	assert refs(facts, "listview") == {"Demo Task"}
	assert refs(facts, "form_trigger") == {"recalculate"}


def test_vue_resources_and_template(bench_root):
	path = bench_root / "apps/demo/frontend/src/pages/Tasks.vue"
	js = JsAnalyzer(APPS)
	facts = VueAnalyzer(js).analyze(str(path), path.read_text())
	# `method: 'POST'` must not hide the url.
	assert refs(facts, "method") == {"demo.api.get_count", "demo.api.open_tasks"}
	assert refs(facts, "doctype") == {"Demo Task"}
	assert {"TaskList", "Button"} <= refs(facts, "component")
	assert {c.callee for c in facts.calls if c.caller == "<template>"} == {"openTask", "save"}
	open_task = next(s for s in facts.symbols if s.name == "openTask")
	assert Path(path).read_text().splitlines()[open_task.line - 1].startswith("function openTask")
