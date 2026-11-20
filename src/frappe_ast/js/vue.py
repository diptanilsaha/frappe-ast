"""Split a Vue SFC into blocks; analyze <script> with JsAnalyzer and scan <template>."""

import re
from pathlib import PurePath

from frappe_ast.js.analyzer import JsAnalyzer
from frappe_ast.model import Call, FileFacts, Ref, Symbol

SCRIPT_RE = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.S)
LANG_RE = re.compile(r"""lang\s*=\s*["'](\w+)["']""")
# Components are PascalCase or kebab-case with a dash; native tags are neither.
COMPONENT_RE = re.compile(r"<([A-Z][A-Za-z0-9]*(?:\.[A-Z]\w*)?|[a-z][a-z0-9]*(?:-[a-z0-9]+)+)[\s/>]")
EVENT_RE = re.compile(r"""(?:@|v-on:)([\w:.-]+)\s*=\s*"([^"]*)\"""")
HANDLER_NAME_RE = re.compile(r"^\s*([A-Za-z_$][\w$.]*)\s*(\(|$)")
# defineProps<ButtonProps>(), defineProps(buttonProps), defineComponent({ props: buttonProps }); same for emits and slots.
API_RES = {
	part: (
		re.compile(
			rf"{macro}\s*(?:<\s*(\w+)\s*>|\(\s*(\w+)\s*\))|\b{part}\s*:\s*(?:Object as SlotsType<\s*)?(\w+)\s*[,>]"
		),
		re.compile(rf"{macro}\s*(?:<\s*\{{|\(\s*[\[{{])|\b{part}\s*:\s*(?:Object as SlotsType<\s*)?\{{"),
	)
	for part, macro in (("props", "defineProps"), ("emits", "defineEmits"), ("slots", "defineSlots"))
}
NON_COMPONENTS = {"router-view", "router-link", "keep-alive", "font-face"}


class VueAnalyzer:
	def __init__(self, js: JsAnalyzer):
		self.js = js

	def analyze(self, path: str, source: str) -> FileFacts:
		facts = FileFacts(path=path, lang="vue")
		component = PurePath(path).stem
		facts.symbols.append(
			Symbol(component, component, "component", 1, source.count("\n") + 1, signature=_api_hint(source))
		)
		for match in SCRIPT_RE.finditer(source):
			lang = _script_lang(match.group(1))
			offset = source.count("\n", 0, match.start(2))
			self.js.analyze(path, match.group(2), lang, facts=facts, line_offset=offset)
		template = _template_span(source)
		if template:
			self._scan_template(facts, source, *template)
		return facts

	def _scan_template(self, facts: FileFacts, source: str, start: int, end: int):
		text = source[start:end]
		base_line = source.count("\n", 0, start) + 1
		for match in COMPONENT_RE.finditer(text):
			tag = match.group(1)
			if tag.lower() not in NON_COMPONENTS:
				line = base_line + text.count("\n", 0, match.start())
				facts.refs.append(Ref("component", tag, "template", line, "<template>", "render"))
		for match in EVENT_RE.finditer(text):
			handler = HANDLER_NAME_RE.match(match.group(2))
			if handler:
				line = base_line + text.count("\n", 0, match.start())
				facts.calls.append(Call("<template>", handler.group(1), "", line))


def _api_hint(source: str) -> str:
	"""Where props, emits and slots are declared: a type or variable to look up, or "inline"."""
	parts = []
	for part, (named, inline) in API_RES.items():
		match = named.search(source)
		if match:
			parts.append(f"{part}: {next(g for g in match.groups() if g)}")
		elif inline.search(source):
			parts.append(f"{part}: inline")
	return f"({', '.join(parts)})" if parts else ""


def _script_lang(attrs: str) -> str:
	match = LANG_RE.search(attrs)
	lang = match.group(1) if match else "js"
	return {"typescript": "ts", "jsx": "tsx"}.get(lang, lang if lang in ("ts", "tsx") else "js")


def _template_span(source: str) -> tuple[int, int] | None:
	"""Span of the outermost <template> block. Nested <template> tags are inside it."""
	start = source.find("<template")
	end = source.rfind("</template>")
	if start == -1 or end <= start:
		return None
	return source.index(">", start) + 1, end
