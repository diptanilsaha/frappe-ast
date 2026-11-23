"""Parse app code written for a newer Python than the one that runs frappe-ast.

`ast` parses only the grammar of the running interpreter, and frappe v16 uses syntax from
3.12 and 3.14. When a file does not parse, it is rewritten to an older equivalent with the
same lines, then parsed again:
- `type X[T] = v` (3.12) -> `X = v`
- `def f[T](...)`, `class C[T](...)` (3.12) -> without the type parameters
- `except A, B:` (3.14) -> `except (A, B):`
- an f-string that only 3.12 parses (nested quotes of its own kind, a backslash in `{}`) -> an
  empty string with the same newlines. Its text is lost; f-strings that parse alone are kept.
"""

import ast
import re

# `type X` of a type alias, and the name of a def or class, when type parameters `[...]` may follow.
TYPED_NAME_RE = re.compile(
	r"^([ \t]*)(?:type[ \t]+(?P<alias>[A-Za-z_]\w*)[ \t]*(?=[\[=])"
	r"|(?P<head>(?:async[ \t]+)?(?:def|class)[ \t]+[A-Za-z_]\w*)(?=\[))",
	re.M,
)
EXCEPT_RE = re.compile(r"^([ \t]*except\*?[ \t]+)(?!\()([^:\n#]+,[^:\n#]+?)([ \t]*:)", re.M)
STRING_START_RE = re.compile(r"([rRbBfFuU]{0,2})('''|\"\"\"|'|\")")


def parse(source: str, filename: str = "<unknown>") -> ast.Module:
	try:
		return ast.parse(source, filename=filename)
	except SyntaxError as error:
		try:
			return ast.parse(downgrade(source), filename=filename)
		except SyntaxError:
			raise error from None


def downgrade(source: str) -> str:
	"""`source` in the grammar of Python 3.11, line for line."""
	source = _drop_type_params(source)
	source = EXCEPT_RE.sub(lambda m: f"{m[1]}({m[2].strip()}){m[3]}", source)
	return _old_fstrings(source)


def _drop_type_params(source: str) -> str:
	"""`type X[T] = v` -> `X = v`, `def f[T](` -> `def f(`, keeping the newlines inside `[...]`."""
	out, at = [], 0
	for match in TYPED_NAME_RE.finditer(source):
		name_end = match.end()
		end = _bracket_end(source, name_end) if source.startswith("[", name_end) else name_end
		head = match["alias"] if match["alias"] else match["head"]
		out += [source[at : match.start()], match[1], head, "\n" * source.count("\n", name_end, end)]
		at = end
	return "".join(out) + source[at:]


def _bracket_end(source: str, start: int) -> int:
	depth = 0
	for i in range(start, len(source)):
		depth += {"[": 1, "]": -1}.get(source[i], 0)
		if depth == 0:
			return i + 1
	return len(source)


def _old_fstrings(source: str) -> str:
	out, i = [], 0
	while i < len(source):
		if source[i] == "#":
			end = source.find("\n", i)
			end = len(source) if end < 0 else end
		else:
			match = STRING_START_RE.match(source, i)
			if not match or (i and (source[i - 1].isalnum() or source[i - 1] == "_")):
				out.append(source[i])
				i += 1
				continue
			end = _string_end(source, match.end(), match[2], "f" in match[1].lower())
			text = source[i:end]
			if "f" in match[1].lower() and not _parses(text):
				lines = text.count("\n")
				text = '""' if not lines else '"""' + "\n" * lines + '"""'
			out.append(text)
			i = end
			continue
		out.append(source[i:end])
		i = end
	return "".join(out)


def _string_end(source: str, i: int, quote: str, is_f: bool) -> int:
	"""Index after the closing quote of a string that starts at `i`. In an f-string, `{...}` may
	hold any string, also one with the same quote (3.12)."""
	while i < len(source):
		if source[i] == "\\":
			i += 2
		elif source.startswith(quote, i):
			return i + len(quote)
		elif is_f and source.startswith("{{", i):
			i += 2
		elif is_f and source[i] == "{":
			i = _expression_end(source, i + 1)
		else:
			i += 1
	return len(source)


def _expression_end(source: str, i: int) -> int:
	depth = 1
	while i < len(source) and depth:
		match = STRING_START_RE.match(source, i)
		if match and not (source[i - 1].isalnum() or source[i - 1] == "_"):
			i = _string_end(source, match.end(), match[2], "f" in match[1].lower())
			continue
		depth += {"{": 1, "}": -1}.get(source[i], 0)
		i += 1
	return i


def _parses(text: str) -> bool:
	try:
		ast.parse(text, mode="eval")
		return True
	except SyntaxError:
		return False
