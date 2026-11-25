"""The resolved call graph: who calls what, and through which mechanism.

A `calls` row resolves through the file's imports and `self.`/`cls.` in the same class. Frappe
also calls code indirectly, and those calls are where a reader loses the thread. Each edge here
carries the mechanism as its kind:

- `call`: a resolved call (re-exports followed); calling a class runs its `__init__`.
- `inherited`: `self.m()` where `m` is defined on a base class, not the class itself.
- `instance`: `x.m()` or `Cls(...).m()` where `x = Cls(...)` in the same function.
- `doc_method`: `doc.m()` on a document whose DocType the code names
  (`doc = frappe.get_doc("Sales Order", n)`), resolved on that DocType's controller class.
- `lifecycle`: what the document layer runs for that call (`doc.save()` runs `validate`,
  `before_save`, `on_update`, ...; `frappe.delete_doc("X", n)` runs `on_trash`, ...) on the
  DocType's controller.
- `doc_event`: the same events, through hooks.py `doc_events` for that DocType and for "*".
- `hook`: a function that reads `frappe.get_hooks("<key>")` with a literal key, to every callback
  registered under that key.
- `proxy`: a call on a typed module attribute (`frappe.qb.get_query()`, where `qb:
  LocalProxy["MariaDB"]`), to the type's method or the monkey patch that installs it
  (`Base.get_query = get_query`).
- `override`: `self.m()` in a class whose subclasses define their own `m`, to each of them
  (virtual dispatch: `File.validate` calls `self.handle_is_private_changed()`, which `FileV1` and
  `FileV2` implement). Skipped past MAX_OVERRIDES, so `Document` does not fan out to every
  controller; the lifecycle edges cover those.
- `callback`: a function named without being called, which whoever receives it may call:
  passed to a call (`frappe.enqueue(fn)`, `Rule("/x", endpoint=fn)`) or stored in a dict
  (sandbox and Jinja globals).
- `dispatch`: a dotted path string handed to an API that calls it (`frappe.enqueue("a.b.c")`,
  `frappe.get_attr`, `frappe.call`).
- `dotted`: any other string literal naming a function of a bench app (`method="a.b.c"`).

Edges are static and conservative: a document of a DocType the code does not name gets no
`doc_method`, `lifecycle` or `doc_event` edges, so one `doc.save()` on an unknown document does not
connect to every `validate` on the bench.
"""

from collections import defaultdict, deque
from collections.abc import Iterator
from sys import intern

from frappe_ast.python.frappe_api import HOOK_READER_APIS, LIFECYCLE_APIS, LIFECYCLE_EVENTS
from frappe_ast.queries.resolver import Resolver, _bases
from frappe_ast.store import Store

# More overriding subclasses than this and `self.m()` gets no override edges: a base that wide is
# a framework class (Document), whose dispatch the lifecycle edges model instead.
MAX_OVERRIDES = 5

# Edges from refs: a function named by reference or by a dotted string.
REF_EDGE_KINDS = {"callback": "callback", "method": "dispatch", "dotted": "dotted"}

# Document methods that start a lifecycle, by the name the code calls.
LIFECYCLE_METHODS = {name: name for name in LIFECYCLE_EVENTS}

Edge = tuple[str, str, int, str, int]  # caller, callee, line, kind, file_id


class CallGraph:
	def __init__(self, store: Store):
		self.store = store
		self.resolver = Resolver(store)
		self._methods: dict[tuple[str, str], str | None] = {}
		self._classes: dict[str, dict | None] = {}
		self._controllers: dict[str, dict | None] = {}
		self._symbols: dict[str, str] = {}  # qualname -> kind, for callable symbols
		self._subclasses: dict[str, list[str]] | None = None  # class -> direct subclasses
		self._bases: dict[tuple[str, str], str] = {}
		self._paths: dict[int, str] | None = None  # file id -> path
		for r in store.rows(
			"select s.qualname, s.kind from symbols s join files f on f.id = s.file_id "
			"where f.lang = 'python' and s.kind in ('function', 'method', 'class')"
		):
			self._symbols.setdefault(r["qualname"], r["kind"])
		self._variables = {
			r["qualname"]: r["signature"].split("|")
			for r in store.rows(
				"select qualname, signature from symbols where kind = 'variable' and signature != ''"
			)
		}
		self._patches = defaultdict(list)  # patched dotted path -> replacements
		for r in store.rows("select target, via from refs where kind = 'monkey_patch'"):
			self._patches[r["target"]].append(r["via"])
		self._hooks = defaultdict(list)  # key -> [(p1, p2, value)]
		for h in store.rows("select key, p1, p2, value from hooks"):
			self._hooks[h["key"]].append((h["p1"] or "", h["p2"] or "", h["value"]))

	# ---- edges -----------------------------------------------------------------------------

	def edges(self, apps: list[str] | None = None, caller: str = "") -> list[dict]:
		"""Every edge out of `caller`, or out of each caller in `apps` (all apps when both are empty)."""
		paths = self._file_paths()
		return [
			{"caller": c, "callee": callee, "line": line, "kind": kind, "path": paths[file_id]}
			for c, callee, line, kind, file_id in self.iter_edges(apps, caller)
		]

	def iter_edges(self, apps: list[str] | None = None, caller: str = "") -> Iterator[Edge]:
		"""`edges` as (caller, callee, line, kind, file_id) tuples, read row by row: only this compact
		does the bench-wide graph fit in memory."""
		where, params = "f.lang = 'python'", [*(apps or ()), *([caller] if caller else ())]
		where += f" and f.app in ({','.join('?' * len(apps))})" if apps else ""
		calls, refs = (f"{where} and c.caller = ?", f"{where} and r.scope = ?") if caller else (where, where)
		for c in self.store.iter_rows(
			"select c.caller, c.callee, c.resolved, c.line, c.receiver, c.arg, c.file_id from calls c "
			f"join files f on f.id = c.file_id where {calls}",
			*params,
		):
			source = intern(c["caller"])
			for target, kind in self.targets(c):
				yield source, intern(target), c["line"], kind, c["file_id"]
		for r in self.store.iter_rows(
			"select r.kind, r.target, r.line, r.scope, r.file_id from refs r join files f on f.id = r.file_id "
			f"where {refs} and r.kind in ('callback', 'method', 'dotted') and r.scope != '<module>'",
			*params,
		):
			target = self._callable(r["target"])
			if target:
				yield intern(r["scope"]), intern(target), r["line"], REF_EDGE_KINDS[r["kind"]], r["file_id"]

	def targets(self, c: dict) -> list[tuple[str, str]]:
		"""(callee qualname, kind) for one calls row."""
		resolved, callee = c["resolved"] or "", c["callee"] or ""
		receiver, arg = c.get("receiver") or "", c.get("arg") or ""
		method = callee.rsplit(".", 1)[-1]
		found: list[tuple[str, str]] = []
		if resolved:
			target = self._callable(resolved)
			if target:
				found.append((target, "call"))
			elif self._class(resolved):
				init = self._method_of(self._class(resolved)["qualname"], "__init__")
				if init:
					found.append((init, "call"))  # Cls(...) runs Cls.__init__
			elif callee.startswith(("self.", "cls.")) and callee.count(".") == 1:
				owner = resolved.rsplit(".", 1)[0]
				inherited = self._method_of(owner, method)
				if inherited:
					found.append((inherited, "inherited"))
			if callee.startswith(("self.", "cls.")) and callee.count(".") == 1:
				found += [(m, "override") for m in self._overrides(resolved.rsplit(".", 1)[0], method)]
			else:
				head = resolved.rsplit(".", 1)[0]
				for cls in self._variables.get(head, ()):
					target = self._method_of(cls, method)
					if target:
						found.append((target, "proxy"))
			if resolved in HOOK_READER_APIS and arg:
				found += [(value, "hook") for value in self._hook_values(arg)]
			if resolved in LIFECYCLE_APIS and arg:
				found += self._lifecycle(arg, LIFECYCLE_APIS[resolved])
		if receiver.startswith("call:"):
			cls = receiver[5:]
			if self._symbols.get(cls) == "class":
				target = self._method_of(cls, method)
				if target:
					found.append((target, "instance"))
		elif receiver.startswith("doctype:") and receiver != "doctype:?":
			doctype = receiver[8:]
			controller = self._controller(doctype)
			if controller:
				target = self._method_of(controller["qualname"], method)
				if target:
					found.append((target, "doc_method"))
			if method in LIFECYCLE_METHODS:
				found += self._lifecycle(doctype, LIFECYCLE_METHODS[method])
		return found

	def _lifecycle(self, doctype: str, operation: str) -> list[tuple[str, str]]:
		found = []
		controller = self._controller(doctype)
		for event in LIFECYCLE_EVENTS[operation]:
			if controller:
				target = self._method_of(controller["qualname"], event)
				if target:
					found.append((target, "lifecycle"))
			for p1, p2, value in self._hooks.get("doc_events", ()):
				if p2 == event and p1 in (doctype, "*"):
					found.append((value, "doc_event"))
		return found

	def _overrides(self, cls_qualname: str, name: str) -> list[str]:
		"""`name` as defined by the subclasses of a class, at any depth."""
		if self._subclasses is None:
			self._subclasses = defaultdict(list)
			for r in self.store.rows(
				"select s.qualname, s.signature, f.module from symbols s join files f on f.id = s.file_id "
				"where f.lang = 'python' and s.kind = 'class' and s.signature != '()'"
			):
				for base in _bases(r["signature"]):
					key = (r["module"], base)
					if key not in self._bases:
						found = self.resolver._base_class(*key)
						self._bases[key] = found["qualname"] if found else ""
					if self._bases[key]:
						self._subclasses[self._bases[key]].append(r["qualname"])
		found, queue, seen = [], deque(self._subclasses.get(cls_qualname, ())), set()
		while queue:
			sub = queue.popleft()
			if sub in seen:
				continue
			seen.add(sub)
			if self._symbols.get(f"{sub}.{name}") == "method":
				found.append(f"{sub}.{name}")
				if len(found) > MAX_OVERRIDES:
					return []
			queue.extend(self._subclasses.get(sub, ()))
		return found

	def _hook_values(self, key: str) -> list[str]:
		"""The functions registered under a hook key; other values (paths, HTML, URLs) are data."""
		return sorted({f for f in (self._callable(v) for _, _, v in self._hooks.get(key, ())) if f})

	def _callable(self, dotted: str) -> str:
		if self._symbols.get(dotted) in ("function", "method"):
			return dotted
		symbol = self.resolver.symbol(dotted)  # re-exports
		if symbol and symbol["kind"] in ("function", "method"):
			return symbol["qualname"]
		return ""

	def _method_of(self, cls_qualname: str, name: str) -> str | None:
		"""`name` on the class or a base class, or the function a monkey patch installs there."""
		key = (cls_qualname, name)
		if key not in self._methods:
			cls = self._class(cls_qualname)
			method = self.resolver.find_method(cls, name)[0] if cls else None
			found = method["qualname"] if method and method["kind"] == "method" else None
			if found is None and cls:
				found = self._patched(cls, name, 0)
			self._methods[key] = found
		return self._methods[key]

	def _patched(self, cls: dict, name: str, depth: int) -> str | None:
		for replacement in self._patches.get(f"{cls['qualname']}.{name}", ()):
			target = self._callable(replacement)
			if target:
				return target
		if depth > 8:
			return None
		for base in _bases(cls["signature"]):
			base_cls = self.resolver._base_class(cls["module"], base)
			if base_cls:
				found = self._patched(base_cls, name, depth + 1)
				if found:
					return found
		return None

	def _class(self, qualname: str) -> dict | None:
		if qualname not in self._classes:
			symbol = self.resolver.symbol(qualname)
			self._classes[qualname] = symbol if symbol and symbol["kind"] == "class" else None
		return self._classes[qualname]

	def _controller(self, doctype: str) -> dict | None:
		if doctype not in self._controllers:
			dt = self.resolver.doctype(doctype)
			self._controllers[doctype] = self.resolver.controller_class(dt) if dt else None
		return self._controllers[doctype]

	# ---- paths -----------------------------------------------------------------------------

	def paths(self, source: str, target: str, limit: int = 5, max_depth: int = 12) -> list[list[dict]]:
		"""Shortest call paths from `source` to `target`, as lists of edges."""
		start = self._callable(source) or source
		goal = self._callable(target) or target
		out_edges: dict[str, list[dict]] = {}  # filled per node as the search reaches it
		found, queue = [], deque([(start, [])])
		seen_depth = {start: 0}
		while queue and len(found) < limit:
			node, path = queue.popleft()
			if len(path) >= max_depth:
				continue
			if node not in out_edges:
				out_edges[node] = sorted(self.edges(caller=node), key=lambda x: x["line"])
			for e in out_edges[node]:
				nxt = e["callee"]
				step = [*path, e]
				if nxt == goal:
					found.append(step)
					if len(found) >= limit:
						break
					continue
				if seen_depth.get(nxt, 99) > len(step):
					seen_depth[nxt] = len(step)
					queue.append((nxt, step))
		return found

	def file_path(self, file_id: int) -> str:
		return self._file_paths()[file_id]

	def _file_paths(self) -> dict[int, str]:
		if self._paths is None:
			self._paths = {r["id"]: r["path"] for r in self.store.rows("select id, path from files")}
		return self._paths
