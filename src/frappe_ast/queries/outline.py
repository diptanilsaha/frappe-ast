"""`frappe-ast outline <file>`: structure and Frappe touchpoints of one file, no index needed."""

from collections import defaultdict
from pathlib import Path

from frappe_ast.analyze import FileAnalyzer
from frappe_ast.model import FileFacts
from frappe_ast.render import symbol_line
from frappe_ast.workspace import Bench, lang_for

REF_GROUPS = {
	"method": "server_calls",
	"doc_method": "doc_method_calls",
	"form_event": "form_events",
	"doctype": "doctypes",
	"listview": "registrations",
	"report": "registrations",
	"treeview": "registrations",
	"component": "components",
	"dotted": "dotted_paths",
}


class Outline:
	def __init__(self, bench: Bench | None, known_apps: set[str]):
		self.bench = bench
		self.analyzer = FileAnalyzer(known_apps)

	def run(self, path: Path, with_calls: bool = False) -> dict:
		lang = lang_for(path.name)
		if not lang:
			raise SystemExit(f"Unsupported file type: {path}")
		app = self.bench.app_for(path) if self.bench else None
		rel = self.bench.relpath(path) if self.bench and app else str(path)
		facts = self.analyzer.analyze(path, rel, lang, app)
		out = {"path": rel, "lang": lang, "module": facts.module, "error": facts.error}
		out["symbols"] = _symbol_tree(facts)
		out["frappe"] = _grouped_refs(facts)
		out["findings"] = [f"{f.line} {f.rule} {f.severity}: {f.message}" for f in facts.findings]
		if with_calls:
			out["calls"] = [
				f"{c.line} {c.caller} -> {c.callee}" + (f" ({c.resolved})" if c.resolved else "")
				for c in facts.calls
			]
		return out


def _symbol_tree(facts: FileFacts) -> list[str]:
	"""One line per symbol, indented by nesting. Aliases (imports) are left out."""
	lines, stack = [], []
	for sym in sorted((s for s in facts.symbols if s.kind != "alias"), key=lambda s: (s.line, -s.end_line)):
		while stack and sym.line > stack[-1]:
			stack.pop()
		row = {**sym.__dict__, "qualname": sym.name, "doc": sym.doc}
		lines.append("  " * len(stack) + f"{sym.line}-{sym.end_line} " + symbol_line(row))
		if sym.kind != "component":
			stack.append(sym.end_line)
	return lines


def _grouped_refs(facts: FileFacts) -> dict:
	groups: dict[str, list[str]] = defaultdict(list)
	seen = set()
	for ref in facts.refs:
		group = REF_GROUPS.get(ref.kind)
		if not group:
			continue
		text = _ref_text(ref)
		key = (group, text if ref.kind in ("doctype", "component") else (text, ref.line))
		if key in seen:
			continue
		seen.add(key)
		groups[group].append(text if ref.kind in ("doctype", "component") else f"{ref.line} {text}")
	return dict(groups)


def _ref_text(ref) -> str:
	if ref.kind == "doctype":
		return f"{ref.target} ({ref.access})"
	if ref.kind in ("method", "doc_method", "dotted"):
		return f"{ref.target} via {ref.via} in {ref.scope}"
	if ref.kind == "form_event":
		return ref.target
	if ref.kind in ("listview", "report", "treeview"):
		return f"{ref.kind}: {ref.target}"
	return ref.target
