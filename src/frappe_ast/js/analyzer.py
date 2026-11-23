"""Extract symbols, calls and Frappe references from JS/TS with tree-sitter."""

import re

import tree_sitter_javascript
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

from frappe_ast.js import constants
from frappe_ast.js import frappe_patterns as fp
from frappe_ast.js import nodes as n
from frappe_ast.js.client_api import DocTypeData
from frappe_ast.js.desk import DeskRefs
from frappe_ast.js.form_setup import FormSetups
from frappe_ast.js.frappe_ui import DocMethodRefs
from frappe_ast.js.method_values import MethodValues
from frappe_ast.js.nodes import FUNCTION_TYPES
from frappe_ast.js.ts_types import TypeSymbols
from frappe_ast.model import Call, FileFacts, Ref, Symbol

LANGUAGES = {
	"js": Language(tree_sitter_javascript.language()),
	"ts": Language(tree_sitter_typescript.language_typescript()),
	"tsx": Language(tree_sitter_typescript.language_tsx()),
}
DOTTED_RE = re.compile(r"^[a-z_][a-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*){2,}$")


class JsAnalyzer:
	def __init__(self, known_apps: set[str]):
		self.known_apps = known_apps
		self.parsers = {lang: Parser(language) for lang, language in LANGUAGES.items()}

	def analyze(
		self, path: str, source: str, lang: str, facts: FileFacts | None = None, line_offset: int = 0
	):
		"""Analyze `source`. Pass `facts` and `line_offset` to add a Vue <script> block."""
		facts = facts or FileFacts(path=path, lang=lang)
		tree = self.parsers[lang].parse(source.encode())
		if tree.root_node.has_error and not facts.error:
			facts.error = "partial parse (syntax errors)"
		consts = constants.collect(tree.root_node)
		walker = _Walker(facts, self.known_apps, line_offset, consts, tree.root_node)
		for name, (method, node) in consts.items():
			walker._symbol(node, name.split(".")[-1], "const", [], qualname=name, signature=f' = "{method}"')
		walker.walk(tree.root_node, [])
		walker.values.finish()
		return facts


class _Walker(TypeSymbols):
	def __init__(self, facts: FileFacts, known_apps: set[str], line_offset: int, consts: dict, root: Node):
		self.facts = facts
		self.known_apps = known_apps
		self.offset = line_offset
		self.consts = consts
		self.ui = DocMethodRefs(self._ref)
		self.data = DocTypeData(self._ref)
		self.desk = DeskRefs(self._ref)
		self.setup = FormSetups(self._ref, known_apps)
		self.values = MethodValues(self._ref, consts, root)

	def walk(self, node: Node, scope: list[str]):
		handler = getattr(self, f"_on_{node.type}", None)
		new_scope = handler(node, scope) if handler else None
		inner = scope if new_scope is None else new_scope
		for child in node.children:
			self.walk(child, inner)

	# Symbols

	def _on_function_declaration(self, node, scope):
		return self._symbol(node, n.text(node.child_by_field_name("name")), "function", scope)

	_on_generator_function_declaration = _on_function_declaration

	def _on_class_declaration(self, node, scope):
		if node.parent is not None and node.parent.type == "assignment_expression":
			return None  # `erpnext.x.Y = class extends Z {}`: named after its target, below
		name = n.text(node.child_by_field_name("name"))
		return self._symbol(node, name, "class", scope, signature=n.heritage(node))

	_on_class = _on_class_declaration

	def _on_member_expression(self, node, scope):
		self.desk.on_member(node, scope)
		return None

	def _on_method_definition(self, node, scope):
		return self._symbol(node, n.text(node.child_by_field_name("name")), "method", scope)

	def _on_variable_declarator(self, node, scope):
		name, value = node.child_by_field_name("name"), node.child_by_field_name("value")
		self.ui.bind(name, value, scope)
		if value is not None and value.type in FUNCTION_TYPES:
			return self._symbol(node, n.text(name), "function", scope)
		self._props_object(node, name, value, scope)
		return None

	def _on_pair(self, node, scope):
		self.desk.on_pair(node, scope)
		value = node.child_by_field_name("value")
		if value is not None and value.type in FUNCTION_TYPES:
			return self._symbol(node, n.key_text(node.child_by_field_name("key")), "method", scope)
		return None

	def _on_assignment_expression(self, node, scope):
		left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
		left_text = n.compact(n.text(left))
		for registry, kind in fp.REGISTRIES.items():
			if left_text.startswith(registry + "["):
				key = n.registry_key(left, registry)
				if key:
					self._ref(kind, key, registry, node, scope, "register")
					if kind == "report":
						self.desk.on_report(right, key, scope)
					return [*scope, f"{kind}:{key}"]
		self.desk.on_assignment(left_text, right, node, scope)
		namespace = self.setup.namespace(left_text, right)
		if namespace:
			return namespace
		if (
			right is not None
			and left.type == "member_expression"
			and right.type in ("class", "class_expression")
		):
			return self._symbol(node, left_text, "class", [], qualname=left_text, signature=n.heritage(right))
		if right is not None and right.type in FUNCTION_TYPES and left.type == "member_expression":
			return self._symbol(node, left_text, "function", [], qualname=left_text)
		return None

	def _on_import_statement(self, node, scope):
		source = n.string(node.child_by_field_name("source"))
		if not source:
			return None
		self._ref("import", source, "import", node, scope, "import")
		for name in n.imported_names(node):
			# target = exported name, via = module: "which files use createResource from frappe-ui".
			self._ref("import_name", name, source, node, scope, "import")
		return None

	def _on_template_string(self, node, scope):
		value = n.pattern(node)
		self.ui.on_url(value, node, scope)
		# `${METHOD}.x` with METHOD a constant or a `this.x` path: each path it can build.
		built = self.values.value(node)[0] if "*" in value else None
		for path in built or [value]:
			self._dotted(path, node, scope)
		return None

	def _on_string(self, node, scope):
		value = n.string(node)
		self.ui.on_url(value, node, scope)
		self._dotted(value, node, scope)
		return None

	# Calls

	def _on_call_expression(self, node, scope):
		func = node.child_by_field_name("function")
		callee = (
			n.compact(n.text(func))
			if func is not None and func.type in ("identifier", "member_expression")
			else ""
		)
		if not callee:
			return None
		self.facts.calls.append(Call(self._scope_name(scope), callee, "", self._line(node)))
		args = n.arguments(node)
		self.values.on_call(callee, args, scope)
		if callee in fp.FORM_EVENT_CALLS:
			return self.setup.on_form_on(node, args, scope)
		self._server_call(callee, args, node, scope)
		self._doctype_call(callee, args, node, scope)
		self.ui.on_call(callee, func, args, node, scope)
		self.desk.on_call(callee, args, node, scope)
		self.setup.on_call(callee, args, node, scope)
		if callee.endswith("frm.trigger") and args:
			self._ref("form_trigger", n.string(args[0]), callee, node, scope, "call")
		if callee in fp.CSCRIPT_EXTENDS and len(args) > 1:
			return self.setup.namespace(n.compact(n.text(args[0])), args[1])
		return None

	def _on_new_expression(self, node, scope):
		self.setup.on_new(node, scope)
		return None

	def _server_call(self, callee, args, node, scope):
		if not args:
			return
		first = args[0]
		if callee in fp.DOC_METHOD_CALLS or callee.endswith(".frm.call"):
			if not self.values.on_doc_call(n.object_get(first, "method"), n.object_get(first, "doc")):
				self.desk.on_doc_call(callee, first, node, scope)
			return
		if callee in fp.URL_OPTION_CALLS:
			self._method_path(callee, n.object_get(args[0], "url"), scope)
			return
		if callee not in fp.SERVER_CALLS:
			return
		if callee in fp.DOC_OPTION_CALLS and n.object_get(first, "doc") is not None:
			if not self.values.on_doc_call(n.object_get(first, "method"), n.object_get(first, "doc")):
				self.desk.on_doc_call(callee, first, node, scope)
			return
		if self.values.on_server_call(callee, args, node, scope):
			return
		# `method` may be an HTTP verb ("POST") next to `url`; keep the first path-like value.
		for candidate in (first, n.object_get(first, "method"), n.object_get(first, "url")):
			method = self._method_path(callee, candidate, scope)
			if method:
				self.data.on_client_method(method, args, node, scope)
				return

	def _method_path(self, callee, candidate, scope) -> str:
		"""A literal path, a constant from this file, or a `method_const` ref for another file's."""
		raw = n.string(candidate)
		if raw and fp.looks_like_method(raw):
			# Report on the string's line so it collapses with its `dotted` ref.
			method = fp.normalize_method(raw)
			self._ref("method", method, callee, candidate, scope, "call")
			return method
		name = constants.reference(candidate)
		if name in self.consts:
			method = self.consts[name][0]
			self._ref("method", method, f"{callee}({name})", candidate, scope, "call")
			return method
		if name:
			self._ref("method_const", name, callee, candidate, scope, "call")
		return ""

	def _dotted(self, value, node, scope):
		"""A dotted path, or an "/api/method/<dotted>" URL (open_url_post, window.open, href)."""
		via = "url" if fp.API_URL_RE.match(value) else "string"
		path = fp.normalize_method(value) if via == "url" else value
		if DOTTED_RE.match(path) and path.split(".")[0] in self.known_apps:
			self._ref("dotted", path, via, node, scope, "call")

	def _doctype_call(self, callee, args, node, scope):
		if not args:
			return
		access = fp.DOCTYPE_CALLS.get(callee)
		if access:
			self.data.on_doctype_call(callee, args, access, node, scope)
		elif (
			callee in fp.ROUTE_CALLS
			and len(args) > 1
			and n.string(args[0]) in ("Form", "List", "Tree", "Report")
		):
			doctype = n.string(args[1])
			if doctype:
				self._ref("doctype", doctype, callee, node, scope, "route")

	# Helpers

	def _symbol(self, node, name, kind, scope, qualname="", signature=None):
		if not name:
			return None
		parent = self._scope_name(scope)
		qual = qualname or ".".join([*scope, name])
		self.facts.symbols.append(
			Symbol(
				name=name,
				qualname=qual,
				kind=kind,
				line=self._line(node),
				end_line=node.end_point[0] + 1 + self.offset,
				signature=n.params(node) if signature is None else signature,
				parent="" if parent == "<module>" else parent,
			)
		)
		return [*scope, name] if not qualname else qualname.split(".")

	def _ref(self, kind, target, via, node, scope, access):
		if target:
			self.facts.refs.append(Ref(kind, target, via, self._line(node), self._scope_name(scope), access))

	def _scope_name(self, scope: list[str]) -> str:
		return ".".join(scope) if scope else "<module>"

	def _line(self, node: Node) -> int:
		return node.start_point[0] + 1 + self.offset
