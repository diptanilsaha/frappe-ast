# frappe-ast

frappe-ast indexes a Frappe bench and answers questions about how its code connects.

## The problem

Frappe joins code through strings and conventions, not imports. A whitelisted method is called from JS as `frappe.call("app.api.x")`, as `frm.call("x")`, or from a frappe-ui resource. `hooks.py` and DocType JSON run other code by name. Another app can override a function, extend a controller class, or patch it at run time. So grep cannot tell you who calls a method or what runs when a document is saved. An agent that changes the code misses callers and breaks them.

## How it works

1. It parses every app in the bench: Python with the stdlib `ast`, JS, TS and Vue with tree-sitter, and `hooks.py` and DocType JSON as data. It never runs app code.
2. It writes the facts to a SQLite index: symbols, calls, the references that Frappe APIs make, hooks, DocTypes and fields.
3. Queries join those facts across files and apps, and print one line per fact, `path:line` first.

It runs on Python 3.11 and later, so it installs next to a v15 bench. It still reads v16 code, which is written for 3.14. The index is at `~/.cache/frappe-ast/<bench>-<hash>.sqlite`. Each query re-reads only the files that changed.

## Use

```sh
uv tool install "git+https://github.com/diptanilsaha/frappe-ast.git"
cd ~/benches/<bench>
frappe-ast method erpnext.selling.doctype.quotation.quotation.make_sales_order   # who calls it, what replaces it
frappe-ast doctype "Sales Invoice"        # controller, hooks, schema, client code
frappe-ast form "Sales Order" item_code   # what runs in the desk form, in load order
frappe-ast check apps/erpnext             # broken hooks, calls, fields and patches
frappe-ast impact --base origin/develop   # what a diff touches
frappe-ast --help
```

To link the agent guide into a bench, run `harness/install.sh ~/benches/<bench>`. `harness/AGENTS.md` is the guide for agents that work in a bench. `AGENTS.md` is the guide for agents that work on this tool.
