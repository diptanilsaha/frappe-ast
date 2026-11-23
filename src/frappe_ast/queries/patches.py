"""Functions that run in place of another: monkey patches and erpnext `regional_overrides`.

A `monkey_patch` ref (python/patches.py) replaces target with via while the patch is in
place. `regional_overrides = {"India": {"erpnext.x.f": "app.y.g"}}` makes an
`@erpnext.allow_regional` function f run g when the company's country is India.

A ref counts only when the replacement is a function, a method or a lambda: an assignment
like `frappe.uploaded_filename = frappe.form_dict.filename` stores state.

FX209 warning: an app patches a function or method of another app that does not exist, so
the patch adds a new attribute that nothing calls (the upstream function was renamed or
moved). Inside one app, adding a method this way is deliberate (`Base.max = _max`).
"""

from frappe_ast.queries.finding import finding
from frappe_ast.queries.resolver import Resolver
from frappe_ast.store import Store

TESTS = "f.path not like '%/test\\_%' escape '\\' and f.path not like '%/tests/%'"
PATCH_ROWS = (
	"select f.path, r.line, r.scope, r.target, r.via as replacement from refs r join files f on f.id = r.file_id "
	f"where r.kind = 'monkey_patch' and f.app not like '%@%' and {TESTS}"
)
FUNCTION_KINDS = ("function", "method")
# Process globals that hold an instance: `frappe.db.sql = record_sql` patches Database.sql.
INSTANCES = {
	"frappe.database.database.Database": ("frappe.db", "frappe.db.__class__"),
	"frappe.utils.redis_wrapper.RedisWrapper": ("frappe.cache", "frappe.cache.__class__"),
}


def instance_names(names: list[str]) -> list[str]:
	"""`names` plus the paths that reach each method through a process global."""
	out = list(names)
	for name in names:
		cls, _, attr = name.rpartition(".")
		out += [f"{instance}.{attr}" for instance in INSTANCES.get(cls, ())]
	return out


class Patches:
	def __init__(self, store: Store, resolver: Resolver):
		self.store = store
		self.resolver = resolver

	def patched_by(self, names: list[str]) -> list[dict]:
		"""Code that replaces one of `names`: monkey patches, then regional overrides."""
		targets = instance_names(names)
		marks = ",".join("?" * len(targets))
		rows = self._rows(f"and r.target in ({marks}) order by f.path, r.line", *targets)
		marks = ",".join("?" * len(names))
		return [{k: v for k, v in r.items() if k != "target"} for r in rows] + [
			{"path": _hooks(h["app"]), "line": h["line"], "region": h["p1"], "replacement": h["value"]}
			for h in self._regional("p2", names)
		]

	def patches(self, names: list[str]) -> list[dict]:
		"""What one of `names` replaces, when it is the replacement."""
		marks = ",".join("?" * len(names))
		rows = self._rows(f"and r.via in ({marks}) order by f.path, r.line", *names)
		return [{k: v for k, v in r.items() if k != "replacement"} for r in rows] + [
			{"path": _hooks(h["app"]), "line": h["line"], "region": h["p1"], "target": h["p2"]}
			for h in self._regional("value", names)
		]

	def missing(self, prefix: str) -> list[dict]:
		"""FX209: patches whose target module or class is indexed but has no such attribute."""
		out = []
		for row in self._rows("and f.path like ?", prefix + "%"):
			if row["path"].split("/")[1] == row["target"].split(".")[0]:
				continue  # an app extending its own code
			owner, _, attr = row["target"].rpartition(".")
			cls = self.resolver.symbol(owner)
			if cls is not None and cls["kind"] == "class":
				method, complete = self.resolver.find_method(cls, attr)
				gone = method is None and complete
			else:
				gone = (
					cls is None
					and self.resolver.module_exists(owner)
					and not self.resolver.resolves(row["target"])
				)
			if gone:
				msg = f"patches {row['target']}, which does not exist (renamed upstream? the patch adds a new attribute that nothing calls)"
				out.append(finding("FX209", "warning", msg, row["path"], row["line"], row["scope"]))
		return out

	def _rows(self, where: str, *params) -> list[dict]:
		rows = self.store.rows(f"{PATCH_ROWS} {where}", *params)
		return [r for r in rows if r["replacement"] == "<lambda>" or self._is_function(r["replacement"])]

	def _is_function(self, dotted: str) -> bool:
		symbol = self.resolver.symbol(dotted)
		return symbol is not None and symbol["kind"] in FUNCTION_KINDS

	def _regional(self, column: str, names: list[str]) -> list[dict]:
		marks = ",".join("?" * len(names))
		return self.store.rows(
			f"select app, p1, p2, value, line from hooks where key = 'regional_overrides' and {column} in ({marks})",
			*names,
		)


def _hooks(app: str) -> str:
	return f"apps/{app}/{app}/hooks.py"
