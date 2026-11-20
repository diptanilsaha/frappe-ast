"""Standard records an app ships as <module>/<kind>/<name>/<name>.json, besides DocTypes.

A Report, Page, Print Format, Web Form, Notification, Dashboard Chart, Number Card, Workspace
or onboarding record becomes one symbol (kind = folder, qualname "<kind>:<name>"), plus
doctype refs for the DocTypes it targets and field refs for the fields it names. The .py,
.js and .html beside the JSON are its code; `record <name>` joins them.
"""

import json
import re
from dataclasses import dataclass, field

from frappe_ast.model import FileFacts, Ref, Symbol


@dataclass(frozen=True)
class RecordKind:
	doctype_keys: tuple = ()  # keys that name the target DocType
	field_keys: tuple = ()  # keys that name a field of that DocType
	summary_keys: tuple = ()  # shown in the symbol signature
	child_fields: tuple = ()  # (table key, field key): rows that name fields
	links: tuple = ()  # (table key, name key, type key or ""): rows that name other records
	extra: dict = field(default_factory=dict)


RECORD_KINDS = {
	"report": RecordKind(("ref_doctype",), (), ("report_type", "is_standard", "prepared_report")),
	"page": RecordKind((), (), ("page_name", "title")),
	"print_format": RecordKind(("doc_type",), (), ("print_format_type", "standard")),
	"web_form": RecordKind(
		("doc_type",), (), ("route", "login_required"), child_fields=(("web_form_fields", "fieldname"),)
	),
	"notification": RecordKind(
		("document_type",),
		("value_changed", "set_property_after_alert", "date_changed"),
		("event", "channel"),
	),
	"dashboard_chart": RecordKind(
		("document_type", "parent_document_type"),
		("based_on", "value_based_on", "group_by_based_on", "aggregate_function_based_on"),
		("chart_type", "report_name"),
	),
	"number_card": RecordKind(
		("document_type", "parent_document_type"),
		("aggregate_function_based_on",),
		("type", "function", "report_name"),
	),
	"workspace": RecordKind(
		(),
		(),
		("title", "type"),
		links=(
			("links", "link_to", "link_type"),
			("shortcuts", "link_to", "type"),
			("charts", "chart_name", ""),
			("number_cards", "number_card_name", ""),
		),
	),
	"onboarding_step": RecordKind(("reference_document",), ("field",), ("action",)),
	"form_tour": RecordKind(("reference_doctype",), (), ("title",)),
	"module_onboarding": RecordKind((), (), ("title",), links=(("steps", "step", ""),)),
	"dashboard_chart_source": RecordKind((), (), ("source_name",)),
	"web_template": RecordKind((), (), ("type",)),
}
LINK_KINDS = {"DocType": "doctype", "Report": "report", "Page": "page", "": ""}
DEFAULT_LINK = {"charts": "dashboard_chart", "number_cards": "number_card", "steps": "onboarding_step"}


def record_kind(rel_parts: tuple[str, ...]) -> str:
	"""`report` for .../report/general_ledger/general_ledger.json, else ""."""
	if len(rel_parts) < 3 or not rel_parts[-1].endswith(".json"):
		return ""
	kind, folder, filename = rel_parts[-3:]
	return kind if kind in RECORD_KINDS and filename == f"{folder}.json" else ""


class RecordReader:
	def analyze(self, rel: str, source: str, kind: str) -> FileFacts:
		facts = FileFacts(path=rel, lang="record")
		try:
			data = json.loads(source)
		except ValueError as e:
			facts.error = f"bad JSON: {e}"
			return facts
		name = data.get("name") if isinstance(data, dict) else None
		if not isinstance(name, str):
			return facts
		spec = RECORD_KINDS[kind]
		facts.symbols.append(Symbol(name, f"{kind}:{name}", kind, 1, 1, signature=_summary(data, spec)))
		via = f"{kind}:{name}"
		doctypes = [d for d in (data.get(k) for k in spec.doctype_keys) if isinstance(d, str) and d]
		for doctype in doctypes:
			facts.refs.append(Ref("doctype", doctype, via, _line(source, doctype), "<record>", kind))
		self._fields(facts, data, spec, doctypes[:1], via, source)
		self._links(facts, data, spec, via, source)
		return facts

	def _fields(self, facts, data, spec, doctypes, via, source):
		names = [data.get(k) for k in spec.field_keys]
		for table, key in spec.child_fields:
			names += [row.get(key) for row in data.get(table) or [] if isinstance(row, dict)]
		for doctype in doctypes:
			for fieldname in (f for f in names if isinstance(f, str) and f):
				facts.refs.append(
					Ref("field", f"{doctype}.{fieldname}", via, _line(source, fieldname), "<record>", "read")
				)
		for doctype, fieldname in _filter_fields(data.get("filters_json")):
			facts.refs.append(
				Ref("field", f"{doctype}.{fieldname}", via, _line(source, fieldname), "<record>", "read")
			)

	def _links(self, facts, data, spec, via, source):
		for table, name_key, type_key in spec.links:
			for row in data.get(table) or []:
				target = row.get(name_key) if isinstance(row, dict) else None
				link_kind = LINK_KINDS.get(row.get(type_key, "") if type_key else "", "") or DEFAULT_LINK.get(
					table, ""
				)
				if isinstance(target, str) and target and link_kind:
					ref_kind = "doctype" if link_kind == "doctype" else "record_link"
					value = target if ref_kind == "doctype" else f"{link_kind}:{target}"
					facts.refs.append(Ref(ref_kind, value, via, _line(source, target), "<record>", "link"))


def _summary(data: dict, spec: RecordKind) -> str:
	parts = [f"{k}={data[k]}" for k in spec.summary_keys if data.get(k) not in (None, "", 0)]
	parts += [f"{k}={data[k]}" for k in spec.doctype_keys if data.get(k)]
	return f"({', '.join(parts)})"


def _filter_fields(raw) -> list[tuple[str, str]]:
	"""filters_json: [[doctype, field, op, value], ...] or {field: value} (DocType unknown)."""
	try:
		rows = json.loads(raw) if isinstance(raw, str) else []
	except ValueError:
		return []
	if not isinstance(rows, list):
		return []
	return [
		(row[0], row[1])
		for row in rows
		if isinstance(row, list) and len(row) > 2 and isinstance(row[0], str) and isinstance(row[1], str)
	]


def _line(source: str, value: str) -> int:
	match = re.search(re.escape(json.dumps(value)), source)
	return source.count("\n", 0, match.start()) + 1 if match else 1
