"""frappe-ast: a static index of a Frappe bench that shows how its code connects.

It answers who calls a method, what runs when a document saves or a form field changes, where
request input goes, which hooks and fields are broken, and what a diff touches. It reads Python,
JS, Vue, hooks.py and DocType JSON, and never runs app code."""

import argparse
import sqlite3
import sys
from pathlib import Path

from frappe_ast.commands import COMMANDS, Context


def main(argv: list[str] | None = None) -> int:
	sys.setrecursionlimit(20000)  # deeply nested JS object literals
	parser = _build_parser()
	args = parser.parse_args(argv)
	if not args.command:
		parser.print_help()
		return 1
	ctx = Context(args)
	try:
		return COMMANDS[args.command].run(ctx, args) or 0
	except sqlite3.OperationalError as e:
		print(f"sql error: {e}", file=sys.stderr)
		return 2
	except BrokenPipeError:
		return 0
	finally:
		ctx.close()


def _build_parser() -> argparse.ArgumentParser:
	common = argparse.ArgumentParser(add_help=False)
	common.add_argument("--bench", type=Path, help="bench folder (default: nearest above cwd)")
	common.add_argument("--db", help="index file (default: ~/.cache/frappe-ast/<bench>-<hash>.sqlite)")
	common.add_argument("--json", action="store_true", help="JSON output")
	common.add_argument("--limit", type=int, default=50, help="max rows per section")
	common.add_argument("--no-refresh", action="store_true", help="skip the incremental index refresh")
	# Options live on each subcommand: `frappe-ast find x --json`.
	parser = argparse.ArgumentParser(prog="frappe-ast", description=__doc__)
	sub = parser.add_subparsers(dest="command", metavar="<command>")
	for name, command in COMMANDS.items():
		command.add_parser(sub, name, common)
	return parser


if __name__ == "__main__":
	raise SystemExit(main())
