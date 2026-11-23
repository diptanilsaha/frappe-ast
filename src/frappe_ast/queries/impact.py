"""`frappe-ast impact`: map a git diff to changed symbols and what depends on them."""

import re
import subprocess
from pathlib import Path

from frappe_ast.queries.patches import Patches
from frappe_ast.queries.resolver import SYMBOL_COLS, Resolver
from frappe_ast.store import Store
from frappe_ast.workspace import App

HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


class Impact:
	def __init__(self, store: Store, bench_root: Path, limit: int = 15):
		self.store = store
		self.resolver = Resolver(store)
		self.root = bench_root
		self.limit = limit

	def run(self, apps: list[App], base: str) -> dict:
		changes = []
		for app in apps:
			for path, lines in _changed_lines(app.root, base).items():
				rel = str((app.root / path).relative_to(self.root))
				changes.append(self._file_impact(rel, lines))
		return {"base": base, "files": changes}

	def _file_impact(self, rel: str, lines: set[int]) -> dict:
		if rel.endswith(".json"):
			doctype = self.resolver.doctype_for_file(rel)
			return {"path": rel, "schema_of": doctype}
		symbols = self._touched_symbols(rel, lines)
		return {
			"path": rel,
			"doctype": self.resolver.doctype_for_file(rel),
			"symbols": [self._symbol_impact(s) for s in symbols],
			"module_level_change": "yes" if not symbols and lines else "",
		}

	def _touched_symbols(self, rel: str, lines: set[int]) -> list[dict]:
		rows = self.store.rows(
			f"select {SYMBOL_COLS} from symbols s join files f on f.id = s.file_id "
			"where f.path = ? and s.kind not in ('alias', 'component') order by s.line",
			rel,
		)
		touched = [r for r in rows if any(r["line"] <= n <= r["end_line"] for n in lines)]
		# Keep the innermost symbol per changed line; a class is noise when its method changed.
		names = {r["qualname"] for r in touched}
		return [
			r
			for r in touched
			if not any(o != r["qualname"] and o.startswith(r["qualname"] + ".") for o in names)
		]

	def _symbol_impact(self, sym: dict) -> dict:
		qual = sym["qualname"]
		impact = {"symbol": f"{sym['kind']} {qual}:{sym['line']}"}
		if sym["whitelisted"]:
			impact["exposure"] = "whitelisted" + (" guest" if sym["allow_guest"] else "")
		if sym["lang"] != "python":
			return impact
		names = [qual, *self.resolver.aliases_of(qual)]  # callers can name a re-export path
		marks = ",".join("?" * len(names))
		self._add(
			impact,
			"callers",
			"select f.path, c.line, c.caller as scope from calls c join files f on f.id = c.file_id "
			f"where c.resolved in ({marks})",
			*names,
		)
		self._add(
			impact, "hooks", f"select app, key, p1, p2, line from hooks where value in ({marks})", *names
		)
		self._add(impact, "string_refs",
			"select f.path, r.line, r.via, r.scope from refs r join files f on f.id = r.file_id "
			f"where r.kind in ('method', 'dotted') and r.target in ({marks})", *names)  # fmt: skip
		# A wrapper that replaces this function must keep its signature.
		patched = Patches(self.store, self.resolver).patched_by(names)
		if patched:
			impact["patched_by"] = patched[: self.limit]
		return impact

	def _add(self, impact: dict, key: str, sql: str, *params):
		rows = self.store.rows(sql, *params)
		impact[key] = rows[: self.limit]
		if len(rows) > self.limit:
			impact[f"{key}_total"] = len(rows)


def _changed_lines(repo: Path, base: str) -> dict[str, set[int]]:
	"""Changed or added line numbers (new side) per file, from `git diff -U0 <base>`."""
	result = subprocess.run(
		["git", "-C", str(repo), "diff", "-U0", "--no-color", "--no-ext-diff", base, "--"],
		capture_output=True,
		text=True,
	)
	if result.returncode != 0:
		raise SystemExit(f"git diff failed in {repo}: {result.stderr.strip()}")
	changes: dict[str, set[int]] = {}
	current = None
	for line in result.stdout.splitlines():
		if line.startswith("+++ "):
			current = None if line.endswith("/dev/null") else line[6:]
			if current:
				changes.setdefault(current, set())
		elif current and (match := HUNK_RE.match(line)):
			start, count = int(match.group(1)), int(match.group(2) or 1)
			# A pure deletion (count 0) still touches the symbol around `start`.
			changes[current].update(range(start, start + max(count, 1)))
	return {p: lines for p, lines in changes.items() if p.endswith((".py", ".js", ".ts", ".vue", ".json"))}
