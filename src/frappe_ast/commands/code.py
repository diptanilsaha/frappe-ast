"""Language-level lookups: outline, find, callers, callees, refs, raw SQL."""

from pathlib import Path

from frappe_ast.commands.base import Command
from frappe_ast.queries.lookup import Lookup
from frappe_ast.queries.outline import Outline
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
	help = "read-only SQL over the index (tables: files symbols calls refs findings hooks doctypes fields permissions doctype_links)"

	def arguments(self, p):
		p.add_argument("query")

	def run(self, ctx, args):
		store = ctx.store()
		store.db.execute("pragma query_only = 1")
		ctx.render.emit(store.rows(args.query))
