# frappe-ast

A static index of a Frappe bench that shows how its code connects: who calls a method, what runs when a document saves, where request input goes, and what a diff touches. It reads the code and never runs it.

Frappe links code by name. Desk JS calls a method by its dotted path, `hooks.py` runs functions from strings, and another app can override a DocType's class. Grep finds the text but not the link. frappe-ast resolves the link, so a person or an agent goes from a bug or an untrusted input straight to the lines that matter.

## Install

```sh
uv tool install "git+https://github.com/diptanilsaha/frappe-ast.git"
```

Python 3.11 or later. It reads v15, v16 and develop benches.

## First run

```sh
cd ~/benches/<bench>
frappe-ast doctype "Sales Invoice"
```

The first query indexes the bench into SQLite under `~/.cache/frappe-ast/`, in up to a minute. Each later query re-reads only the files that changed.

**For an agent**, link the bench guide into the bench: `harness/install.sh ~/benches/<bench>`. The agent then reads `harness/AGENTS.md`, which says which query answers which question.

## What it answers

| Question | Example |
|---|---|
| Who defines, overrides, hooks or calls a method? | `method erpnext.selling.doctype.quotation.quotation.make_sales_order` |
| What does a function make run, and how do two functions connect? | `edges <function>` · `paths <from> <to>` |
| What can make a function run: a request, a document save, a hook, a job? | `reach erpnext.stock.stock_ledger.make_sl_entries` |
| Where do a function's parameters and the request input go? | `flow frappe.api.v2.bulk_update_docs --follow` |
| Everything about a DocType, or what a form field change runs | `doctype "Sales Invoice"` · `form "Sales Order" item_code` |
| Who references a DocType, field or event? | `refs "Item"` |
| Which hooks, methods or fields are broken? What does a diff touch? | `check apps/erpnext` · `impact --base origin/develop` |

`frappe-ast --help` lists every command, and `frappe-ast <command> --help` lists its options.

## Output

- One fact per line, `path:line` first, relative to the bench root.
- `--json` for scripts (it adds `col`, the column); `--limit N` caps the rows per section.
- `--bench <path>` picks another bench than the one above the current directory; `--no-refresh` skips the refresh.

## Docs

| Read | When you |
|---|---|
| [`harness/AGENTS.md`](harness/AGENTS.md) | run an agent in a bench: the query for each question, and what static analysis cannot see |
| [How it works](docs/how-it-works.md) | want to know how the index is built and refreshed, with a diagram per command |
| [Call graph and entry points](docs/call-graph.md) | read `edges`, `paths`, `surface` or `reach` output |
| [Argument flow](docs/argument-flow.md) | read `flow` output |
| [The index's facts](docs/facts.md) | write `sql` queries over the index |
| [`AGENTS.md`](AGENTS.md) | change this tool |
