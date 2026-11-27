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
apps/demo/demo/argflow.py:7  load  param:doctype  frappe.get_doc  0
apps/demo/demo/argflow.py:7  load  param:name  frappe.get_doc  1
apps/demo/demo/argflow.py:8  load  param:kwargs  doc.update  0
apps/demo/demo/argflow.py:9  load  form_dict:limit  frappe.utils.cint  0
apps/demo/demo/argflow.py:10  load  param:doctype  frappe.db.sql  0  f-string
apps/demo/demo/argflow.py:10  load  form_dict:limit  frappe.db.sql  0  f-string,frappe.utils.cint
apps/demo/demo/argflow.py:11  load  param:name  frappe.get_list  filters  container
apps/demo/demo/argflow.py:16  load  param:name  frappe.db.get_value  1
apps/demo/demo/argflow.py:18  load  param:kwargs  frappe.get_doc  1
apps/demo/demo/argflow.py:20  load  param:name  frappe.db.exists  1  .strip
```

Each line reads: at `path:line`, inside `caller` (named without the module its path shows),
the value from **source** reaches argument **arg** of **callee**, through **via**.

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
  | `compare` | a comparison: `field >= start` builds a query criterion that holds `start` |
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
- **Filling a container in place** carries the value into it: after `filters["name"] = name`,
  `row.title = name`, `rows.append(name)` or `grouped[k].append(name)`, the local name the
  container hangs off holds `name` (via `container`). On `self` (`self.rows[k] = name`) it is
  a store on the instance.
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
apps/demo/demo/argflow.py:38  outer  param:name  demo.argflow.inner  0  .strip
apps/demo/demo/argflow.py:39  outer  param:kwargs  Loader().fetch  0
- through=apps/demo/demo/argflow.py:39  demo.argflow.Loader.fetch (url)
  rows (1):
    apps/demo/demo/argflow.py:33  Loader.fetch  param:kwargs  frappe.make_get_request  0
- through=apps/demo/demo/argflow.py:38  demo.argflow.inner (docname)
  rows (2):
    apps/demo/demo/argflow.py:43  inner  param:name  frappe.get_doc  1  .strip
    apps/demo/demo/argflow.py:44  inner  param:name  demo.argflow.remove  0  .strip
- through=apps/demo/demo/argflow.py:38  demo.argflow.inner (docname) > apps/demo/demo/argflow.py:44  demo.argflow.remove (task)
  rows (1):
    apps/demo/demo/argflow.py:48  remove  param:name  frappe.db.delete  1  .strip
```

The first lines are the flows in `outer` itself. Each block after them is one chain of calls
the value crossed, joined by `>`, each with the parameter the value landed in; its **rows** are
the flows at the end of that chain. The last block reads: `outer`'s `name`, stripped, became `inner`'s `docname`, then `remove`'s
`task`, and reached argument 1 of `frappe.db.delete`. The source stays `outer`'s parameter, and
`via` gathers the steps from every function on the way.

Arguments bind to parameters as Python binds them: by position (skipping `self` on a method),
by keyword, and into `*args` or `**kwargs` when nothing else matches. `kwargs.get("url")`
became `Loader.fetch`'s `url` through an instance call. Callees resolve as in
[`edges`](call-graph.md#edges-what-a-function-makes-run): imports, `self.` methods, overrides,
instances and documents of a named DocType. `--depth` caps the calls followed (4 by default).

On a real bench (frappe and erpnext v16; paths, qualnames and chains shortened with `...`), a
value from a whitelisted method often crosses into document methods, a store on `self` and on
into the framework:

```
$ frappe-ast flow erpnext.selling.doctype.sales_order.sales_order.update_status --follow
apps/erpnext/.../sales_order.py:2222  update_status  param:name  frappe.get_doc  1
apps/erpnext/.../sales_order.py:2226  update_status  param:status  so.update_status  0
- through=apps/erpnext/.../sales_order.py:2226  ...SalesOrder.update_status (status)
  rows (1):
    apps/erpnext/.../sales_order.py:683  SalesOrder.update_status  param:status  ...SalesOrder.set_status  status
- through=... > apps/erpnext/.../sales_order.py:683  ...StatusUpdater.set_status (status) > ... > apps/erpnext/.../invoice_discounting.py:115  frappe.model.document.Document.db_set (value)
  rows (3):
    apps/frappe/frappe/model/document.py:1810  Document.db_set  param:status  frappe.model.document.Document.set  1
    apps/frappe/frappe/model/document.py:1829  Document.db_set  param:status  frappe.db.set_single_value  2
    apps/frappe/frappe/model/document.py:1838  Document.db_set  param:status  frappe.db.set_value  3
```

## Values that come back from a call, and values kept on `self`

```python
class Finder:
	def find(self, key, limit=None):
		self.limit = limit                               # line 56
		self.query()
		return self.rows

	def query(self):
		frappe.get_all("Demo Task", limit=self.limit)    # line 61

def search(key, limit):
	rows = Finder().find(key, limit=limit)               # line 69
	for row in rows:
		frappe.msgprint(row.title)                       # line 71
	frappe.msgprint(echo(key))                           # line 72: echo returns text.strip()
	frappe.msgprint(str(limit))                          # line 73
```

```
$ frappe-ast flow demo.argflow.search --follow
apps/demo/demo/argflow.py:69  search  param:key  Finder().find  0
apps/demo/demo/argflow.py:69  search  param:limit  Finder().find  limit
apps/demo/demo/argflow.py:72  search  param:key  demo.argflow.echo  0
apps/demo/demo/argflow.py:72  search  param:key  frappe.msgprint  0  demo.argflow.echo
apps/demo/demo/argflow.py:73  search  param:limit  str  0
apps/demo/demo/argflow.py:73  search  param:limit  frappe.msgprint  0  str
- through=apps/demo/demo/argflow.py:69  demo.argflow.Finder.find (limit)
  rows (1):
    apps/demo/demo/argflow.py:56  Finder.find  param:limit  demo.argflow.Finder.limit  =
- through=apps/demo/demo/argflow.py:69  demo.argflow.Finder.find (limit) > apps/demo/demo/argflow.py:56  demo.argflow.Finder.query (self.limit)
  rows (1):
    apps/demo/demo/argflow.py:61  Finder.query  param:limit  frappe.get_all  limit
```

- **A call's return value** carries an argument only when the callee returns it. `find` returns
  `self.rows`, so the rows at line 71 carry neither `key` nor `limit`, and there is no row for
  line 71. `echo` returns its parameter, so line 72 keeps `key`. Calling a class
  (`Query(doctype)`) returns the instance, so its arguments stay with it. A callee outside the bench
  (`str`, a library method) or one frappe-ast cannot resolve keeps every argument it is given:
  its `via` step is its name or `call`.
- **A store on the instance** is a row with arg `=` (line 56). `--follow` continues from it into
  every method of the class and its subclasses that reads `self.limit`, whichever runs first:
  the hop shows as `Finder.query (self.limit)`.

A value that passed through a query's result (`via` ending in `.run` or `call` after a query
builder) shaped the query: it chose which rows came back, and the rows may not contain it.

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
apps/erpnext/erpnext/accounts/doctype/account/account.py:658  set_account_name_and_number  param:account  frappe.db.set_value  1
apps/erpnext/erpnext/accounts/doctype/bank_reconciliation_tool/bank_reconciliation_tool.py:486  update_clearance_date  param:clearance_date  frappe.db.set_value  3
```

## Limits

- `--follow` crosses the calls `edges` resolves, but not the document lifecycle, `doc_events`
  or hooks, which do not pass the caller's arguments through. It follows a value back out
  through a `return` only to check that the callee returns it, not into the caller's caller.
- A store on `self` reaches the methods of the class whose file stores it; a subclass in another
  file that reads it is followed, but a base class in another file that stores it is not seen.
  Values kept in globals are not followed. A value set on another object (`doc.x = v`) stays
  with that local name, and is not followed into other functions that read `doc.x`.
- A callee that puts an argument into another argument and returns that one
  (`rows.append(x); return rows` is followed, `other.add(x); return other` on a parameter is not)
  can drop a row.
- Source line order stands in for execution order inside a function; across the methods of a
  class, order is not known.

[`harness/AGENTS.md`](../harness/AGENTS.md#what-static-analysis-cannot-see) lists what the rest
of the tool cannot see.
