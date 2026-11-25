"""Language-level lookups: outline, find, callers, callees, refs, raw SQL."""

from pathlib import Path

from frappe_ast.commands.base import Command
from frappe_ast.queries.callgraph import CallGraph
from frappe_ast.queries.flow import FlowQuery
from frappe_ast.queries.lookup import Lookup
from frappe_ast.queries.outline import Outline
from frappe_ast.queries.surface import Surface
from frappe_ast.workspace import Bench


class OutlineCommand(Command):
	help = "symbols and Frappe touchpoints of one file (py, js, ts, vue); no index needed"

	def arguments(self, p):
		p.add_argument("file", type=Path)
		p.add_argument("--calls", action="store_true", help="also list every call")

	def run(self, ctx, args):
		path = args.file.resolve()
		try:
			bench = Bench(args.bench) if args.bench else Bench.find(path.parent)
			known = {a.name for a in bench.apps()}
		except SystemExit:
			bench, known = None, set()
		ctx.render.emit(Outline(bench, known).run(path, args.calls))


class FindCommand(Command):
	help = "find symbol definitions: name, dotted suffix, or glob (e.g. 'get_*_details')"

	def arguments(self, p):
		p.add_argument("pattern")
		p.add_argument("--kind", help="function, method, class, component, ...")
		p.add_argument("--app")
		p.add_argument("--lang", choices=["python", "js", "ts", "tsx", "vue"])

	def run(self, ctx, args):
		ctx.render.emit(
			Lookup(ctx.store(), args.limit).find(
				args.pattern, args.kind or "", args.app or "", args.lang or ""
			)
		)


class CallersCommand(Command):
	help = "who calls a Python dotted path (resolved through imports)"

	def arguments(self, p):
		p.add_argument("target")

	def run(self, ctx, args):
		ctx.render.emit(Lookup(ctx.store(), args.limit).callers(args.target))


class CalleesCommand(Command):
	help = "what a symbol calls (qualname, e.g. erpnext.x.y.Class.method)"

	def arguments(self, p):
		p.add_argument("target")

	def run(self, ctx, args):
		ctx.render.emit(Lookup(ctx.store(), args.limit).callees(args.target))


class EdgesCommand(Command):
	help = "the resolved call graph out of a symbol or app: calls, inherited, overriding and instance methods, document lifecycle, doc_events, hook dispatch and functions passed by reference"

	def arguments(self, p):
		p.add_argument("target", nargs="?", help="a caller qualname; omit with --app to list every edge")
		p.add_argument("--app", action="append", help="callers in this app (repeatable)")
		p.add_argument(
			"--kind",
			help="call, inherited, instance, proxy, doc_method, lifecycle, doc_event, hook, override, callback, dispatch, dotted",
		)

	def run(self, ctx, args):
		graph = CallGraph(ctx.store())
		edges = graph.edges(args.app or None, args.target or "")
		if args.kind:
			edges = [e for e in edges if e["kind"] == args.kind]
		edges.sort(key=lambda e: (e["path"], e["line"]))
		ctx.render.emit(edges if args.json and not args.target else edges[: args.limit])


class PathsCommand(Command):
	help = "shortest call paths from one Python symbol to another, through every edge kind of `edges`"

	def arguments(self, p):
		p.add_argument("source", help="caller qualname, e.g. an endpoint")
		p.add_argument("target", help="callee qualname")
		p.add_argument("--depth", type=int, default=12)

	def run(self, ctx, args):
		paths = CallGraph(ctx.store()).paths(args.source, args.target, min(args.limit, 20), args.depth)
		if not paths:
			ctx.render.emit(f"no path from {args.source} to {args.target} within {args.depth} calls")
			return 1
		ctx.render.emit(
			[
				{
					"route": i + 1,
					"steps": [
						f"{e['path']}:{e['line']}  {e['caller']} -[{e['kind']}]-> {e['callee']}" for e in p
					],
				}
				for i, p in enumerate(paths)
			]
		)


class SurfaceCommand(Command):
	help = "entry points: functions a request, document operation, hook or job runs directly (--reached: every function they reach)"

	def arguments(self, p):
		p.add_argument(
			"--kind",
			help="api, guest_api, route, request, www, doc_lifecycle, doc_event, permission, scheduler, template, report, hook",
		)
		p.add_argument(
			"--reached", action="store_true", help="every reached function, with surface kinds and depth"
		)

	def run(self, ctx, args):
		surface = Surface(ctx.store())
		if args.reached:
			rows = [{"function": q, **v} for q, v in sorted(surface.reached().items())]
		else:
			rows = [{"function": q, "kinds": sorted(k)} for q, k in sorted(surface.roots().items())]
		if args.kind:
			rows = [r for r in rows if args.kind in r["kinds"]]
		ctx.render.emit(rows if args.json else rows[: args.limit])


class ReachCommand(Command):
	help = (
		"how something outside the process can make a function run: the shortest route from each surface root"
	)

	def arguments(self, p):
		p.add_argument("target", help="function qualname")
		p.add_argument("--depth", type=int, default=20)

	def run(self, ctx, args):
		routes = Surface(ctx.store()).reach(args.target, min(args.limit, 50), args.depth)
		if not routes:
			ctx.render.emit(f"no surface root reaches {args.target} within {args.depth} calls")
			return 1
		ctx.render.emit(
			[
				{
					"root": r["root"],
					"kinds": ", ".join(r["kinds"]),
					"steps": [
						f"{e['path']}:{e['line']}  {e['caller']} -[{e['kind']}]-> {e['callee']}"
						for e in r["steps"]
					],
				}
				for r in routes
			]
		)


class FlowCommand(Command):
	help = "argument flow: which call arguments carry a function's parameters or request input (form_dict, headers, body)"

	def arguments(self, p):
		p.add_argument(
			"target", nargs="?", help="a function qualname; omit with --to to search every function"
		)
		p.add_argument("--to", help="only calls of this function, e.g. frappe.get_doc")
		p.add_argument(
			"--source", help="only sources starting with this: param, param:name, form_dict, request"
		)
		p.add_argument("--direct", action="store_true", help="only values that reach the argument unchanged")
		p.add_argument("--app", action="append", help="functions in this app (repeatable)")
		p.add_argument(
			"--follow", action="store_true", help="continue into the bench functions an argument is passed to"
		)
		p.add_argument("--depth", type=int, default=4, help="calls to follow (with --follow)")

	def run(self, ctx, args):
		if not (args.target or args.to) or (args.follow and not args.target):
			ctx.render.emit("give a function, --to <callee>, or both (--follow needs a function)")
			return 2
		query, source = FlowQuery(ctx.store()), args.source or ""
		if args.follow:
			rows = query.follow(args.target, args.to or "", source, args.direct, args.depth)
		else:
			rows = query.rows(args.target or "", args.to or "", source, args.direct, args.app)
		ctx.render.emit(rows if args.json else rows[: args.limit])


class RefsCommand(Command):
	help = "references to a DocType, dotted path, component, form event (DT:event) or import"

	def arguments(self, p):
		p.add_argument("target")
		p.add_argument(
			"--kind", help="doctype, method, dotted, doc_method, form_event, component, import, ..."
		)

	def run(self, ctx, args):
		ctx.render.emit(Lookup(ctx.store(), args.limit).refs(args.target, args.kind or ""))


class SqlCommand(Command):
	help = "read-only SQL over the index (tables: files symbols calls refs flows findings hooks doctypes fields permissions doctype_links)"

	def arguments(self, p):
		p.add_argument("query")

	def run(self, ctx, args):
		store = ctx.store()
		store.db.execute("pragma query_only = 1")
		ctx.render.emit(store.rows(args.query))
