"""Subcommands. Each one parses its own args and prints through Renderer."""

from frappe_ast.commands.base import Context
from frappe_ast.commands.code import (
	CalleesCommand,
	CallersCommand,
	FindCommand,
	OutlineCommand,
	RefsCommand,
	SqlCommand,
)
from frappe_ast.commands.index import IndexCommand, StatusCommand
from frappe_ast.commands.reports import (
	AppsCommand,
	ComponentCommand,
	DoctypeCommand,
	EndpointsCommand,
	FormCommand,
	HooksCommand,
	MethodCommand,
	RecordCommand,
)
from frappe_ast.commands.review import CheckCommand, ImpactCommand

__all__ = ["COMMANDS", "Context"]

COMMANDS = {
	"index": IndexCommand(),
	"status": StatusCommand(),
	"outline": OutlineCommand(),
	"find": FindCommand(),
	"method": MethodCommand(),
	"doctype": DoctypeCommand(),
	"form": FormCommand(),
	"record": RecordCommand(),
	"callers": CallersCommand(),
	"callees": CalleesCommand(),
	"refs": RefsCommand(),
	"endpoints": EndpointsCommand(),
	"hooks": HooksCommand(),
	"component": ComponentCommand(),
	"apps": AppsCommand(),
	"check": CheckCommand(),
	"impact": ImpactCommand(),
	"sql": SqlCommand(),
}
