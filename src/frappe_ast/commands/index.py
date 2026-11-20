"""Index lifecycle: build/refresh and status."""

from frappe_ast.commands.base import Command


class IndexCommand(Command):
	help = "build or refresh the index (incremental; --full rebuilds)"

	def arguments(self, p):
		p.add_argument("--apps", help="comma-separated apps to refresh")
		p.add_argument("--full", action="store_true")

	def run(self, ctx, args):
		store = ctx.store(refresh=False)
		if args.full:
			store.clear_files()
		stats = ctx.refresh(args.apps.split(",") if args.apps else None)
		ctx.render.emit({"index": str(store.path), **stats})


class StatusCommand(Command):
	help = "index location, freshness and counts per app"

	def run(self, ctx, args):
		store = ctx.store(refresh=False)
		ctx.render.emit(
			{
				"bench": str(ctx.bench.root),
				"index": str(store.path),
				"indexed_at": store.get_meta("indexed_at"),
				# Quote this in a feedback report: it names the analyzer build that produced the index.
				"analyzer": (store.get_meta("analyzer") or "")[:12],
				"apps": store.rows(
					"select app, count(*) files, sum(error != '') with_errors from files group by app order by app"
				),
				"doctypes": store.rows(
					"select app, count(*) doctypes from doctypes group by app order by app"
				),
			}
		)
