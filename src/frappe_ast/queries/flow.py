"""Argument flow: which call arguments carry a function's parameters or request input.

Rows come from the `flows` table (python/flows.py), one per (call, argument, source), each inside
one function. `follow` joins them across calls: a flow into a bench function continues with that
function's flows from the parameter the argument binds to, through the same callee resolution as
`edges` (imports, `self.` methods, overrides, instances, documents of a named DocType). Lifecycle,
doc_events and hook edges pass no arguments through, so a flow does not cross them.
"""

import ast
import json
from collections import defaultdict

from frappe_ast.queries.callgraph import CallGraph
from frappe_ast.store import Store

# Edge kinds whose call binds the written arguments to the callee's parameters.
_ARG_EDGES = {"call", "inherited", "instance", "doc_method", "override", "proxy"}


class FlowQuery:
	def __init__(self, store: Store):
		self.store = store
		self._graph: CallGraph | None = None
		self._by_caller: dict[str, list[dict]] | None = None
		self._params: dict[str, tuple[list[str], str, str] | None] = {}

	def rows(
		self,
		function: str = "",
		to: str = "",
		source: str = "",
		direct: bool = False,
		apps: list[str] | None = None,
	) -> list[dict]:
		"""Flows out of `function`, into calls of `to` (resolved or as written), from sources that
		start with `source` (`param`, `form_dict`, `request`), with no step between when `direct`."""
		where, params = ["1"], []
		if function:
			where.append("w.caller = ?")
			params.append(function)
		if to:
			where.append("(w.resolved = ? or w.callee = ?)")
			params += [to, to]
		if source:
			where.append("w.source like ?")
			params.append(f"{source}%")
		if direct:
			where.append("w.via = ''")
		if apps:
			where.append(f"f.app in ({','.join('?' * len(apps))})")
			params += apps
		return [
			_row(r)
			for r in self.store.rows(
				"select w.*, f.path from flows w join files f on f.id = w.file_id "
				f"where {' and '.join(where)} order by f.path, w.line, w.arg",
				*params,
			)
		]

	def follow(
		self,
		function: str,
		to: str = "",
		source: str = "",
		direct: bool = False,
		depth: int = 4,
	) -> list[dict]:
		"""Flows out of `function` followed into the bench functions they reach, up to `depth`
		calls deep. Each row keeps the original source, the steps from every function on the way
		in `via`, and the calls it went `through`."""
		out, seen = [], set()
		stack = [(r, [], r["via"]) for r in self._flows_of(function)]
		while stack:
			row, through, via = stack.pop()
			start = through[0]["source"] if through else row["source"]
			if source and not start.startswith(source):
				continue
			callee = row["resolved"] or row["callee"]
			if (not to or to in (callee, row["callee"])) and not (direct and via):
				out.append(
					{**_row(row), "source": start, "via": via, "through": [h["step"] for h in through]}
				)
			if len(through) >= depth:
				continue
			for target, param in self._bound(row):
				if (target, param, start) in seen:
					continue
				seen.add((target, param, start))
				hop = {"source": start, "step": f"{row['path']}:{row['line']}  {target} ({param})"}
				for inner in self._flows_of(target):
					if inner["source"] == f"param:{param}":
						steps = _join(inner["via"], via)
						stack.append((inner, [*through, hop], steps))
		unique = {json.dumps(r, sort_keys=True): r for r in out}  # two equal calls on one line
		return sorted(unique.values(), key=lambda r: (len(r["through"]), r["path"], r["arg"]))

	# ---- helpers ---------------------------------------------------------------------------

	def _flows_of(self, function: str) -> list[dict]:
		if self._by_caller is None:
			self._by_caller = defaultdict(list)
			for r in self.store.rows(
				"select w.*, f.path, c.receiver from flows w join files f on f.id = w.file_id "
				"left join calls c on c.file_id = w.file_id and c.caller = w.caller "
				"and c.line = w.line and c.callee = w.callee"
			):
				self._by_caller[r["caller"]].append(dict(r))
		return self._by_caller.get(function, [])

	def _bound(self, row: dict) -> list[tuple[str, str]]:
		"""(bench function, parameter) that this argument binds to, for each callee the call reaches."""
		if self._graph is None:
			self._graph = CallGraph(self.store)
		call = {k: row.get(k) or "" for k in ("callee", "resolved", "receiver")}
		found = set()
		for target, kind in self._graph.targets({**call, "arg": ""}):
			if kind in _ARG_EDGES:
				param = self._param(target, row["arg"])
				if param:
					found.add((target, param))
		return sorted(found)

	def _param(self, function: str, arg: str) -> str:
		"""The parameter of `function` that an argument at position or keyword `arg` binds to."""
		if function not in self._params:
			self._params[function] = self._signature(function)
		sig = self._params[function]
		if not sig:
			return ""
		names, vararg, kwarg = sig
		if arg.isdigit():
			i = int(arg)
			return names[i] if i < len(names) else vararg
		if arg == "*":
			return vararg
		if arg == "**":
			return kwarg
		return arg if arg in names else kwarg

	def _signature(self, function: str) -> tuple[list[str], str, str] | None:
		symbol = self._graph.resolver.symbol(function)
		if not symbol or not symbol["signature"]:
			return None
		try:
			args = ast.parse(f"def f{symbol['signature'].split(' -> ')[0]}: pass").body[0].args
		except SyntaxError:
			return None
		names = [a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]]
		decorators = json.loads(symbol["decorators"]) if symbol["decorators"] else []
		if symbol["kind"] == "method" and "staticmethod" not in decorators and names:
			names = names[1:]  # self / cls is bound by the call
		return names, args.vararg.arg if args.vararg else "", args.kwarg.arg if args.kwarg else ""


def _join(outer: str, inner: str) -> str:
	"""Two via chains, outermost first, a step repeated across the seam kept once."""
	if outer and inner and outer.split(",")[-1] == inner.split(",")[0]:
		inner = inner.partition(",")[2]
	return ",".join(v for v in (outer, inner) if v)


def _row(r) -> dict:
	return {
		"path": f"{r['path']}:{r['line']}",
		"caller": r["caller"],
		"source": r["source"],
		"callee": r["resolved"] or r["callee"],
		"arg": r["arg"],
		"via": r["via"],
	}
