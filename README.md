# frappe-ast

frappe-ast indexes a Frappe bench and answers questions about how its code connects.

## The problem

Frappe joins code through strings and conventions, not imports. A whitelisted method is called from JS as `frappe.call("app.api.x")`, as `frm.call("x")`, or from a frappe-ui resource. `hooks.py` and DocType JSON run other code by name. Another app can override a function, extend a controller class, or patch it at run time. So grep cannot tell you who calls a method or what runs when a document is saved. An agent that changes the code misses callers and breaks them.

## How it works

1. It parses every app in the bench: Python with the stdlib `ast`, JS, TS and Vue with tree-sitter, and `hooks.py` and DocType JSON as data. It never runs app code.
2. It writes the facts to a SQLite index: symbols, calls, the references that Frappe APIs make, hooks, DocTypes and fields.
3. Queries join those facts across files and apps, and print one line per fact, `path:line` first.

It runs on Python 3.11 and later, so it installs next to a v15 bench. It still reads v16 code, which is written for 3.14. The index is at `~/.cache/frappe-ast/<bench>-<hash>.sqlite`. Each query re-reads only the files that changed.

## What it handles

- **Server calls from Python:** imports, re-exports through a package `__init__`, `frappe.enqueue`, `frappe.get_attr`, and `/api/method/...` URLs in strings and f-strings.
- **Server calls from desk JS:** `frappe.call`, `frappe.xcall`, `frm.call`, `frappe.call({doc})`, `run_doc_method`, page methods (`{module, page, method}`), and `/api/method/...` URLs.
- **Method paths that code builds:** constants, `this.x` properties and local variables (`${METHOD}.x`, `prefix + "x"`), and functions that pass a method name through (`api("get_x")`).
- **Frontend calls:** frappe-ui (`createResource`, `useCall`, `useDoc`, `useDoctype`, `createListResource`, v2 URLs), handles imported from another file, method paths in Vue template props, and frappe-react-sdk.
- **DocType controller methods:** the form's own controller, its base classes, `override_doctype_class`, `extend_doctype_class`, and Button fields whose `options` name a method.
- **Desk forms:** the scripts each app loads in desk order, `frappe.ui.form.on` handlers, the JS controller class chain, and shared `public/js` code that installs itself on forms (setup functions, `@self` handlers, `$.extend` helpers, classes made with `new`).
- **Call graph and attack surface:** what each function makes run (calls, inherited and overriding methods, `Cls()` into `__init__`, document lifecycle and `doc_events`, `frappe.get_hooks` dispatch, functions passed to `frappe.enqueue` or a werkzeug `Rule`), and every route from something outside the process (whitelisted methods, REST routes, the request pipeline, website pages, controller lifecycle methods, hooks, jobs) to a function.
- **Hooks:** `doc_events`, `scheduler_events`, `override_whitelisted_methods`, `regional_overrides` and the rest, including keys that `hooks.py` computes.
- **Monkey patches:** `module.f = g`, `Class.m = g`, `setattr(...)`, wrappers such as frappe's recorder on `Document.run_method`, and patches on `frappe.db`.
- **DocTypes and records:** fields, permissions, connections, actions and states from JSON, custom fields that apps ship, and Report, Page, Web Form and Number Card records.
- **frappe-ui copies:** the version each frontend builds against, and the props, emits and slots of each component in that copy.
- **Checks:** hook paths that do not resolve, calls to missing or not whitelisted methods, unknown fields, missing `required_apps`, broken records, and patches on functions that no longer exist.
- **Diffs:** the symbols a change touches, and their callers, hooks and patches.

## Use

```sh
uv tool install "git+https://github.com/diptanilsaha/frappe-ast.git"
cd ~/benches/<bench>
frappe-ast method erpnext.selling.doctype.quotation.quotation.make_sales_order   # who calls it, what replaces it
frappe-ast doctype "Sales Invoice"        # controller, hooks, schema, client code
frappe-ast form "Sales Order" item_code   # what runs in the desk form, in load order
frappe-ast check apps/erpnext             # broken hooks, calls, fields and patches
frappe-ast impact --base origin/develop   # what a diff touches
frappe-ast reach frappe.utils.response.send_private_file   # every route from a request to it
frappe-ast --help
```

To link the agent guide into a bench, run `harness/install.sh ~/benches/<bench>`. `harness/AGENTS.md` is the guide for agents that work in a bench. `AGENTS.md` is the guide for agents that work on this tool.
