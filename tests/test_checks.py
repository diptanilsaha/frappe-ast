"""`check`: one case per cross-file rule, and the near misses each rule must not report."""

import pytest

from frappe_ast.queries.check import Checker


@pytest.fixture(scope="module")
def findings(store) -> list[dict]:
	return Checker(store, {"demo", "demo_fr", "frappe"}).run()


def messages(findings, rule: str, path_end: str = "") -> set[str]:
	return {f["message"] for f in findings if f["rule"] == rule and f["path"].endswith(path_end)}


def test_fx201_hook_path(findings):
	assert messages(findings, "FX201") == {
		"doc_events > Demo Task > on_submit: demo.api.missing_handler does not resolve",
		"doc_events > Demo Note > on_submit: demo.api.missing_handler does not resolve",
	}


def test_fx202_fx203_server_methods(findings):
	assert "demo.api.gone does not exist" in messages(findings, "FX202")
	assert messages(findings, "FX203") == {"demo.api.not_exposed is not @frappe.whitelist()"}
	# An overloaded def (the whitelisted implementation wins), and a function frappe.client.* overrides.
	assert not any("demo.api.make" in m for m in messages(findings, "FX202") | messages(findings, "FX203"))


def test_fx202_skips_built_bundles(findings):
	# public/board/assets/index-<hash>.js calls demo.api.gone, but it is build output, not source.
	assert not any("/assets/" in f["path"] for f in findings)


def test_fx204_doc_methods(findings):
	assert {
		"DemoTask.private_helper is not @frappe.whitelist()",
		"demo.demo.doctype.demo_task.demo_task.no_such_function does not exist",
	} <= messages(findings, "FX204", "demo_task.js")
	assert not any("close_task does not" in m for m in messages(findings, "FX204"))
	# `vanished` (useDoc) is not reported: Document (frappe) is not indexed, so it may be inherited.
	assert messages(findings, "FX204", "tasks.ts") == {"DemoTask.private_helper is not @frappe.whitelist()"}


def test_fx205_form_handlers(findings):
	# helper (frm.events.x), recalculate (frm.trigger), color (a shipped custom field) are fine.
	assert {m.split("'")[1] for m in messages(findings, "FX205")} == {"ghost_field"}


def test_fx206_missing_required_app(findings):
	assert [m.split(",")[0] for m in messages(findings, "FX206")] == ["demo requires missing_app"]


def test_fx207_fields(findings):
	assert {m for m in messages(findings, "FX207") if "Demo Task" in m} == {
		"Demo Task has no field statsu (custom field, or a typo?)",  # demo_task.js and tasks.ts
		"Demo Task table items has no field nope (custom field, or a typo?)",
		"Demo Task has no field ghost (custom field, or a typo?)",
		"Demo Task has no field colour (custom field, or a typo?)",  # the Number Card's filters
	}
	# priority comes from create_custom_fields, color from fixtures/custom_field.json.
	assert messages(findings, "FX207", "tasks.ts") == {
		"Demo Task has no field statsu (custom field, or a typo?)"
	}


def test_fx208_records(findings):
	# `execute = OpenTasksReport.execute` is an entry point. The misnamed registration is not.
	(fx208,) = messages(findings, "FX208")
	assert "registers report 'Open Taskz', but the report is 'Open Tasks'" in fx208


def test_fx209_patch_on_a_missing_function(findings):
	# Only demo_fr's patch on demo counts: inside its own app, demo adds attributes on purpose.
	assert messages(findings, "FX209") == {
		"patches demo.api.make_all, which does not exist "
		"(renamed upstream? the patch adds a new attribute that nothing calls)"
	}
