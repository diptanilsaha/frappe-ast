"""frappe-ui calls that run a DocType controller method and name the DocType themselves.

Their ref target is "<DocType>:<method>". Desk calls (frm.call) leave the DocType out,
because it is the form's own DocType, and queries take it from the file's folder.
"""

from urllib.parse import unquote

from tree_sitter import Node

from frappe_ast.js import frappe_patterns as fp
from frappe_ast.js import nodes as n


class DocMethodRefs:
	def __init__(self, ref):
		self.ref = ref  # _Walker._ref
		self.handles: dict[str, str] = {}  # variable -> DocType, from `x = useDoctype("X")`

	def bind(self, name: Node | None, value: Node | None):
		doctype = _handle_doctype(value, {})
		if name is not None and name.type == "identifier" and doctype:
			self.handles[n.text(name)] = doctype

	def on_call(self, callee: str, func: Node, args: list[Node], node: Node, scope: list[str]):
		if not args:
			return
		if callee in fp.DOC_METHOD_MAPS:
			self._method_map(callee, args[0], scope)
		elif callee.endswith(".submit"):
			self._runner(callee, func, args[0], node, scope)

	def on_url(self, value: str, node: Node, scope: list[str]):
		for pattern, kind in fp.URL_METHODS:
			match = pattern.search(value)
			if match:
				target = f"{unquote(match.group(1))}:{match.group(2)}"
				self.ref(kind, target, "url", node, scope, "call")
				return

	def _method_map(self, callee: str, options: Node, scope: list[str]):
		"""useDoc({doctype, methods: {k: "m" | {name: "m"}}}) and createDocumentResource's twin."""
		doctype = n.string(n.object_get(options, "doctype"))
		option, key = fp.DOC_METHOD_MAPS[callee]
		methods = n.object_get(options, option)
		if not doctype or methods is None or methods.type != "object":
			return
		for _, value in n.object_members(methods):
			method = n.string(value) or n.string(n.object_get(value, key))
			if method:
				self.ref("doc_method", f"{doctype}:{method}", callee, value, scope, "call")

	def _runner(self, callee: str, func: Node, payload: Node, node: Node, scope: list[str]):
		"""useDoctype("X").runDocMethod.submit({method: "m"}), or through a variable."""
		runner = func.child_by_field_name("object")
		if runner is None or runner.type != "member_expression":
			return
		kind = fp.DOCTYPE_RUNNERS.get(n.text(runner.child_by_field_name("property")))
		doctype = _handle_doctype(runner.child_by_field_name("object"), self.handles)
		method = n.string(n.object_get(payload, "method"))
		if kind and doctype and method:
			self.ref(kind, f"{doctype}:{method}", callee.split(".")[-2], node, scope, "call")


def _handle_doctype(node: Node | None, handles: dict[str, str]) -> str:
	if node is None:
		return ""
	if node.type == "identifier":
		return handles.get(n.text(node), "")
	if node.type == "call_expression" and n.text(node.child_by_field_name("function")) in fp.DOCTYPE_HANDLES:
		args = n.arguments(node)
		return n.string(args[0]) if args else ""
	return ""
