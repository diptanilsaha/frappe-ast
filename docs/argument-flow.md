# Argument flow

`flow` answers "where does this value go?" For each call in a function, it shows which
arguments carry one of the function's parameters or a value from the request, and what the
value went through on the way. `--follow` keeps going into the functions the value is passed
to.

| Question | Command |
|---|---|
| Where do this function's parameters and request values go? | `flow <function>` |
| ... and on into the functions they are passed to? | `flow <function> --follow` |
| Which functions pass a value straight into this call? | `flow --to <callee> --direct` |

## Reading the output

The samples use the `argflow.py` fixture in this repository (`tests/bench/apps/demo`):

```python
@frappe.whitelist()
def load(doctype, name, **kwargs):
	doc = frappe.get_doc(doctype, name)                                  # line 7
	doc.update(kwargs)
	limit = cint(frappe.form_dict.get("limit"))
	frappe.db.sql(f"select name from `tab{doctype}` limit {limit}")      # line 10
	frappe.get_list("Demo Task", filters={"name": name})
	if name:
		target = name
	else:
		target = "T-1"
	frappe.db.get_value("Demo Task", target)                             # line 16
	for row in kwargs.get("rows"):
		frappe.get_doc("Demo Note", row)
	if isinstance(name, str):
		frappe.db.exists("Demo Task", name.strip())                      # line 20
	name = "fixed"
	frappe.db.get_value("Demo Note", name)
```

```
$ frappe-ast flow demo.argflow.load
apps/demo/demo/argflow.py:7  demo.argflow.load  param:doctype  frappe.get_doc  0
apps/demo/demo/argflow.py:7  demo.argflow.load  param:name  frappe.get_doc  1
apps/demo/demo/argflow.py:8  demo.argflow.load  param:kwargs  doc.update  0
apps/demo/demo/argflow.py:9  demo.argflow.load  form_dict:limit  frappe.utils.cint  0
apps/demo/demo/argflow.py:10  demo.argflow.load  param:doctype  frappe.db.sql  0  f-string
apps/demo/demo/argflow.py:10  demo.argflow.load  form_dict:limit  frappe.db.sql  0  f-string,frappe.utils.cint
apps/demo/demo/argflow.py:11  demo.argflow.load  param:name  frappe.get_list  filters  container
apps/demo/demo/argflow.py:16  demo.argflow.load  param:name  frappe.db.get_value  1
apps/demo/demo/argflow.py:18  demo.argflow.load  param:kwargs  frappe.get_doc  1
apps/demo/demo/argflow.py:20  demo.argflow.load  param:name  frappe.db.exists  1  .strip
```

Each line reads: at `path:line`, inside `caller`, the value from **source** reaches argument
**arg** of **callee**, through **via**.

- **source** is where the value started:

  | Source | Means |
  |---|---|
  | `param:name` | the function's parameter `name` (`*args` and `**kwargs` too; never `self`) |
  | `form_dict` / `form_dict:limit` | `frappe.form_dict`, or one key of it |
  | `request.headers:Host` | a part of `frappe.request` (`headers`, `args`, `data`, `json`, ...), or `frappe.get_request_header("Host")` |

- **arg** is the argument: `0`, `1`, ... by position, a keyword name (`filters`), or `*` / `**`
  for unpacking.
- **via** lists what the value went through, outermost first. Empty means it arrives as the
  caller sent it, whatever its type. Line 10 reads: `limit` went through `cint`, then into an
  f-string, then into `frappe.db.sql`.

  | Step | The code |
  |---|---|
  | a function name | the value was passed to that call and its result used (`frappe.utils.cint`) |
  | `.strip`, `.copy`, ... | a method called on the value itself |
  | `f-string` | an f-string |
  | `format` | `%` or `+` |
  | `container` | a list, tuple, dict or comprehension holding it |

  frappe-ast records the steps and leaves their meaning to you: it does not decide whether
  `cint` makes a value acceptable for a given call.

## How values are tracked

Inside one function, frappe-ast walks the statements in order and remembers what each local
name holds:

- **Assignment** carries a value to a new name: `limit = cint(...)` (line 9 to 10).
- **Branches** merge: `target` holds `name` after the `if`, because either branch may run
  (line 16).
- **Loops** carry the iterated value: `row` comes from `kwargs` (line 18). A loop body is
  walked twice, so a value assigned late in the body still reaches a call early in it.
- **Reading a part** stays direct: `x["k"]`, `x.k` and `x.get("k")` are the same value as `x`
  for the flow. Reading a key of `form_dict` names it: `form_dict:limit`.
- **Rebinding** forgets: after `name = "fixed"`, the last call carries no flow, so there is no
  row for it.
- Calls to `isinstance`, `len`, `type`, `hasattr` and similar builtins are not listed: they
  look at a value without passing it on.

## Following a value across calls

```python
@frappe.whitelist()
def outer(name, **kwargs):
	inner(name.strip(), limit=5)               # line 38
	Loader().fetch(kwargs.get("url"))          # line 39

def inner(docname, limit=None):
	frappe.get_doc("Demo Task", docname)       # line 43
	remove(docname)                            # line 44

def remove(task):
	frappe.db.delete("Demo Task", task)        # line 48
```

```
$ frappe-ast flow demo.argflow.outer --follow
apps/demo/demo/argflow.py:38  demo.argflow.outer  param:name  demo.argflow.inner  0  .strip
apps/demo/demo/argflow.py:39  demo.argflow.outer  param:kwargs  Loader().fetch  0
- apps/demo/demo/argflow.py:33  caller=demo.argflow.Loader.fetch  source=param:kwargs  callee=frappe.make_get_request  arg=0
  through (1):
    apps/demo/demo/argflow.py:39  demo.argflow.Loader.fetch (url)
- apps/demo/demo/argflow.py:43  caller=demo.argflow.inner  source=param:name  callee=frappe.get_doc  arg=1  via=.strip
  through (1):
    apps/demo/demo/argflow.py:38  demo.argflow.inner (docname)
- apps/demo/demo/argflow.py:44  caller=demo.argflow.inner  source=param:name  callee=demo.argflow.remove  arg=0  via=.strip
  through (1):
    apps/demo/demo/argflow.py:38  demo.argflow.inner (docname)
- apps/demo/demo/argflow.py:48  caller=demo.argflow.remove  source=param:name  callee=frappe.db.delete  arg=1  via=.strip
  through (2):
    apps/demo/demo/argflow.py:38  demo.argflow.inner (docname)
    apps/demo/demo/argflow.py:44  demo.argflow.remove (task)
```

The first lines are the flows in `outer` itself. Each block after them continued into another
function: **through** lists the calls it crossed, each with the parameter the value landed in.
The last block reads: `outer`'s `name`, stripped, became `inner`'s `docname`, then `remove`'s
`task`, and reached argument 1 of `frappe.db.delete`. The source stays `outer`'s parameter, and
`via` gathers the steps from every function on the way.

Arguments bind to parameters as Python binds them: by position (skipping `self` on a method),
by keyword, and into `*args` or `**kwargs` when nothing else matches. `kwargs.get("url")`
became `Loader.fetch`'s `url` through an instance call. Callees resolve as in
[`edges`](call-graph.md#edges-what-a-function-makes-run): imports, `self.` methods, overrides,
instances and documents of a named DocType. `--depth` caps the calls followed (4 by default).

On a real bench (frappe and erpnext v16; paths and qualnames shortened with `...`), a value from a
whitelisted method often crosses into a document method:

```
$ frappe-ast flow erpnext.selling.doctype.sales_order.sales_order.update_status --follow
apps/erpnext/.../sales_order.py:2039  ...sales_order.update_status  param:name  frappe.get_doc  1
apps/erpnext/.../sales_order.py:2040  ...sales_order.update_status  param:status  so.update_status  0
- apps/erpnext/.../sales_order.py:637  caller=...SalesOrder.update_status  source=param:status  callee=...SalesOrder.set_status  arg=status
  through (1):
    apps/erpnext/.../sales_order.py:2040  ...SalesOrder.update_status (status)
```

## Filters

| Option | Keeps |
|---|---|
| `--to frappe.db.set_value` | calls of that function, resolved or as written |
| `--source param`, `--source form_dict:limit` | sources that start with this |
| `--direct` | values that arrive unchanged (empty `via`) |
| `--app erpnext` | functions in that app (without `--follow`) |
| `--json` | every row as JSON, for scripts |

Without a function, `--to` searches the whole bench:

```
$ frappe-ast flow --to frappe.db.set_value --source param --direct --app erpnext --limit 2
apps/erpnext/erpnext/accounts/doctype/account/account.py:599  erpnext.accounts.doctype.account.account.update_account_number  param:name  frappe.db.set_value  1
apps/erpnext/erpnext/accounts/doctype/account/account.py:601  erpnext.accounts.doctype.account.account.update_account_number  param:name  frappe.db.set_value  1
```

## Limits

- `--follow` crosses the calls `edges` resolves, but not the document lifecycle, `doc_events`
  or hooks, which do not pass the caller's arguments through. It never follows a value back
  out through a `return`.
- Values kept on `self`, in globals, or in a dict or list changed in place (`d["k"] = x`) are
  not followed.
- Source line order stands in for execution order.

[`harness/AGENTS.md`](../harness/AGENTS.md#what-static-analysis-cannot-see) lists what the rest
of the tool cannot see.
