"""Methods of indexed Python classes: through bases, monkey patches and `super()`."""

from collections import defaultdict

from frappe_ast.queries.resolver import Resolver, _bases
from frappe_ast.store import Store


class Classes:
	def __init__(self, store: Store, resolver: Resolver):
		self.resolver = resolver
		self.symbols: dict[str, str] = {}  # qualname -> kind, for callable symbols
		for r in store.rows(
			"select s.qualname, s.kind from symbols s join files f on f.id = s.file_id "
			"where f.lang = 'python' and s.kind in ('function', 'method', 'class')"
		):
			self.symbols.setdefault(r["qualname"], r["kind"])
		self._patches = defaultdict(list)  # patched dotted path -> replacements
		for r in store.rows("select target, via from refs where kind = 'monkey_patch'"):
			self._patches[r["target"]].append(r["via"])
		self._methods: dict[tuple[str, str], str | None] = {}
		self._classes: dict[str, dict | None] = {}

	def callable(self, dotted: str) -> str:
		if self.symbols.get(dotted) in ("function", "method"):
			return dotted
		symbol = self.resolver.symbol(dotted)  # re-exports
		if symbol and symbol["kind"] in ("function", "method"):
			return symbol["qualname"]
		return ""

	def method_of(self, cls_qualname: str, name: str) -> str | None:
		"""`name` on the class or a base class, or the function a monkey patch installs there."""
		key = (cls_qualname, name)
		if key not in self._methods:
			cls = self.class_(cls_qualname)
			method = self.resolver.find_method(cls, name)[0] if cls else None
			found = method["qualname"] if method and method["kind"] == "method" else None
			if found is None and cls:
				found = self._patched(cls, name, 0)
			self._methods[key] = found
		return self._methods[key]

	def super_method(self, cls_qualname: str, name: str) -> str | None:
		"""What `super().name` runs in a method of the class: `name` on its bases, depth first,
		which is the MRO when inheritance is single."""
		cls = self.class_(cls_qualname)
		for base in _bases(cls["signature"]) if cls else ():
			base_cls = self.resolver._base_class(cls["module"], base)
			found = self.method_of(base_cls["qualname"], name) if base_cls else None
			if found:
				return found
		return None

	def class_(self, qualname: str) -> dict | None:
		if qualname not in self._classes:
			symbol = self.resolver.symbol(qualname)
			self._classes[qualname] = symbol if symbol and symbol["kind"] == "class" else None
		return self._classes[qualname]

	def _patched(self, cls: dict, name: str, depth: int) -> str | None:
		for replacement in self._patches.get(f"{cls['qualname']}.{name}", ()):
			target = self.callable(replacement)
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
