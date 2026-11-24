"""The Python analyzer: symbols, imports, refs, per-file checks, hooks.py and monkey patches."""

from frappe_ast.python import compat
from frappe_ast.python.analyzer import PythonAnalyzer
from frappe_ast.python.hooks import HooksReader

API = "apps/demo/demo/api.py"
DISPATCH = "apps/demo/demo/dispatch.py"
APPS = {"demo", "frappe"}


def test_whitelisted_functions(analyze):
	symbols = {s.qualname: s for s in analyze(API).symbols}
	guest = symbols["demo.api.open_tasks"]
	assert guest.whitelisted and guest.allow_guest and guest.http_methods == "POST"
	assert not symbols["demo.api.not_exposed"].whitelisted
	# `make` is an @overload stub plus the whitelisted implementation.
	assert symbols["demo.api.make"].whitelisted


def test_imports_resolve_calls_and_reexports(analyze):
	facts = analyze(API)
	assert {s.qualname: s.kind for s in facts.symbols}["demo.api.close"] == "alias"
	resolved = {c.resolved for c in facts.calls if c.caller == "demo.api.validate_any"}
	assert "demo.demo.doctype.demo_task.demo_task.close_task" in resolved


def test_import_refs(analyze):
	facts = analyze(API)
	assert {"frappe", "frappe.utils", "typing", "frappe.api"} <= facts.targets("import")
	assert {"frappe.utils.cint", "frappe.api.ApiVersion"} <= facts.targets("import_name")
	(inner,) = [r for r in facts.refs("import_name") if r.target == "frappe.api.ApiVersion"]
	assert (inner.via, inner.scope) == ("frappe.api", "demo.api.api_version")


def test_relative_imports_in_a_package():
	source = "from . import utils\nfrom .sub import thing\nimport a.b as ab\n"
	facts = PythonAnalyzer(APPS).analyze("demo/pkg/__init__.py", source, "demo.pkg")
	aliases = {s.name: s.signature for s in facts.symbols if s.kind == "alias"}
	assert aliases == {"utils": "demo.pkg.utils", "thing": "demo.pkg.sub.thing"}
	assert {r.target for r in facts.refs if r.kind == "import"} == {"demo.pkg", "demo.pkg.sub", "a.b"}


def test_module_imports_win_over_type_checking_blocks():
	# A generated `if TYPE_CHECKING:` block in a class imports a child-table type of the same name.
	source = (
		"from demo.utils import Note\n\n\n"
		"class Task(Note):\n"
		"\tfrom typing import TYPE_CHECKING\n\n"
		"\tif TYPE_CHECKING:\n"
		"\t\tfrom demo.demo.doctype.note.note import Note\n"
	)
	facts = PythonAnalyzer(APPS).analyze("demo/task.py", source, "demo.task")
	aliases = {s.qualname: s.signature for s in facts.symbols if s.kind == "alias"}
	assert aliases["demo.task.Note"] == "demo.utils.Note"


def test_frappe_refs(analyze):
	facts = analyze(API)
	assert {"Demo Task", "Demo Note"} <= facts.targets("doctype")
	assert "demo.api.validate_any" in facts.targets("method")  # frappe.enqueue
	assert facts.targets("realtime") == {"task_done"}


def test_call_receivers(analyze):
	# what a method is called on, for the call graph: an instance, or a document of a DocType
	calls = {(c.callee, c.receiver, c.arg) for c in analyze(DISPATCH).facts.calls}
	assert ("engine.run", "call:demo.dispatch.Engine", "") in calls
	assert ("Engine().run", "call:demo.dispatch.Engine", "") in calls
	assert ("doc.save", "doctype:Demo Task", "") in calls
	assert ("frappe.get_doc().submit", "doctype:Demo Note", "") in calls
	assert ("doc.save", "doctype:?", "") in calls
	assert ("frappe.get_hooks", "", "permission_query_conditions") in calls
	assert ("frappe.db.sql", "", "") in calls  # a multi-line SQL string is not a name


def test_callback_refs(analyze):
	refs = {(r.target, r.via, r.scope) for r in analyze(DISPATCH).refs("callback")}
	assert ("demo.dispatch.job", "frappe.enqueue", "demo.dispatch.start_jobs") in refs
	assert ("demo.dispatch.route_handler", "werkzeug.routing.Rule", "<module>") in refs
	assert ("demo.dispatch.sandboxed", "dict", "demo.dispatch.safe_globals") in refs


def test_typed_module_attributes(analyze):
	qb = next(s for s in analyze(DISPATCH).symbols if s.qualname == "demo.dispatch.qb")
	assert (qb.kind, qb.signature) == ("variable", "demo.dispatch.Builder")


def test_api_urls_in_strings(analyze):
	# get_url("/api/method/x?y=1") and f"/api/method/x?y={y}": the constant part names the method.
	urls = {r.target for r in analyze(API).refs("dotted") if r.via == "url"}
	assert urls == {"demo.api.open_tasks", "demo.api.make"}


def test_per_file_checks(analyze):
	rules = {(f.rule, f.severity) for f in analyze(API).findings}
	assert {
		("FA101", "warning"),  # `status` formatted into SQL
		("FA103", "info"),
		("FA105", "warning"),
		("FA106", "info"),
		("FA107", "info"),
	} <= rules


def test_hooks_fold_computed_keys(bench_root):
	entries = HooksReader().read("demo", bench_root / "apps/demo/demo/hooks.py")
	events = {(e.path, e.value) for e in entries if e.key == "doc_events"}
	# tuple(closing_doctypes) and doc_events["Demo Note"] = {...} expand to plain keys.
	assert {
		(("Demo Task", "on_submit"), "demo.api.on_submit_any"),
		(("Demo Note", "on_submit"), "demo.api.missing_handler"),
		(("Demo Note", "on_update"), "demo.api.validate_any"),
	} <= events
	assert not any(e.value.startswith("<expr") for e in entries)


def test_monkey_patches(analyze):
	base = "demo.controllers.base_document.BaseDocument"
	patches = {
		(r.target, r.via, r.scope) for r in analyze("apps/demo/demo/overrides.py").refs("monkey_patch")
	}
	# `api.make = original` restores the saved function, so it is not a patch.
	assert patches == {
		("demo.api.make", "demo.overrides.our_make", "demo.overrides.TaskOverride.validate"),
		("demo.api.get_count", "demo.overrides.install.counted", "demo.overrides.install"),  # a wrapper
		(f"{base}.archive", "demo.overrides.traced_archive", "demo.overrides.install"),  # setattr
		(f"{base}.archive_all", "demo.overrides.traced_archive", "demo.overrides.install"),
		("demo.api.daily", "<lambda>", "demo.overrides.install"),
	}


def test_syntax_newer_than_the_running_python(analyze, bench_root):
	# frappe v16 uses 3.12 type parameters and f-strings, and 3.14 `except A, B:`.
	rel = "apps/demo/demo/utils/new_syntax.py"
	source = (bench_root / rel).read_text()
	old = compat.downgrade(source)
	assert old.count("\n") == source.count("\n")  # line numbers do not move
	compile(old, rel, "exec")  # the grammar of the oldest supported Python
	facts = analyze(rel)
	assert not facts.facts.error
	lines = {s.qualname: s.line for s in facts.symbols}
	assert lines["demo.utils.new_syntax.first"] == 7 and lines["demo.utils.new_syntax.Box.label"] == 12
	assert lines["demo.utils.new_syntax.report_url"] == 23
	# An f-string that parses alone keeps its text.
	assert ("demo.api.make", "url") in facts.pairs("dotted")
