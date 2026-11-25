"""The Python analyzer: symbols, imports, refs, per-file checks, hooks.py and monkey patches."""

import pytest
from support import tagged_lines

from frappe_ast.python import compat
from frappe_ast.python.analyzer import PythonAnalyzer
from frappe_ast.python.hooks import HooksReader

API = "apps/demo/demo/api.py"
DISPATCH = "apps/demo/demo/dispatch.py"
ARGFLOW = "apps/demo/demo/argflow.py"
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
	assert ("Lock().__enter__", "call:demo.dispatch.Lock", "") in calls  # with Lock():
	assert ("lock.__exit__", "call:demo.dispatch.Lock", "") in calls
	assert ("Lock().__aexit__", "call:demo.dispatch.Lock", "") in calls  # async with
	assert ("super().__init__", "super:demo.dispatch.ChildParser", "") in calls
	assert ("super().save", "super:demo.dispatch.ChildParser", "") in calls  # super(ChildParser, self)


def test_attribute_refs(analyze):
	# attribute reads and writes on a receiver of known type, for @property edges
	refs = {(r.target, r.via, r.access, r.scope) for r in analyze(DISPATCH).refs("attribute")}
	assert ("session", "call:demo.dispatch.Conn", "read", "demo.dispatch.use_property") in refs
	assert ("session", "call:demo.dispatch.Conn", "write", "demo.dispatch.set_property") in refs
	assert ("session", "self:demo.dispatch.Conn", "read", "demo.dispatch.Conn.reuse") in refs
	assert not {r for r in refs if r[0] in ("run", "open_session")}  # a method call is a call


def test_callback_refs(analyze):
	refs = {(r.target, r.via, r.scope) for r in analyze(DISPATCH).refs("callback")}
	assert ("demo.dispatch.job", "frappe.enqueue", "demo.dispatch.start_jobs") in refs
	assert ("demo.dispatch.route_handler", "werkzeug.routing.Rule", "<module>") in refs
	assert ("demo.dispatch.sandboxed", "dict", "demo.dispatch.safe_globals") in refs


def test_typed_module_attributes(analyze):
	qb = next(s for s in analyze(DISPATCH).symbols if s.qualname == "demo.dispatch.qb")
	assert (qb.kind, qb.signature) == ("variable", "demo.dispatch.Builder")


@pytest.mark.parametrize(
	"tag, callee, arg, source, via",
	[
		("loaded", "frappe.get_doc", "1", "param:name", ""),
		("update", "doc.update", "0", "param:kwargs", ""),
		("sql", "frappe.db.sql", "0", "form_dict:limit", "f-string,frappe.utils.cint"),  # outermost first
		("filters", "frappe.get_list", "filters", "param:name", "container"),
		("branch", "frappe.db.get_value", "1", "param:name", ""),  # assigned in one branch of an if
		("loop", "frappe.get_doc", "1", "param:kwargs", ""),  # the loop variable
		("method", "frappe.db.exists", "1", "param:name", ".strip"),
		("header", "frappe.cache.set_value", "1", "request.headers:Host", ""),
		("form_key", "frappe.db.get_value", "1", "form_dict:task", ""),
		("fetch", "frappe.make_get_request", "0", "param:url", ""),  # self is not a parameter
	],
)
def test_argument_flow(analyze, bench_root, tag, callee, arg, source, via):
	line = tagged_lines(bench_root / ARGFLOW)[tag]
	flows = {(f.callee, f.arg, f.source, f.via) for f in analyze(ARGFLOW).facts.flows if f.line == line}
	assert (callee, arg, source, via) in flows


def test_no_flow_without_a_source(analyze, bench_root):
	flows = analyze(ARGFLOW).facts.flows
	assert not [f for f in flows if f.line == tagged_lines(bench_root / ARGFLOW)["rebound"]]  # a literal now
	assert not [f for f in flows if f.callee == "isinstance"]  # inspects, passes nothing on


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
