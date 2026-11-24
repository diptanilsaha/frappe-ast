# frappe-ast

A static index of a Frappe bench that answers how its code connects, built for humans and AI agents.

Frappe joins code through strings and conventions: desk JS calls a method by its dotted path, `hooks.py` runs code by name, and another app can override or patch a function at run time. Grep finds the text, but not who calls a method or what runs when a document saves. frappe-ast resolves both, without running app code.

Using an agent? Run `harness/install.sh ~/benches/<bench>`. It links `harness/AGENTS.md` into the bench, the guide that tells agents which query answers which question.

```sh
cd ~/benches/<bench>
frappe-ast method erpnext.selling.doctype.quotation.quotation.make_sales_order  # every caller, override and hook
frappe-ast doctype "Sales Invoice"            # controller, doc_events from every app, schema, client code
frappe-ast form "Sales Order" item_code       # the handlers a field change runs, in desk order
frappe-ast edges erpnext.controllers.stock_controller.StockController.validate   # what it makes run
frappe-ast reach frappe.utils.response.send_private_file   # every route from an entry point to it
frappe-ast flow frappe.api.v2.bulk_update_docs --follow   # where each argument goes, across calls
frappe-ast check apps/erpnext                 # dead hooks, missing methods, unknown fields
frappe-ast impact --base origin/develop       # what a diff touches, and who depends on it
```

## Install

```sh
uv tool install "git+https://github.com/diptanilsaha/frappe-ast.git"
```

It runs on Python 3.11 and later and reads v15, v16 and develop benches.

## How it works

It parses Python with `ast`, JS, TS and Vue with tree-sitter, and `hooks.py` and DocType JSON as data, into a SQLite index at `~/.cache/frappe-ast/`. The first run indexes the bench in seconds; after that each query re-reads only the files that changed. Queries join the facts across files and apps.

## Output

- One line per fact, `path:line` first, paths relative to the bench root.
- `--json` for code that filters the output; `--limit N` caps rows per section.
- `--bench` picks the bench (default: the nearest one above the current directory); `--no-refresh` skips the index refresh.

## Commands

| Command | What it does |
|---|---|
| `method <dotted>` | Definition, overrides, patches, hooks, Python callers and client calls (`frappe.call`, `frm.call`, frappe-ui, URLs, Button fields). |
| `callers` / `callees` | Python calls into or out of a symbol, resolved through imports and re-exports. |
| `edges <fn>` / `paths <from> <to>` | The call graph: inherited and overriding methods, constructors, document lifecycle, `doc_events`, hook dispatch, functions passed to `frappe.enqueue`. |
| `flow <fn> [--follow]` | Where a function's parameters and request input go: each call and argument they reach, unchanged or through what, and on into the functions they are passed to. |
| `surface` / `reach <fn>` | Functions that requests, pages, documents, hooks and jobs run directly, and the route from each to a function. |
| `doctype <name>` | Controller and its bases, `doc_events`, overrides, schema, links and the client code that names its fields. |
| `form <DocType> [field]` | A desk form's scripts from every app, its JS controller chain, and the handlers per event. |
| `record <name>` | A standard Report, Page, Web Form, Print Format, ... with the code beside its JSON. |
| `component <name>` | A Vue component in an app or its frappe-ui copy: props, emits, slots, usages. |
| `refs <target>` | References to a DocType, field, component, form event, realtime event or import. |
| `find` / `outline` | Symbol definitions by name or glob; the symbols of one file. |
| `hooks` / `endpoints` / `apps` | Hook entries folded across apps; whitelisted methods; apps, `required_apps` and frontends. |
| `check [path]` | Frappe-aware rules: dead hook paths, missing methods, unknown fields, broken records, stale patches. |
| `impact --base <ref>` | Symbols a diff touches, with their callers, hooks and patches. |
| `sql <query>` | Read-only SQL over the index. |
| `index` / `status` | Build or refresh the index; where it is and how fresh. |

[How it works](docs/how-it-works.md) shows how the index is built and the flow behind every command. The guides walk through commands with sample output: [call graph and entry points](docs/call-graph.md) (`edges`, `paths`, `surface`, `reach`) and [argument flow](docs/argument-flow.md) (`flow`). `frappe-ast <command> --help` lists each command's options. `AGENTS.md` is the guide for agents that work on this tool.
