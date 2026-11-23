"""Frappe-level reports: methods, DocTypes, endpoints, hooks, components, apps."""

from frappe_ast.commands.base import Command
from frappe_ast.queries.apps import AppGraph
from frappe_ast.queries.component import ComponentReport
from frappe_ast.queries.doctype import DoctypeReport
from frappe_ast.queries.form import FormReport
from frappe_ast.queries.lookup import Lookup
from frappe_ast.queries.method import MethodTrace
from frappe_ast.queries.record import RecordReport


class MethodCommand(Command):
	help = "trace a dotted server method: definition, whitelist, overrides, hooks, callers, client calls"

	def arguments(self, p):
		p.add_argument("dotted")

	def run(self, ctx, args):
		ctx.render.emit(MethodTrace(ctx.store(), args.limit).run(args.dotted))


class DoctypeCommand(Command):
	help = "a DocType's schema, controller, doc_events, form JS, links and code references"

	def arguments(self, p):
		p.add_argument("name")
		p.add_argument(
			"--fields",
			action="store_true",
			help="list every field with flags, depends_on, permlevel and defaults",
		)

	def run(self, ctx, args):
		ctx.render.emit(DoctypeReport(ctx.store(), args.limit).run(args.name, args.fields))


class EndpointsCommand(Command):
	help = "whitelisted methods (HTTP API surface)"

	def arguments(self, p):
		p.add_argument("--app")
		p.add_argument("--guest", action="store_true", help="only allow_guest=True")
		p.add_argument("--path", help="path prefix, e.g. apps/erpnext/erpnext/stock")

	def run(self, ctx, args):
		ctx.render.emit(
			Lookup(ctx.store(), args.limit).endpoints(args.app or "", args.guest, args.path or "")
		)


class HooksCommand(Command):
	help = "hooks.py entries, statically evaluated across apps"

	def arguments(self, p):
		p.add_argument("--app")
		p.add_argument("--key", help="e.g. doc_events, scheduler_events, override_whitelisted_methods")
		p.add_argument("--match", help="substring of DocType, event or value")

	def run(self, ctx, args):
		ctx.render.emit(
			Lookup(ctx.store(), args.limit).hooks(args.app or "", args.key or "", args.match or "")
		)


class ComponentCommand(Command):
	help = "a Vue component: definitions (apps and frappe-ui versions), import sources, usages"

	def arguments(self, p):
		p.add_argument("name", help="PascalCase or kebab-case, e.g. Button or text-editor")
		p.add_argument("--app", help="only this app's usages and the frappe-ui copy it builds against")

	def run(self, ctx, args):
		ctx.render.emit(ComponentReport(ctx.store(), args.limit).run(args.name, args.app or ""))


class AppsCommand(Command):
	help = "apps with version, required_apps (missing ones too), dependents, frontends and frappe-ui copies"

	def run(self, ctx, args):
		ctx.render.emit(AppGraph(ctx.store(), ctx.bench).run())


class FormCommand(Command):
	help = (
		"a desk form: its scripts from every app, the JS controller class chain, and the handlers per event"
	)

	def arguments(self, p):
		p.add_argument("doctype")
		p.add_argument(
			"event", nargs="?", default="", help="an event or fieldname: list its handlers in run order"
		)

	def run(self, ctx, args):
		ctx.render.emit(FormReport(ctx.store(), args.limit).run(args.doctype, args.event))


class RecordCommand(Command):
	help = "a standard Report, Page, Web Form, Print Format, ... with the code beside its JSON"

	def arguments(self, p):
		p.add_argument("name")
		p.add_argument("--kind", default="", help="report, page, web_form, print_format, workspace, ...")

	def run(self, ctx, args):
		ctx.render.emit(RecordReport(ctx.store(), args.limit).run(args.name, args.kind))
