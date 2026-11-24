"""Helpers the tests share: readable views of analyzer facts and trace rows."""


class Facts:
	def __init__(self, facts):
		self.facts = facts
		self.symbols = facts.symbols
		self.calls = facts.calls
		self.findings = facts.findings

	def refs(self, kind: str) -> list:
		return [r for r in self.facts.refs if r.kind == kind]

	def targets(self, kind: str) -> set[str]:
		return {r.target for r in self.refs(kind)}

	def pairs(self, kind: str) -> set[tuple[str, str]]:
		"""(target, via) of each ref of `kind`."""
		return {(r.target, r.via) for r in self.refs(kind)}


def calls(rows: list[dict]) -> set[tuple[str, str]]:
	"""(file name, via) of trace rows."""
	return {(r["path"].rsplit("/", 1)[-1], r["via"]) for r in rows}


def tagged_lines(path) -> dict[str, int]:
	"""`{tag: line}` for the `# @tag` comments in a bench file, so tests name lines, not numbers."""
	found = {}
	for n, text in enumerate(path.read_text().splitlines(), 1):
		if "# @" in text:
			found[text.rsplit("# @", 1)[1].strip()] = n
	return found
