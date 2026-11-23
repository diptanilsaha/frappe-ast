"""Method paths that the code builds, and functions that pass a method name through.

- A constant prefix: `${METHOD}.convert_all`, `METHOD + ".x"`, `${this.method_path}.x` (with
  `this.method_path = "app.api"` in the same file).
- Wrappers: `function api(method) { return frappe.xcall(`${METHOD}.${method}`) }` and
  `function call_action(frm, method) { frm.call({method}) }`. Call sites may come before the
  definition, so the walker records every call and `finish()` matches them after the walk.
- run_doc_method named directly: `frappe.xcall("run_doc_method", {dt, dn, method})`,
  `frappe.call({method: "run_doc_method", args: {docs, method}})`, `createResource({url:
  "run_doc_method", makeParams: () => ({dt, dn, method})})`.
- frappe.call({module, page, method}): `<module>.page.<page>.<page>.<method>` (request.js).
"""

import re

from tree_sitter import Node

from frappe_ast.js import constants
from frappe_ast.js import frappe_patterns as fp
from frappe_ast.js import nodes as n
from frappe_ast.js.desk import doc_target
from frappe_ast.js.nodes import FUNCTION_TYPES

PARAM = "\0"  # stands for the wrapper's parameter inside a built path
NAME_RE = re.compile(r"^[a-z_]\w*$")
SCOPES = (*FUNCTION_TYPES, "method_definition", "function_declaration", "program")


class MethodValues:
	def __init__(self, ref, consts: dict, root: Node):
		self.ref = ref  # _Walker._ref
		self.consts = consts
		self.root = root
		self.props = _this_props(root)
		self.wrappers: dict[str, tuple[str, int, str]] = {}  # name -> (kind, param index, template)
		self.sites: list[tuple[str, list[Node], list[str]]] = []

	def on_server_call(self, callee: str, args: list[Node], node: Node, scope: list[str]) -> bool:
		"""Refs for a server call whose method is built, explicit run_doc_method or a page method."""
		first = args[0]
		options = first if first.type == "object" else None
		candidate = n.object_get(options, "method") if options is not None else first
		if options is not None and n.object_get(options, "module") is not None:
			return self._page_method(callee, options, node, scope)
		raw = n.string(candidate) or n.string(n.object_get(options, "url"))
		if fp.normalize_method(raw) == "run_doc_method":
			return self._run_doc_method(callee, first, args, node, scope)
		if fp.normalize_method(raw) in fp.HANDLER_METHODS:
			self.ref("method", "frappe.handler." + fp.normalize_method(raw), callee, node, scope, "call")
			return True
		if NAME_RE.match(raw) and n.object_get(options, "docs") is not None:
			# call({docs, method}) from useFrappePostCall("run_doc_method") (frappe-react-sdk).
			target = doc_target(n.object_get(options, "docs")) + raw
			self.ref("doc_method", target, "run_doc_method", node, scope, "call")
			return True
		values, name = self.value(candidate)
		if not values or (not name and not any(PARAM in v for v in values)):
			return False
		for value in (fp.normalize_method(v) for v in values):
			if PARAM in value:
				self._wrapper("method", value, candidate)
			elif fp.looks_like_method(value):
				self.ref("method", value, f"{callee}({name})", candidate, scope, "call")
		return True

	def on_doc_call(self, method: Node | None, doc: Node | None) -> bool:
		"""frm.call({method}) or frappe.call({method, doc}) inside a function that takes `method`."""
		values, _ = self.value(method)
		if values != [PARAM]:
			return False
		if doc is None:
			return self._wrapper("module_method", PARAM, method)
		return self._wrapper("doc_method", doc_target(doc) + PARAM, method)

	def on_call(self, callee: str, args: list[Node], scope: list[str]):
		if args:
			self.sites.append((callee, args, scope))

	def finish(self):
		for callee, args, scope in self.sites:
			name = callee.replace("me.", "this.", 1)
			kind, index, template = self.wrappers.get(name) or self.wrappers.get(callee) or ("", 0, "")
			literal = n.string(args[index]) if kind and index < len(args) else ""
			if not NAME_RE.match(literal):
				continue
			target = template.replace(PARAM, literal)
			if kind != "method" or fp.looks_like_method(target):
				self.ref(kind, target, callee, args[index], scope, "call")

	def value(self, node: Node | None, depth: int = 0) -> tuple[list[str] | None, str]:
		"""(possible paths, PARAM standing for a parameter; first constant or variable used), or
		(None, "") when unknown. A variable assigned in branches has one value per assignment."""
		if node is None or depth > 3:
			return None, ""
		if node.type in ("string", "string_fragment"):
			return [n.string(node)], ""
		if node.type == "template_string":
			return self._join(_template_parts(node), depth)
		if node.type == "binary_expression" and n.text(node.child_by_field_name("operator")) == "+":
			return self._join([node.child_by_field_name("left"), node.child_by_field_name("right")], depth)
		if node.type == "parenthesized_expression" and node.named_children:
			return self.value(node.named_children[0], depth + 1)
		name = n.compact(n.text(node))
		if node.type in ("identifier", "shorthand_property_identifier") and name in n.enclosing_params(node):
			return [PARAM], ""
		if name in self.props:
			return [self.props[name]], name
		const = constants.reference(node)
		if const in self.consts:
			return [self.consts[const][0]], const
		if node.type in ("identifier", "shorthand_property_identifier"):
			return self._variable(node, name, depth)
		return None, ""

	def _variable(self, node: Node, name: str, depth: int) -> tuple[list[str] | None, str]:
		"""`var prefix = "app.x."` or `method = "a"` / `method = "b"` in the declaring scope."""
		values = []
		for expr in _assigned(node, name, self.root):
			found, _ = self.value(expr, depth + 1)
			if found is None:
				return None, ""
			values += found
		return (sorted(set(values)), name) if values and len(set(values)) <= 8 else (None, "")

	def _join(self, parts: list, depth: int) -> tuple[list[str] | None, str]:
		out, first = [""], ""
		for part in parts:
			found, name = ([part], "") if isinstance(part, str) else self.value(part, depth + 1)
			if found is None:
				return None, ""
			out = [a + b for a in out for b in found][:8]
			first = first or name
		out = [v for v in out if v.count(PARAM) <= 1]
		return (out, first) if out else (None, "")

	def _wrapper(self, kind: str, template: str, node: Node) -> bool:
		fn = _enclosing_function(node)
		if fn is None:
			return False
		name, params = fn
		names = _identifiers(node)
		# `let url = `...${method}`; fetch(url)`: the parameter is in the variable's value.
		names += [i for x in names for e in _assigned(node, x, self.root) for i in _identifiers(e)]
		used = [p for p in names if p in params]
		if used:
			self.wrappers[name] = (kind, params.index(used[0]), template)
		return True

	def _page_method(self, callee: str, options: Node, node: Node, scope: list[str]) -> bool:
		module, page = n.string(n.object_get(options, "module")), n.string(n.object_get(options, "page"))
		method = n.string(n.object_get(options, "method"))
		if module and page and NAME_RE.match(method):
			self.ref("method", f"{module}.page.{page}.{page}.{method}", callee, node, scope, "call")
		return True

	def _run_doc_method(self, callee, first, args, node, scope) -> bool:
		params = _run_doc_params(first, args)
		method = n.string(n.object_get(params, "method"))
		if not NAME_RE.match(method):
			return True
		dt, docs = n.string(n.object_get(params, "dt")), n.object_get(params, "docs")
		prefix = f"{dt}:" if dt else doc_target(docs) if docs is not None else "?:"
		self.ref("doc_method", prefix + method, "run_doc_method", node, scope, "call")
		return True


def _run_doc_params(first: Node, args: list[Node]) -> Node | None:
	"""The {dt, dn, method} or {docs, method} object of a run_doc_method call."""
	if first.type != "object":
		return args[1] if len(args) > 1 else None
	for key in ("args", "params"):
		if n.object_get(first, key) is not None:
			return n.object_get(first, key)
	make = n.object_get(first, "makeParams")
	body = make.child_by_field_name("body") if make is not None else None
	while body is not None and body.type == "parenthesized_expression" and body.named_children:
		body = body.named_children[0]
	return body if body is not None and body.type == "object" else None


def _assigned(node: Node, name: str, root: Node) -> list[Node]:
	"""Initial value and later assignments of the local `name`, in the scope that declares it."""
	bound = n.binding(node, name)
	if bound is not None and bound[0] == "param":
		return []
	scope = node.parent
	while scope is not None and not (scope.type in SCOPES and _declarator(scope, name) is not None):
		scope = scope.parent
	declarator = _declarator(scope, name) if scope is not None else None
	if declarator is None:
		return []
	found = [declarator.child_by_field_name("value")] if declarator.child_by_field_name("value") else []
	stack = [scope.child_by_field_name("body") or scope]
	while stack:
		x = stack.pop()
		if x.type == "assignment_expression" and n.text(x.child_by_field_name("left")) == name:
			found.append(x.child_by_field_name("right"))
		stack += [
			c for c in x.named_children if c.type not in (*FUNCTION_TYPES, "function_declaration", "class")
		]
	return found


def _declarator(scope: Node, name: str) -> Node | None:
	"""`var/let/const name` declared directly in `scope` (a function body or the program)."""
	stack = [scope.child_by_field_name("body") or scope]
	while stack:
		x = stack.pop()
		if x.type == "variable_declarator" and n.text(x.child_by_field_name("name")) == name:
			return x
		stack += [
			c for c in x.named_children if c.type not in (*FUNCTION_TYPES, "function_declaration", "class")
		]
	return None


def _identifiers(node: Node) -> list[str]:
	stack, found = [node], []
	while stack:
		x = stack.pop()
		if x.type in ("identifier", "shorthand_property_identifier"):
			found.append(n.text(x))
		stack.extend(x.named_children)
	return found


def _template_parts(node: Node) -> list:
	parts = []
	for child in node.children:
		if child.type == "template_substitution":
			parts.append(child.named_children[0] if child.named_children else "")
		elif child.type != "`":
			parts.append(n.text(child))
	return parts


def _enclosing_function(node: Node) -> tuple[str, list[str]] | None:
	"""(call name, parameter names) of the function around `node`: `api`, or `this.call` for a method."""
	fn = node.parent
	while fn is not None and fn.type not in (*FUNCTION_TYPES, "method_definition", "function_declaration"):
		fn = fn.parent
	if fn is None:
		return None
	params = n.enclosing_params(fn.child_by_field_name("body") or fn)
	if fn.type == "method_definition":
		return "this." + n.text(fn.child_by_field_name("name")), params
	name = fn.child_by_field_name("name")
	if name is None and fn.parent is not None and fn.parent.type == "variable_declarator":
		name = fn.parent.child_by_field_name("name")
	return (n.text(name), params) if name is not None else None


def _this_props(root: Node) -> dict[str, str]:
	"""`this.method_path = "app.api"`: properties a class sets once to a method path."""
	found: dict[str, set[str]] = {}
	stack = [root]
	while stack:
		node = stack.pop()
		if node.type == "assignment_expression":
			left = n.compact(n.text(node.child_by_field_name("left")))
			value = n.string(node.child_by_field_name("right"))
			if left.startswith("this.") and value and fp.looks_like_method(value):
				found.setdefault(left, set()).add(value)
		stack.extend(node.named_children)
	return {k: v.pop() for k, v in found.items() if len(v) == 1}
