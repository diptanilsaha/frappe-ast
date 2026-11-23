"""Which desk forms a piece of JS runs on, for code that is not in a form's own scripts.

The forms of code at (path, scope), first match wins:
1. Inside frappe.ui.form.on(X): X, unless X is a child table (then the forms around it). "@self" is
   the forms around it, "@self Item" their "<DocType> Item", "$" each DocType that a call of the
   enclosing setup function passes (`setup_call` refs).
2. The file's own forms (FormScripts.doctypes_for_file: folder, doctype_js hooks, frappe.ui.form.on).
3. The nearest enclosing JS class: forms that install it or a subclass as frm.cscript, or create it
   with `new` (`js_new` refs). The nearest enclosing function: the forms of its call sites.
Code at the top level of a shared file runs wherever its bundle loads: no form.
"""

from frappe_ast.queries.forms import FormScripts
from frappe_ast.queries.resolver import Resolver
from frappe_ast.store import Store

MAX_DEPTH = 4
CHILD_FLAG = '"istable": 1'


class FormContexts:
	def __init__(self, store: Store, resolver: Resolver, forms: FormScripts | None = None):
		self.store = store
		self.resolver = resolver
		self.forms = forms or FormScripts(store, resolver)
		self._cache: dict[tuple[str, str], list[str]] = {}
		self._class_forms: dict[str, set[str]] | None = None
		self._truncated = False

	def doctypes(self, path: str, scope: str, depth: int = 0) -> list[str]:
		key = (path, scope)
		if key in self._cache:
			return self._cache[key]
		if depth > MAX_DEPTH:
			self._truncated = True
			return []
		outer, self._truncated = self._truncated, False
		found = self._resolve(path, scope, depth)
		# A result cut short by MAX_DEPTH (a call chain, or a cycle) is not this scope's answer.
		if not self._truncated:
			self._cache[key] = found
		self._truncated = self._truncated or outer
		return found

	def registrations(self, doctype: str) -> list[dict]:
		"""Handlers that shared code registers on `doctype` or, while it runs there, on another
		DocType such as a child table. Rows look like FormReport's form_event rows."""
		out = []
		for row in self.store.rows(
			"select f.path, r.kind, r.line, r.target, r.scope from refs r join files f on f.id = r.file_id "
			"where r.kind in ('setup_event', 'form_event') and f.app not like '%@%' order by f.path, r.line"
		):
			if row["kind"] == "form_event" and self._form_script(row["path"]):
				continue  # a form's own script: FormReport reads it directly
			tag, _, event = row["target"].rpartition(":")
			for target in self._targets(tag, row, doctype):
				out.append({"path": row["path"], "line": row["line"], "target": f"{target}:{event}"})
		return out

	def class_forms(self) -> dict[str, set[str]]:
		"""JS class qualname -> forms that install it or a subclass as frm.cscript, or `new` it."""
		if self._class_forms is None:
			self._class_forms = {}
			for ref in self._rows("form_controller"):
				self._add_class(ref["target"], self.forms.doctypes_for_file(ref["path"]))
			for ref in self._rows("js_new"):
				self._add_class(ref["target"], self.doctypes(ref["path"], ref["scope"], 1))
		return self._class_forms

	def _resolve(self, path: str, scope: str, depth: int) -> list[str]:
		at = _form_segment(scope)
		if at >= 0:
			return self._in_form_on(path, scope, at, depth)
		own = self.forms.doctypes_for_file(path)
		if own:
			return own
		for symbol in self._enclosing(path, scope):
			if symbol["kind"] == "class":
				return sorted(self.class_forms().get(symbol["qualname"], ()))
			callers = self._call_site_forms(symbol["qualname"], depth)
			if callers:
				return callers
		return []

	def _in_form_on(self, path: str, scope: str, at: int, depth: int) -> list[str]:
		tag = scope[at + 5 :].split(".")[0]
		outer = scope[: at - 1] if at else "<module>"
		if tag.startswith("@self"):
			return [d + tag[5:] for d in self.doctypes(path, outer, depth + 1)]
		if tag == "$":
			return sorted({r["target"] for r in self._setup_calls(outer)})
		record = self.resolver.doctype(tag)
		if record is None or CHILD_FLAG not in (record["flags"] or ""):
			return [tag]
		return self.doctypes(path, outer, depth + 1) or [tag]

	def _targets(self, tag: str, row: dict, doctype: str) -> list[str]:
		"""DocTypes this registration adds handlers to that matter on form `doctype`: the form
		itself, or any DocType it names while it runs on that form (its child tables)."""
		if tag == "$":
			calls = self._setup_calls(row["scope"])
			return sorted({c["target"] for c in calls if c["target"] == doctype or doctype in self._host(c)})
		if tag.startswith("@self"):
			hosts = self.doctypes(row["path"], row["scope"])
			return [doctype + tag[5:]] if doctype in hosts else []
		if tag == doctype or doctype in self.doctypes(row["path"], row["scope"]):
			return [tag]
		return []

	def _form_script(self, path: str) -> bool:
		return bool(self.resolver.doctype_for_file(path) or self.forms.hooked().get(path))

	def _host(self, call: dict) -> list[str]:
		return self.doctypes(call["path"], call["scope"], 1)

	def _setup_calls(self, function: str) -> list[dict]:
		return self.store.rows(
			"select f.path, r.target, r.scope from refs r join files f on f.id = r.file_id "
			"where r.kind = 'setup_call' and r.via = ?",
			function,
		)

	def _call_site_forms(self, qualname: str, depth: int) -> list[str]:
		rows = self.store.rows(
			"select distinct f.path, c.caller from calls c join files f on f.id = c.file_id where c.callee = ?",
			qualname,
		)
		return sorted({d for r in rows for d in self.doctypes(r["path"], r["caller"], depth + 1)})

	def _enclosing(self, path: str, scope: str) -> list[dict]:
		"""Class and function symbols whose qualname is `scope` or a prefix of it, innermost first."""
		return self.store.rows(
			"select s.qualname, s.kind from symbols s join files f on f.id = s.file_id "
			"where f.path = ? and s.kind in ('class', 'function', 'method') "
			"and (? = s.qualname or ? like s.qualname || '.%') order by length(s.qualname) desc",
			path,
			scope,
			scope,
		)

	def _add_class(self, qualname: str, doctypes: list[str]):
		for cls in self.forms.chain(qualname) if doctypes else []:
			self._class_forms.setdefault(cls["qualname"], set()).update(doctypes)

	def _rows(self, kind: str) -> list[dict]:
		return self.store.rows(
			"select distinct f.path, r.target, r.scope from refs r join files f on f.id = r.file_id "
			"where r.kind = ? and f.app not like '%@%'",
			kind,
		)


def _form_segment(scope: str) -> int:
	"""Index of the innermost "form:" scope segment, or -1."""
	at = scope.rfind("form:")
	while at > 0 and scope[at - 1] != ".":
		at = scope.rfind("form:", 0, at)
	return at
