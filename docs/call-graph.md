# Call graph and entry points

Frappe runs much of its code without a visible call: `doc.cancel()` runs the controller's
`on_cancel` and every app's `doc_events`, `frappe.get_hooks("x")` calls whatever `hooks.py`
registers under `x`, and `frappe.enqueue(fn)` hands a function to a worker. `callers` and
`callees` stop at those boundaries. The call graph crosses them, and the entry-point queries
use it to answer the reverse question: what can make this function run?

| Question | Command |
|---|---|
| What does this function make run? | `edges <function>` |
| How does one function get to another? | `paths <from> <to>` |
| Which functions does something outside the process run directly? | `surface` |
| What makes this function run, and by which route? | `reach <function>` |

The samples below come from a v16 bench with frappe and erpnext. Paths are relative to the
bench root, and qualnames are full dotted paths, so lines are long.

## `edges`: what a function makes run

```python
# erpnext/accounts/doctype/pos_invoice_merge_log/pos_invoice_merge_log.py
def merge_cancel_and_close():
	for log in merge_logs:
		merge_log = frappe.get_doc("POS Invoice Merge Log", log)
		...
		merge_log.cancel()
```

```
$ frappe-ast edges erpnext.accounts.doctype.pos_invoice_merge_log.pos_invoice_merge_log.cancel_merge_logs.merge_cancel_and_close
apps/erpnext/.../pos_invoice_merge_log.py:650  ...merge_cancel_and_close  frappe.model.document.get_doc  call
apps/erpnext/.../pos_invoice_merge_log.py:655  ...merge_cancel_and_close  frappe.model.document.Document.cancel  doc_method
apps/erpnext/.../pos_invoice_merge_log.py:655  ...merge_cancel_and_close  erpnext.accounts.doctype.pos_invoice_merge_log.pos_invoice_merge_log.POSInvoiceMergeLog.on_cancel  lifecycle
apps/erpnext/.../pos_invoice_merge_log.py:655  ...merge_cancel_and_close  frappe.desk.notifications.clear_doctype_notifications  doc_event
apps/erpnext/.../pos_invoice_merge_log.py:655  ...merge_cancel_and_close  frappe.workflow.doctype.workflow_action.workflow_action.process_workflow_actions  doc_event
apps/erpnext/.../pos_invoice_merge_log.py:655  ...merge_cancel_and_close  frappe.automation.doctype.assignment_rule.assignment_rule.apply  doc_event
apps/erpnext/.../pos_invoice_merge_log.py:655  ...merge_cancel_and_close  frappe.automation.doctype.milestone_tracker.milestone_tracker.evaluate_milestone  doc_event
```

Each line is one edge: where the call is, the caller, the function it runs, and the **kind**,
which says how frappe-ast knows. One `merge_log.cancel()` gives seven edges: the method itself,
the controller's `on_cancel`, and four `doc_events` handlers registered for every DocType.
(The samples shorten paths and callers with `...`; the tool prints them in full.)

| Kind | The code | Runs |
|---|---|---|
| `call` | `make_sl_entries(...)`, `Engine(...)` | the function, resolved through imports and re-exports; a class runs its `__init__` |
| `inherited` | `self.update_blanket_order()` | the method on a base class |
| `override` | `self.write()` in a base class | each subclass's own `write`, up to five subclasses |
| `instance` | `engine = Engine(); engine.run()`, `with Lock():` | `Engine.run`; `Lock.__enter__` and `Lock.__exit__` (`__aenter__`, `__aexit__` for `async with`) |
| `super` | `super().validate()`, `super(Cls, self).save()` | the method on the nearest base class that defines it |
| `property` | `self.meta`, `Conn().session`, `conn.session = s` | the `@property` or `cached_property` getter the read runs, or the `@x.setter` the assignment runs |
| `doc_method` | `doc = frappe.get_doc("Sales Order", n); doc.update_status(s)` | the method on that DocType's controller |
| `lifecycle` | `doc.save()`, `doc.cancel()`, `frappe.delete_doc("X", n)` | the controller methods the document layer runs for it (`validate`, `on_update`, `on_cancel`, ...) |
| `doc_event` | the same calls | `hooks.py` `doc_events` handlers for that DocType and for `"*"`, from every app |
| `hook` | `frappe.get_hooks("update_website_context")` | every function registered under that key |
| `proxy` | `frappe.qb.get_query()` | the method on the attribute's declared type, or the function patched onto it |
| `callback` | `frappe.enqueue(fn)`, `{"run": fn}` | a function passed along or stored in a dict, which the receiver may call |
| `dispatch` | `frappe.enqueue("app.module.fn")` | the function a dotted string names, for APIs that call it |
| `dotted` | `method="app.module.fn"` | any other string naming a bench function |

Filter with `--kind lifecycle`, or list a whole app's edges with `edges --app erpnext --json`.

frappe-ast adds an edge only when the code names its target: an import, a DocType literal, a
hook key, a declared type. `doc.save()` on a document whose DocType is a variable gets no
`lifecycle` edges, rather than an edge to every `validate` on the bench. A missing edge can
hide a route; a guessed edge would invent one.

## `paths`: how one function gets to another

```
$ frappe-ast paths erpnext.selling.doctype.sales_order.sales_order.update_status erpnext.selling.doctype.sales_order.sales_order.SalesOrder.update_reserved_qty
- route=1
  steps (2):
    apps/erpnext/.../sales_order.py:2040  ...sales_order.update_status -[doc_method]-> ...sales_order.SalesOrder.update_status
    apps/erpnext/.../sales_order.py:642  ...sales_order.SalesOrder.update_status -[call]-> ...sales_order.SalesOrder.update_reserved_qty
```

The shortest routes first, up to `--limit` of them, each step with its edge kind. `--depth`
caps the number of calls (12 by default).

## `surface`: entry points

An entry point is a function that something outside the Python process runs directly: an
HTTP request, a document operation from desk or the REST API, a hook, a scheduled job.

```
$ frappe-ast surface --kind scheduler --limit 2
- function=erpnext.accounts.deferred_revenue.process_deferred_accounting
  kinds (1):
    scheduler
- function=erpnext.accounts.doctype.bank_transaction_rule.bank_transaction_rule.scheduler_run_rule_evaluation
  kinds (1):
    scheduler
```

| Kind | Found from | Count on the sample bench |
|---|---|---|
| `api` | `@frappe.whitelist()`, `override_whitelisted_methods`, `standard_queries` | 1353 |
| `guest_api` | `@frappe.whitelist(allow_guest=True)` | 65 |
| `route` | `werkzeug.routing.Rule(..., endpoint=fn)`: the REST API's own handlers | 27 |
| `request` | `frappe.app.application` and request-time hooks (`before_request`, `on_session_creation`, `website_context`, ...) | 16 |
| `www` | `get_context` in `www/`, `templates/pages/` and web forms | 47 |
| `doc_lifecycle` | a controller's lifecycle method (`validate`, `on_submit`, ...) | 1106 |
| `doc_event` | `hooks.py` `doc_events` | 45 |
| `permission` | `has_permission`, `permission_query_conditions`, `has_website_permission` | 50 |
| `scheduler` | `scheduler_events` | 86 |
| `template` | `jinja` methods and filters | 4 |
| `report` | a report's module-level `execute` | 177 |
| `hook` | any other `hooks.py` value that names a function | 48 |

A function can have several kinds. `surface --reached` lists every function the entry points
reach through the call graph, with the fewest calls from any of them and every kind that gets
there:

```
$ frappe-ast surface --reached --json | jq -c '.[] | select(.function == "erpnext.stock.stock_ledger.make_sl_entries")'
{"function":"erpnext.stock.stock_ledger.make_sl_entries","kinds":["api","doc_lifecycle"],"depth":2}
```

## `reach`: what makes a function run

```
$ frappe-ast reach erpnext.stock.stock_ledger.make_sl_entries --limit 2
- root=erpnext.stock.doctype.stock_entry.stock_entry.StockEntry.on_submit  kinds=doc_lifecycle
  steps (2):
    apps/erpnext/.../stock_entry.py:700  ...StockEntry.on_submit -[call]-> ...StockEntry.update_stock_ledger
    apps/erpnext/.../stock_entry.py:2762  ...StockEntry.update_stock_ledger -[call]-> erpnext.stock.stock_ledger.make_sl_entries
- root=erpnext.stock.doctype.stock_entry.stock_entry.StockEntry.on_cancel  kinds=doc_lifecycle
  steps (2):
    apps/erpnext/.../stock_entry.py:738  ...StockEntry.on_cancel -[call]-> ...StockEntry.update_stock_ledger
    apps/erpnext/.../stock_entry.py:2762  ...StockEntry.update_stock_ledger -[call]-> erpnext.stock.stock_ledger.make_sl_entries
```

One route per entry point, shortest first. Read it top down: submitting a Stock Entry runs
`on_submit`, which calls `update_stock_ledger`, which calls `make_sl_entries`. No routes means
no entry point reaches the function through edges frappe-ast knows; see the limits below
before you treat it as dead code.

## Limits

The graph is static. It has no edge for a function called through a name only known at run
time (`frappe.get_attr(var)`, `getattr(doc, name)`), a method a library calls on an object you
hand it, or `self.m()` when more than five subclasses define `m`. Source line order stands in
for execution order. [`harness/AGENTS.md`](../harness/AGENTS.md#what-static-analysis-cannot-see)
lists the rest.
