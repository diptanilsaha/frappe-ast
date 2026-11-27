"""Compact text output for agents and humans. `--json` bypasses this."""

import json

HIDDEN_KEYS = {
	"decorators",
	"end_line",
	"lang",
	"module",
	"parent",
	"app",
	"http_methods",
	"allow_guest",
	"whitelisted",
}


class Renderer:
	def __init__(self, as_json: bool = False):
		self.as_json = as_json

	def emit(self, data) -> None:
		if self.as_json:
			print(json.dumps(data, indent=1, default=str))
		else:
			print("\n".join(self._lines(data, 0)))

	def _lines(self, data, depth: int) -> list[str]:
		pad = "  " * depth
		if isinstance(data, list):
			if not data:
				return [f"{pad}(none)"]
			lines = []
			for item in data:
				lines += self._block(item, depth) if _is_nested(item) else [pad + self._row(item)]
			return lines
		if not isinstance(data, dict):
			return [f"{pad}{data}"]
		lines = []
		for key, value in data.items():
			if value in (None, "", [], {}) or value is False:
				continue
			if isinstance(value, dict) and "qualname" in value and "kind" in value:
				lines.append(f"{pad}{key}: {symbol_line(value)}")
			elif isinstance(value, dict) and not _is_flat(value):
				lines.append(f"{pad}{key}:")
				lines += self._lines(value, depth + 1)
			elif isinstance(value, list):
				lines.append(f"{pad}{key} ({len(value)}):")
				lines += self._lines(value, depth + 1)
			elif isinstance(value, dict):
				lines.append(f"{pad}{key}: " + " ".join(f"{k}={v}" for k, v in value.items()))
			else:
				lines.append(f"{pad}{key}: {self._row(value)}")
		return lines

	def _block(self, item: dict, depth: int) -> list[str]:
		"""A list item with nested parts: scalars on a `-` line, nested keys below it."""
		scalars = {k: v for k, v in item.items() if not isinstance(v, dict | list)}
		nested = {k: v for k, v in item.items() if isinstance(v, dict | list)}
		return ["  " * depth + "- " + self._row(scalars, keyed=True), *self._lines(nested, depth + 1)]

	def _row(self, item, keyed: bool = False) -> str:
		if not isinstance(item, dict):
			return str(item)
		if "qualname" in item and "kind" in item:
			return symbol_line(item)
		lines = ",".join(map(str, item.get("lines") or [item.get("line")]))
		head = f"{item['path']}:{lines}" if "path" in item and "line" in item else item.get("path", "")
		if "scope" in item and "path" in item:
			item = {**item, "scope": short_scope(item["path"], item["scope"])}
		rest = [
			f"{k}={_fmt(v)}" if keyed and k != "symbol" else _fmt(v)
			for k, v in item.items()
			if k not in ("path", "line", "lines", "col") and v not in (None, "", [], {})
		]
		return "  ".join(p for p in [head, *rest] if p)


def symbol_line(s: dict) -> str:
	tags = []
	if s.get("whitelisted"):
		tags.append("whitelisted" + (" guest" if s.get("allow_guest") else ""))
		if s.get("http_methods"):
			tags.append(s["http_methods"])
	text = f"{s['kind']} {s['qualname']}{s.get('signature') or ''}"
	if tags:
		text += f" [{', '.join(tags)}]"
	if "path" in s:
		text += f"  {s['path']}:{s['line']}"
		if s.get("end_line") and s["end_line"] != s["line"]:
			text += f"-{s['end_line']}"
	if s.get("doc"):
		text += f"  # {s['doc']}"
	return text


def short_scope(path: str, scope: str) -> str:
	"""Drop the module prefix from a scope when the path already shows it."""
	if not path.endswith(".py") or not path.startswith("apps/"):
		return scope
	module = ".".join(path.removesuffix(".py").split("/")[2:]).removesuffix(".__init__")
	return scope.removeprefix(module + ".") if scope != module else "<module>"


def _is_nested(item) -> bool:
	"""A row with nested parts; `lines`, every line of a folded row, prints in its head."""
	return isinstance(item, dict) and any(
		isinstance(v, dict | list) and v for k, v in item.items() if k != "lines"
	)


def _is_flat(value: dict) -> bool:
	return all(not isinstance(v, dict | list) for v in value.values()) and len(value) <= 6


def _fmt(value) -> str:
	if isinstance(value, dict):
		return " ".join(f"{k}={v}" for k, v in value.items() if k not in HIDDEN_KEYS and v not in (None, ""))
	return str(value)
