"""Argument flow: which call arguments carry a function's parameters or request input.

Rows come from the `flows` table (python/flows.py), one per (call, argument, source), each inside
one function. A row whose value came back from a call is kept only when that call returns it
(queries/returns.py). `follow` joins rows across functions, up to `depth` hops:

- a flow into a bench function continues with that function's flows from the parameter the
  argument binds to, through the same callee resolution as `edges` (imports, `self.` methods,
  overrides, instances, documents of a named DocType);
- a store on the instance (`self.x = value`) continues with the flows from `self.x` in every
  method of the class and its subclasses, whichever order they run in.

Lifecycle, doc_events and hook edges pass no arguments through, so a flow does not cross them.
`<return>` rows (what a function returns) show for the function asked about, not for those a
flow is followed into.
"""

import json

from frappe_ast.queries.callgraph import CallGraph
from frappe_ast.queries.returns import ARG_EDGES, RETURN, ReturnSummaries
from frappe_ast.queries.signatures import Signatures
from frappe_ast.store import Store

_COLUMNS = "w.*, f.path, c.receiver"
_JOIN = (
	"flows w join files f on f.id = w.file_id left join calls c on c.file_id = w.file_id "
	"and c.caller = w.caller and c.line = w.line and c.col = w.col and c.callee = w.callee"
)


class FlowQuery:
	def __init__(self, store: Store):
		self.store = store
		self.graph = CallGraph(store)
		self.signatures = Signatures(self.graph.resolver)
		self.returns = ReturnSummaries(store, self.graph, self.signatures.bind)
		self._by_caller: dict[str, list[dict]] = {}
		self._methods: dict[str, list[str]] = {}

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
				f"select {_COLUMNS} from {_JOIN} where {' and '.join(where)} order by f.path, w.line, w.arg",
				*params,
			)
			if self._kept(r)
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
		hops. Each row keeps the original source, the steps from every function on the way in
		`via`, and the hops it made in `through`."""
		out, seen = [], set()
		stack = [(r, [], r["via"]) for r in self._flows_of(function)]
		while stack:
			row, through, via = stack.pop()
			start = through[0]["source"] if through else row["source"]
			if source and not start.startswith(source):
				continue
			callee = row["resolved"] or row["callee"]
			shown = not through or row["callee"] != RETURN
			if shown and (not to or to in (callee, row["callee"])) and not (direct and via):
				out.append(
					{**_row(row), "source": start, "via": via, "through": [h["step"] for h in through]}
				)
			if len(through) >= depth:
				continue
			for target, entry in self._hops(row):
				if (target, entry, start) in seen:
					continue
				seen.add((target, entry, start))
				hop = {"source": start, "step": f"{row['path']}:{row['line']}  {target} ({entry})"}
				for inner in self._flows_of(target):
					if inner["source"] == entry or inner["source"] == f"param:{entry}":
						stack.append((inner, [*through, hop], _join(inner["via"], via)))
		unique = {json.dumps(r, sort_keys=True): r for r in out}  # two equal calls on one line
		return sorted(unique.values(), key=lambda r: (len(r["through"]), r["path"], r["arg"]))

	# ---- helpers ---------------------------------------------------------------------------

	def _flows_of(self, function: str) -> list[dict]:
		if function not in self._by_caller:
			self._by_caller[function] = [
				r
				for r in self.store.rows(f"select {_COLUMNS} from {_JOIN} where w.caller = ?", function)
				if self._kept(r)
			]
		return self._by_caller[function]

	def _kept(self, row: dict) -> bool:
		return not row["returns"] or self.returns.keeps(row["file_id"], row["returns"])

	def _hops(self, row: dict) -> list[tuple[str, str]]:
		"""(function, where the value enters it) for each function this row passes the value to:
		a parameter for a call, `self.x` for a store on the instance."""
		if row["arg"] == "=" and row["callee"].startswith("self."):
			cls, attr = row["resolved"].rsplit(".", 1)
			return [(m, f"self.{attr}") for m in self._methods_of(cls)]
		call = {k: row.get(k) or "" for k in ("callee", "resolved", "receiver")}
		found = set()
		for target, kind in self.graph.targets({**call, "arg": ""}):
			if kind in ARG_EDGES:
				param = self.signatures.bind(target, row["arg"])
				if param:
					found.add((target, param))
		return sorted(found)

	def _methods_of(self, cls: str) -> list[str]:
		"""The methods of a class and of its subclasses."""
		if cls not in self._methods:
			classes = [cls, *self.graph.subclasses(cls)]
			marks = ",".join("?" * len(classes))
			self._methods[cls] = [
				r["qualname"]
				for r in self.store.rows(
					f"select qualname from symbols where kind = 'method' and parent in ({marks}) "
					"order by qualname",
					*classes,
				)
			]
		return self._methods[cls]


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
