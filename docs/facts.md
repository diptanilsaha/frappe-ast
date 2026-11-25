# The index's facts

The formats of the rows analyzers write, for reading the index with `frappe-ast sql` and for
changing an analyzer. [How it works](how-it-works.md#3-what-the-index-holds) lists the tables;
this page says what the values in them mean.

## `calls`

| Column | Value |
|---|---|
| `caller` | qualname of the enclosing function, or `<module>` |
| `callee` | the callee as written: `frappe.get_doc`, `doc.save`, `Engine().run` for a method on a call's result; a `with` block on a typed receiver adds `Lock().__enter__` and `Lock().__exit__` (`__aenter__`, `__aexit__` for `async with`) |
| `resolved` | the absolute dotted target through imports and `self.` / `cls.`, `""` when unknown |
| `receiver` | what the call is made on: `doctype:<DocType>` for a document from `frappe.get_doc` and friends (`doctype:?` when the DocType is not a literal), `call:<dotted>` for the result of calling `<dotted>` (an instance when it is a class), `super:<class>` for `super()` in a method of `<class>` or `super(<class>, self)`, `""` otherwise |
| `arg` | the first positional argument when it is a short string literal |

## `refs`

One row per reference a Frappe API makes. `target` and `via` depend on `kind`:

| Kind | `target` | `via` and notes |
|---|---|---|
| `doctype` | a DocType name | the API that names it |
| `import` | the module path an import names (Python: absolute; JS: as written) | `import` |
| `import_name` | a name an import binds (Python: the absolute dotted name; JS: the exported name) | the module it comes from |
| `form_event` | `<DocType>:<event>` for a `frappe.ui.form.on` handler | `frappe.ui.form.on` |
| `form_trigger` | an event name the code fires with `frm.trigger("x")` | `frm.trigger` |
| `component` | a Vue component rendered in a template, PascalCase | `template` |
| `report`, `page` | the name a `.js` registers in `frappe.query_reports` / `frappe.pages` | the registry |
| `listview`, `treeview`, `calendar`, `chart_source` | the DocType or chart a `.js` registers view settings for | `frappe.listview_settings`, `frappe.treeview_settings`, `frappe.views.calendar`, `frappe.dashboards.chart_sources` |
| `method`, `dotted` | a dotted server method | the API, `url` for a `/api/method/` URL, `string` for any other literal |
| `doc_method`, `module_method` | `x` when the DocType is the form's (`frm.call`; `queries/contexts.py` finds the form), `<DocType>:x` when the call names it, `?:x` for a document of a DocType the code does not name | |
| `handle_method` | `<variable>:<method>` for `<variable>.runDocMethod/runMethod.submit` on a frappe-ui handle from another file | the runner; queries match it to `doctype_handle` refs in the same app |
| `doctype_handle` | the DocType a frappe-ui handle (`useDoctype`, `createListResource`) binds | the variable |
| `method_const` | a constant's name | matched to `const` symbols in the same app |
| `field` | `<DocType>.<field>` | |
| `form_field` | `field` or `table.field` (`*.field` for `add_fetch`), no DocType | queries resolve the form from the file (`queries/forms.py`) |
| `script_field` | a name the file defines itself | |
| `custom_field` | the field | the DocType |
| `setup_event` | `<tag>:<event>` for `frappe.ui.form.on` with a DocType that is not a literal: `@self`, `@self Item`, or `$` for the setup function's first parameter | |
| `setup_call` | a DocType literal passed to an app function | the function |
| `js_new` | a class an app creates with `new` | |
| `form_controller` | the JS class a form installs as `frm.cscript` | |
| `cscript_event` | a handler assigned to `frm.cscript` | |
| `realtime` | the event name | `access` is `publish` (Python) or `listen` (JS) |
| `record_link` | `<kind>:<name>` | |
| `monkey_patch` | the patched dotted path | the replacement: a dotted name, `<lambda>`, or `<function>.<name>` for a wrapper the patching function defines; queries count it only when the replacement is a function |
| `attribute` | an attribute name read or assigned (`access` is `read` or `write`), not called; once per function, receiver and access | the receiver: `self:<class>` for `self.x` in a method of `<class>`, or a `calls.receiver` value (`call:...`, `doctype:<DocType>`); queries count it only when the attribute is a `@property` |
| `callback` | a bench-app function named without being called | the call it is passed to (`frappe.enqueue`, `werkzeug.routing.Rule`), or `dict` when a dict holds it |

`scope` is the enclosing symbol, and `access` says what the reference does (`read`, `write`,
`call`, `define`, `import`, `pass`, ...).

## `flows`

One row per (call, argument, source) inside one function.

| Column | Value |
|---|---|
| `arg` | the argument: `0`, `1`, ... by position, a keyword name, or `*` / `**` for unpacking |
| `source` | `param:<name>`, `form_dict[:<key>]` or `request.<attr>[:<key>]`; the request tables are in `python/frappe_api.py` |
| `via` | the steps between source and argument, outermost first: a resolved call, `.<method>`, `f-string`, `format`, `container`; `""` when the value arrives unchanged |

A row records a fact, never a judgement: whether a step makes a value acceptable for a call is
for the reader to decide.

## `hooks`

`key` is the top-level `hooks.py` name; `p1`, `p2`, `p3` are the nested keys
(`doc_events` → DocType → event), and `value` is the leaf. A value the evaluator cannot fold is
kept as `<expr ...>`.
