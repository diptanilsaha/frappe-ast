"""1-based character columns for facts, from the byte offsets that ast and tree-sitter report.

A column tells two facts on one line apart, so each fact has its own position: `flows` joins
`calls` by it, and two uses of one attribute on a line stay two rows.
"""

import re

_LINE_BREAK = re.compile("\r\n|\r|\n")  # what ast counts as a line break; str.splitlines counts more


class Columns:
	def __init__(self, source: str):
		self.source = source
		self.ascii = source.isascii()
		self._lines: list[str] | None = None

	def col(self, line: int, byte_offset: int) -> int:
		"""The column of a byte offset on a 1-based line of the source."""
		if self.ascii:
			return byte_offset + 1
		if self._lines is None:
			self._lines = _LINE_BREAK.split(self.source)
		text = self._lines[line - 1] if 0 < line <= len(self._lines) else ""
		if text.isascii():
			return byte_offset + 1
		return len(text.encode()[:byte_offset].decode("utf-8", "replace")) + 1
