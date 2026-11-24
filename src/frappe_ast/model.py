"""Facts that analyzers extract from one source file.

Every analyzer (Python, JS, Vue) returns a FileFacts. The index stores these
rows as-is, so keep the shapes flat and language-neutral.
"""

from dataclasses import dataclass, field


@dataclass
class Symbol:
	name: str
	qualname: str  # dotted import path for Python, namespace path for JS
	kind: str  # function, method, class, component, form_event, variable
	line: int
	end_line: int
	signature: str = ""
	parent: str = ""
	doc: str = ""
	decorators: list[str] = field(default_factory=list)
	whitelisted: bool = False
	allow_guest: bool = False
	http_methods: str = ""


@dataclass
class Call:
	caller: str  # qualname of the enclosing symbol, or "<module>"
	callee: str  # callee text as written, e.g. "frappe.get_doc"
	resolved: str  # best-effort absolute dotted target, "" when unknown
	line: int
	# What the call is made on, when the code says: "doctype:<DocType>" for a document from
	# frappe.get_doc and friends ("doctype:?" when the DocType is not a literal), "call:<dotted>"
	# for the result of calling <dotted> (an instance, when <dotted> is a class). "" otherwise.
	receiver: str = ""
	arg: str = ""  # the first positional argument when it is a string literal


@dataclass
class Flow:
	"""A value that reaches a call's argument from a parameter or the request, in one function."""

	caller: str  # qualname of the function
	callee: str  # as written
	resolved: str
	line: int
	arg: str  # "0", "1", ... positional; a keyword name; "*" / "**" for unpacking
	source: str  # "param:<name>", "form_dict[:<key>]", "request.<attr>[:<key>]"
	via: str = ""  # the outermost call or operation between source and argument; "" when direct


@dataclass
class Ref:
	"""A reference to a Frappe entity (DocType, server method, hook...)."""

	kind: str  # doctype, method, dotted, form_event, listview, report, component
	target: str
	via: str  # the API that made the reference, e.g. "frappe.get_doc"
	line: int
	scope: str = ""  # enclosing symbol qualname
	access: str = ""  # read, write, delete, call, render


@dataclass
class Finding:
	rule: str
	severity: str  # error, warning, info
	message: str
	line: int
	scope: str = ""


@dataclass
class FileFacts:
	path: str
	lang: str
	module: str = ""  # dotted module for Python files
	symbols: list[Symbol] = field(default_factory=list)
	calls: list[Call] = field(default_factory=list)
	refs: list[Ref] = field(default_factory=list)
	flows: list[Flow] = field(default_factory=list)
	findings: list[Finding] = field(default_factory=list)
	error: str = ""
