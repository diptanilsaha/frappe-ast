"""The row shape every check rule returns."""

from pathlib import PurePath


def finding(rule, severity, message, path, line, scope="") -> dict:
	return {
		"rule": rule,
		"severity": severity,
		"message": message,
		"path": str(PurePath(path)),
		"line": line,
		"scope": scope,
	}
