"""Pick the right analyzer for a file. Used by the indexer and by `outline`."""

import json
import re
from pathlib import Path

from frappe_ast.js.analyzer import JsAnalyzer
from frappe_ast.js.vue import VueAnalyzer
from frappe_ast.model import FileFacts, Ref
from frappe_ast.python.analyzer import PythonAnalyzer
from frappe_ast.records import RecordReader, record_kind
from frappe_ast.workspace import App, python_module


class FileAnalyzer:
	def __init__(self, known_apps: set[str]):
		self.python = PythonAnalyzer(known_apps)
		self.js = JsAnalyzer(known_apps)
		self.vue = VueAnalyzer(self.js)
		self.records = RecordReader()

	def analyze(self, path: Path, rel: str, lang: str, app: App | None) -> FileFacts:
		try:
			source = path.read_text(encoding="utf-8")
		except (UnicodeDecodeError, OSError) as e:
			return FileFacts(path=rel, lang=lang, error=f"unreadable: {e}")
		try:
			return self._dispatch(source, rel, lang, path, app)
		except RecursionError:
			return FileFacts(path=rel, lang=lang, error="too deeply nested to analyze")
		except Exception as e:  # one odd file must not stop a bench-wide index
			return FileFacts(path=rel, lang=lang, error=f"analyzer error: {type(e).__name__}: {e}")

	def _dispatch(self, source: str, rel: str, lang: str, path: Path, app: App | None) -> FileFacts:
		if lang == "python":
			module = python_module(app, path) if app else path.stem
			return self.python.analyze(rel, source, module)
		if lang == "record":
			return self.records.analyze(rel, source, record_kind(Path(rel).parts))
		if lang == "fixture":
			return _fixture(rel, source)
		if lang == "vue":
			return self.vue.analyze(rel, source)
		return self.js.analyze(rel, source, lang)


def _fixture(rel: str, source: str) -> FileFacts:
	"""fixtures/custom_field.json: one custom_field ref per row, on its fieldname line."""
	facts = FileFacts(path=rel, lang="fixture")
	try:
		rows = json.loads(source)
	except ValueError as e:
		facts.error = f"bad JSON: {e}"
		return facts
	if isinstance(rows, dict):  # {"User": [{"fieldname": ...}]}, the create_custom_fields shape
		rows = [
			{**r, "dt": dt}
			for dt, items in rows.items()
			if isinstance(items, list)
			for r in items
			if isinstance(r, dict)
		]
	for row in rows if isinstance(rows, list) else []:
		doctype, field = (row.get("dt"), row.get("fieldname")) if isinstance(row, dict) else (None, None)
		if isinstance(doctype, str) and isinstance(field, str):
			match = re.search(rf'"fieldname":\s*"{re.escape(field)}"', source)
			line = source.count("\n", 0, match.start()) + 1 if match else 1
			facts.refs.append(Ref("custom_field", field, doctype, line, "<fixture>", "define"))
	return facts
