"""Review helpers: checks and diff impact."""

from frappe_ast.commands.base import Command, bench_prefix
from frappe_ast.python import checks as python_checks
from frappe_ast.queries import check as cross_checks
from frappe_ast.queries.check import Checker
from frappe_ast.queries.impact import Impact


class CheckCommand(Command):
	help = "Frappe-aware checks: per-file rules (FA1xx) and cross-file rules (FX2xx)"

	def arguments(self, p):
		p.add_argument("path", nargs="?", default="", help="file or folder to limit findings to")
		p.add_argument("--rule", help="comma-separated rule ids")
		p.add_argument(
			"--severity",
			choices=["error", "warning", "info"],
			help="minimum severity (default: warning; all when --rule is given)",
		)
		p.add_argument("--no-cross", action="store_true", help="skip cross-file rules")
		p.add_argument("--list-rules", action="store_true", help="explain every rule id and exit")

	def run(self, ctx, args):
		if args.list_rules:
			print(python_checks.__doc__.strip() + "\n\n" + cross_checks.__doc__.strip())
			return 0
		store = ctx.store()
		prefix = bench_prefix(ctx.bench, args.path)
		rules = set(args.rule.split(",")) if args.rule else None
		findings = Checker(store, ctx.app_names).run(prefix, rules, not args.no_cross)
		severity = args.severity or ("" if rules else "warning")
		if severity:
			order = {"info": 0, "warning": 1, "error": 2}
			findings = [f for f in findings if order[f["severity"]] >= order[severity]]
		ctx.render.emit({"count": len(findings), "findings": findings[: args.limit * 10]})
		return 1 if any(f["severity"] == "error" for f in findings) else 0


class ImpactCommand(Command):
	help = "changed symbols since a git ref and what depends on them"

	def arguments(self, p):
		p.add_argument(
			"--base", default="HEAD", help="git ref to diff against (default: HEAD = uncommitted work)"
		)
		p.add_argument("--apps", help="comma-separated apps (default: apps with changes)")

	def run(self, ctx, args):
		store = ctx.store()
		apps = ctx.bench.apps(args.apps.split(",") if args.apps else None)
		ctx.render.emit(Impact(store, ctx.bench.root, args.limit).run(apps, args.base))
