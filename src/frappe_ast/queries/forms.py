"""Which desk form a JS file serves, and the JS controller class behind a form.

A file serves the form of: the DocType whose folder holds it, the DocTypes a doctype_js /
doctype_list_js hook maps it to (in any app), or else the non-child DocTypes it passes to
frappe.ui.form.on. `frm` in that file is that form, also inside child-table handlers.
"""

from pathlib import PurePath

from frappe_ast.queries.resolver import Resolver
from frappe_ast.store import Store

FORM_EVENTS = {
	"setup", "onload", "refresh", "onload_post_render", "before_load", "validate", "before_save",
	"after_save", "before_submit", "on_submit", "before_cancel", "after_cancel", "timeline_refresh",
	"before_workflow_action", "after_workflow_action", "form_render", "before_discard", "after_discard",
	"on_hide", "after_rename", "before_rename",
}  # fmt: skip
ROW_EVENT_SUFFIXES = ("_add", "_remove", "_move", "_before_remove", "_on_form_rendered")
SCRIPT_HOOKS = ("doctype_js", "doctype_list_js", "doctype_tree_js", "doctype_calendar_js")
MAX_CHAIN = 12


class FormScripts:
	def __init__(self, store: Store, resolver: Resolver | None = None):
		self.store = store
		self.resolver = resolver or Resolver(store)
		self._hooked: dict[str, list[str]] | None = None
		self._file_doctypes: dict[str, list[str]] = {}

	def doctypes_for_file(self, path: str) -> list[str]:
		"""The forms `frm` can be in this file: folder, then hooks, then frappe.ui.form.on."""
		if path not in self._file_doctypes:
			self._file_doctypes[path] = self._doctypes_for_file(path)
		return self._file_doctypes[path]

	def _doctypes_for_file(self, path: str) -> list[str]:
		own = self.resolver.doctype_for_file(path)
		hooked = self.hooked().get(path, [])
		if own or hooked:
			return [d for d in [own, *hooked] if d]
		# `+r.kind` keeps SQLite on the file index: this file has few refs, the bench has many.
		rows = self.store.rows(
			"select distinct r.target from refs r where r.file_id = (select id from files where path = ?) "
			"and +r.kind = 'doctype' and r.via = 'frappe.ui.form.on' and r.target in "
			"(select name from doctypes where flags not like '%\"istable\": 1%')",
			path,
		)
		return [r["target"] for r in rows]

	def scripts(self, doctype: str) -> list[str]:
		"""JS files desk loads for this DocType, in load order (= handler registration order).

		frappe/desk/form/meta.py: <name>.js, regional/<country>.js, <name>_list.js,
		regional/<country>_list.js, _kanban, _calendar, _tree, then doctype_js hooks by app.
		"""
		return [path for path, _ in self.labelled_scripts(doctype)]

	def labelled_scripts(self, doctype: str) -> list[tuple[str, str]]:
		record = self.resolver.doctype(doctype)
		out: list[tuple[str, str]] = []
		if record:
			base = record["path"].removesuffix(".json")
			folder = str(PurePath(base).parent)
			regional = self.store.rows(
				"select path from files where path like ? and lang = 'js' order by path",
				folder + "/regional/%.js",
			)
			form_regional = [r["path"] for r in regional if not r["path"].endswith("_list.js")]
			list_regional = [r["path"] for r in regional if r["path"].endswith("_list.js")]
			candidates = [(base + ".js", "form")]
			candidates += [
				(p, f"form, only when System Settings country is {PurePath(p).stem}") for p in form_regional
			]
			candidates += [(base + "_list.js", "list")]
			candidates += [
				(p, f"list, only when System Settings country is {PurePath(p).stem[:-5]}")
				for p in list_regional
			]
			candidates += [(base + f"_{view}.js", view) for view in ("kanban", "calendar", "tree")]
			known = {
				r["path"] for r in self.store.rows("select path from files where path like ?", folder + "/%")
			}
			out = [(p, label) for p, label in candidates if p in known]
		for path, doctypes in self.hooked().items():
			if doctype in doctypes:
				out.append((path, f"{self._hook_key(path, doctype)} hook from {path.split('/')[1]}"))
		return out

	def _hook_key(self, path: str, doctype: str) -> str:
		app = path.split("/")[1]
		rows = self.store.rows(
			"select key from hooks where app = ? and p1 = ? and ? like '%' || value", app, doctype, path
		)
		return rows[0]["key"] if rows else "doctype_js"

	def hooked(self) -> dict[str, list[str]]:
		"""File path -> DocTypes, from doctype_js and friends in every app's hooks.py."""
		if self._hooked is None:
			self._hooked = {}
			marks = ",".join("?" * len(SCRIPT_HOOKS))
			for hook in self.store.rows(
				f"select app, p1, value from hooks where key in ({marks})", *SCRIPT_HOOKS
			):
				path = f"apps/{hook['app']}/{hook['app']}/{hook['value'].lstrip('/')}"
				self._hooked.setdefault(path, []).append(hook["p1"])
		return self._hooked

	def controllers(self, doctype: str) -> list[dict]:
		"""form_controller refs in the form's scripts: the class each one installs as frm.cscript."""
		scripts = self.scripts(doctype)
		if not scripts:
			return []
		marks = ",".join("?" * len(scripts))
		return self.store.rows(
			"select f.path, r.line, r.target from refs r join files f on f.id = r.file_id "
			f"where r.kind = 'form_controller' and f.path in ({marks}) order by f.path, r.line",
			*scripts,
		)

	def chain(self, qualname: str) -> list[dict]:
		"""The JS class and its `extends` bases, most derived first. Stops at a class not indexed."""
		out = []
		while qualname and len(out) < MAX_CHAIN:
			rows = self.store.rows(
				"select s.qualname, s.signature, s.line, f.path from symbols s join files f on f.id = s.file_id "
				"where s.qualname = ? and s.kind = 'class' and f.lang in ('js', 'ts') and f.app not like '%@%'",
				qualname,
			)
			if not rows:
				out.append({"qualname": qualname, "path": "", "line": 0, "missing": True})
				break
			out.append(rows[0])
			qualname = rows[0]["signature"].strip("()")
		return out

	def methods(self, chain: list[dict]) -> dict[str, dict]:
		"""Method name -> the most derived definition, as JS prototype lookup finds it."""
		found: dict[str, dict] = {}
		for cls in chain:
			for row in self.store.rows(
				"select s.name, s.line, f.path from symbols s join files f on f.id = s.file_id "
				"where s.parent = ? and s.kind = 'method' and f.path = ?",
				cls["qualname"],
				cls["path"],
			):
				found.setdefault(row["name"], {**row, "cls": cls["qualname"]})
		return found

	def fieldnames(self, doctype: str) -> set[str]:
		rows = self.store.rows(
			"select fieldname from fields where doctype = ? union select target from refs "
			"where kind = 'custom_field' and via = ?",
			doctype,
			doctype,
		)
		return {r["fieldname"] for r in rows if r["fieldname"]}

	def child_tables(self, doctype: str) -> dict[str, str]:
		"""Table fieldname -> child DocType."""
		rows = self.store.rows(
			"select fieldname, options from fields where doctype = ? and fieldtype in ('Table', 'Table MultiSelect')",
			doctype,
		)
		return {r["fieldname"]: r["options"] for r in rows}


def is_event_name(name: str, fields: set[str]) -> bool:
	return name in FORM_EVENTS or name in fields or name.endswith(ROW_EVENT_SUFFIXES)
