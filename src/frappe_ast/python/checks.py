"""Frappe-aware Python checks. Each rule is a heuristic with a stable id.

Rules (id, default severity):
  FA101 warning  SQL formatted with a function parameter (f-string, %, .format, +)
        info     SQL formatted with other values (table names, built conditions)
  FA102 warning  frappe.db.commit() inside an endpoint or a lifecycle method
  FA103 info     endpoint open to Guest (allow_guest=True)
  FA104 warning  ignore_permissions inside an endpoint
  FA105 warning  endpoint writes with frappe.db.* and has no permission check
  FA106 info     database call inside a loop (N+1)
  FA107 info     frappe.throw/msgprint message not wrapped in _()
  FA108 warning  frappe.get_all inside an endpoint (skips user permissions)
"""

import ast

from frappe_ast.model import Finding
from frappe_ast.python.astutil import dotted, truthy
from frappe_ast.python.frappe_api import DOCTYPE_APIS, LIFECYCLE_METHODS

SQL_APIS = {"frappe.db.sql", "frappe.db.multisql", "frappe.db.sql_list", "frappe.db.sql_ddl"}
UNCHECKED_WRITES = {"frappe.db.set_value", "frappe.db.delete", "frappe.db.set_single_value", "frappe.db.sql"}
PERMISSION_CALLS = (
	"has_permission",
	"check_permission",
	"only_for",
	"is_owner",
	"validate_permission",
	"frappe.get_list",
	"has_access",
)
MESSAGE_APIS = {"frappe.throw", "frappe.msgprint"}


class PythonChecks:
	def __init__(self, imports):
		self.imports = imports
		self.findings: list[Finding] = []

	def run(self, tree: ast.Module) -> list[Finding]:
		for node in ast.walk(tree):
			if isinstance(node, ast.ClassDef):
				for item in node.body:
					if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
						self._check_function(item, f"{node.name}.{item.name}", in_class=True)
			elif isinstance(node, ast.Module):
				for item in node.body:
					if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
						self._check_function(item, item.name, in_class=False)
		self._check_loops(tree)
		return self.findings

	def _check_function(self, fn, scope: str, in_class: bool):
		endpoint = self._is_whitelisted(fn)
		lifecycle = in_class and fn.name in LIFECYCLE_METHODS
		calls = [(n, self._api(n)) for n in ast.walk(fn) if isinstance(n, ast.Call)]
		if endpoint and self._allows_guest(fn):
			self._add("FA103", "info", "endpoint allows Guest; validate every argument", fn.lineno, scope)
		params = {a.arg for a in [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]} - {"self", "cls"}
		for extra in (fn.args.vararg, fn.args.kwarg):
			if extra:
				params.add(extra.arg)
		for call, api in calls:
			self._check_call(call, api, scope, endpoint, lifecycle, params)
		if endpoint and not in_class:
			self._check_unchecked_writes(fn, calls, scope)

	def _check_call(
		self, call: ast.Call, api: str, scope: str, endpoint: bool, lifecycle: bool, params: set[str]
	):
		if api in SQL_APIS and call.args and _is_dynamic_string(call.args[0]):
			tainted = sorted(_formatted_names(call.args[0]) & params)
			if tainted:
				msg = f"{api} formats parameter(s) {', '.join(tainted)} into SQL; pass them as query values"
				self._add("FA101", "warning", msg, call.lineno, scope)
			else:
				self._add(
					"FA101",
					"info",
					f"{api} with a formatted query; check what is interpolated",
					call.lineno,
					scope,
				)
		if api == "frappe.db.commit" and (endpoint or lifecycle):
			where = "endpoint" if endpoint else "lifecycle method"
			msg = f"frappe.db.commit() in {where}; the request commits on success"
			self._add("FA102", "warning", msg, call.lineno, scope)
		if endpoint and any(kw.arg == "ignore_permissions" and truthy(kw.value) for kw in call.keywords):
			msg = "ignore_permissions=True in an endpoint; check permission first"
			self._add("FA104", "warning", msg, call.lineno, scope)
		if endpoint and api == "frappe.get_all":
			msg = "frappe.get_all skips user permissions; use frappe.get_list in endpoints"
			self._add("FA108", "warning", msg, call.lineno, scope)
		if api in MESSAGE_APIS and call.args and _is_plain_message(call.args[0]):
			self._add("FA107", "info", f"{api} message is not translatable; wrap in _()", call.lineno, scope)

	def _check_unchecked_writes(self, fn, calls, scope: str):
		writes = [(c, api) for c, api in calls if api in UNCHECKED_WRITES and _is_write(c, api)]
		if not writes:
			return
		text = ast.unparse(fn)
		if any(token in text for token in PERMISSION_CALLS):
			return
		call, api = writes[0]
		msg = f"endpoint calls {api} (no permission check) and has no has_permission/only_for"
		self._add("FA105", "warning", msg, call.lineno, scope)

	def _check_loops(self, tree: ast.Module):
		for loop in ast.walk(tree):
			if not isinstance(loop, ast.For | ast.While | ast.AsyncFor):
				continue
			for stmt in loop.body:
				for call in (n for n in ast.walk(stmt) if isinstance(n, ast.Call)):
					api = self._api(call)
					if api in DOCTYPE_APIS or api in SQL_APIS:
						self._add("FA106", "info", f"{api} inside a loop; batch the query", call.lineno)
		self._dedupe()

	def _is_whitelisted(self, fn) -> bool:
		return any(self._deco_name(d) == "frappe.whitelist" for d in fn.decorator_list)

	def _allows_guest(self, fn) -> bool:
		for deco in fn.decorator_list:
			if isinstance(deco, ast.Call) and self._deco_name(deco) == "frappe.whitelist":
				return any(kw.arg == "allow_guest" and truthy(kw.value) for kw in deco.keywords)
		return False

	def _deco_name(self, deco: ast.expr) -> str:
		target = deco.func if isinstance(deco, ast.Call) else deco
		name = dotted(target)
		return (self.imports.resolve(name) or name) if name else ""

	def _api(self, call: ast.Call) -> str:
		name = dotted(call.func)
		return (self.imports.resolve(name) or name) if name else ""

	def _add(self, rule: str, severity: str, message: str, line: int, scope: str = ""):
		self.findings.append(Finding(rule, severity, message, line, scope))

	def _dedupe(self):
		seen, unique = set(), []
		for f in self.findings:
			key = (f.rule, f.line)
			if key not in seen:
				seen.add(key)
				unique.append(f)
		self.findings = unique


def _is_dynamic_string(node: ast.expr) -> bool:
	if isinstance(node, ast.JoinedStr):
		return any(isinstance(v, ast.FormattedValue) for v in node.values)
	if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod | ast.Add):
		return _has_string(node)
	if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
		return True
	return False


def _formatted_names(node: ast.expr) -> set[str]:
	"""Names that flow into the formatted parts of a query string."""
	if isinstance(node, ast.JoinedStr):
		parts = [v.value for v in node.values if isinstance(v, ast.FormattedValue)]
	elif isinstance(node, ast.BinOp):
		parts = [node.right] if isinstance(node.op, ast.Mod) else [node.left, node.right]
	elif isinstance(node, ast.Call):
		parts = [*node.args, *(kw.value for kw in node.keywords)]
	else:
		parts = []
	return {n.id for part in parts for n in ast.walk(part) if isinstance(n, ast.Name)}


def _has_string(node: ast.expr) -> bool:
	return any(isinstance(n, ast.Constant) and isinstance(n.value, str) for n in ast.walk(node))


def _is_write(call: ast.Call, api: str) -> bool:
	if api != "frappe.db.sql":
		return True
	first = call.args[0] if call.args else None
	text = first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else ""
	return text.lstrip().lower().startswith(("update", "delete", "insert"))


def _is_plain_message(node: ast.expr) -> bool:
	return isinstance(node, ast.JoinedStr) or (isinstance(node, ast.Constant) and isinstance(node.value, str))
