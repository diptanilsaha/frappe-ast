"""The sources an expression carries, and the steps between each source and the value.

`Sources` maps a source to its via chain, outermost step first. A step through a call's return
also names the call (`line:col:callee`: two calls in a chain like `a().b()` start at one column)
and the argument the value went in by (`flt@12:8:flt:0`), so a query that knows the callee can
check that its return value carries that argument. `split_via` turns a chain into the `flows`
row's `via` (names only) and `returns` (the calls).
"""

import ast

Sources = dict[str, str]  # source -> via ("" when direct)


def call_step(name: str, line: int, col: int, callee: str, arg: str) -> str:
	return f"{name}@{line}:{col}:{callee}:{arg}"


def split_via(via: str) -> tuple[str, str]:
	"""(`via` without positions, a repeated step kept once; `returns`: `line:col:callee:arg;...`)."""
	names, returns = [], []
	for step in filter(None, via.split(",")):
		name, _, position = step.partition("@")
		if position:
			returns.append(position)
		if not names or names[-1] != name:
			names.append(name)
	return ",".join(names), ";".join(returns)


def keyed(found: Sources, key: ast.expr) -> Sources:
	"""`form_dict["name"]`: a request source without a key gains it; anything else passes through."""
	if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
		return found
	return {
		(f"{s}:{key.value}" if not s.startswith(("param:", "self.")) and ":" not in s else s): via
		for s, via in found.items()
	}


def union(*parts: Sources) -> Sources:
	out: Sources = {}
	for part in parts:
		for source, via in part.items():
			if source not in out or via == "":
				out[source] = via
	return out


def through(found: Sources, via: str) -> Sources:
	"""The same sources, now behind one more step."""
	return {s: f"{via},{v}" if v else via for s, v in found.items()}
