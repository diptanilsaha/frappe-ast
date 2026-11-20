"""Type-level symbols for component APIs: TS interfaces / object types and `*Props` objects.

Mixed into the JS walker. The signature lists member names, so `component` can show a
component's props, emits and slots without opening types.ts.
"""

from frappe_ast.js import nodes as n

PROPS_SUFFIXES = ("Props", "props")


class TypeSymbols:
	def _on_interface_declaration(self, node, scope):
		members = n.type_members(node.child_by_field_name("body"))
		extends = next((c for c in node.named_children if c.type == "extends_type_clause"), None)
		suffix = " " + " ".join(n.text(extends).split()) if extends is not None else ""
		self._symbol(
			node,
			n.text(node.child_by_field_name("name")),
			"type",
			scope,
			signature=_members(members) + suffix,
		)
		return None

	def _on_type_alias_declaration(self, node, scope):
		value = node.child_by_field_name("value")
		if value is not None and value.type == "object_type":
			self._symbol(
				node,
				n.text(node.child_by_field_name("name")),
				"type",
				scope,
				signature=_members(n.type_members(value)),
			)
		return None

	def _props_object(self, node, name, value, scope):
		"""`export const buttonProps = {...}`: runtime props a component spreads in."""
		if value is not None and value.type == "object" and n.text(name).endswith(PROPS_SUFFIXES):
			keys = [key for key, _ in n.object_members(value)]
			self._symbol(node, n.text(name), "props", scope, signature=_members(keys))


def _members(names: list[str]) -> str:
	shown = ", ".join(names[:30])
	return "{" + shown + (", ..." if len(names) > 30 else "") + "}"
