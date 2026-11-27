"""Which parameter of a bench function a call's argument binds to, from the indexed signature."""

import ast
import json

from frappe_ast.queries.resolver import Resolver

Signature = tuple[list[str], str, str]  # named parameters, *args, **kwargs


class Signatures:
	def __init__(self, resolver: Resolver):
		self.resolver = resolver
		self._cache: dict[str, Signature | None] = {}

	def bind(self, function: str, arg: str) -> str:
		"""The parameter of `function` that an argument at position or keyword `arg` binds to."""
		if function not in self._cache:
			self._cache[function] = self._signature(function)
		sig = self._cache[function]
		if not sig:
			return ""
		names, vararg, kwarg = sig
		if arg.isdigit():
			i = int(arg)
			return names[i] if i < len(names) else vararg
		if arg == "*":
			return vararg
		if arg == "**":
			return kwarg
		return arg if arg in names else kwarg

	def _signature(self, function: str) -> Signature | None:
		symbol = self.resolver.symbol(function)
		if not symbol or not symbol["signature"]:
			return None
		try:
			args = ast.parse(f"def f{symbol['signature'].split(' -> ')[0]}: pass").body[0].args
		except SyntaxError:
			return None
		names = [a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]]
		decorators = json.loads(symbol["decorators"]) if symbol["decorators"] else []
		if symbol["kind"] == "method" and "staticmethod" not in decorators and names:
			names = names[1:]  # self / cls is bound by the call
		return names, args.vararg.arg if args.vararg else "", args.kwarg.arg if args.kwarg else ""
