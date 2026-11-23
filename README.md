# frappe-ast

A static AST index and query tool for Frappe benches. It parses Python (stdlib `ast`), JS/TS (tree-sitter), Vue SFCs, `hooks.py` and DocType JSON, and answers the questions that grep cannot answer:

- Who calls this whitelisted method? This covers Python callers through imports and re-exports, `frappe.call`, `frm.call`, frappe-ui `createResource` / `useCall` / `useDoc` / `useDoctype`, method paths kept in constants, and `frappe.enqueue`.
- What runs when this DocType is saved? This covers the controller lifecycle, `doc_events` from every app, overrides, and form JS.
- What runs in the desk form when this field changes? Run `form "Sales Order" item_code` to see. It covers scripts from every app in desk load order, `frappe.ui.form.on` handlers, and the JS controller class chain (`erpnext.selling.SalesOrderController` → `SellingController` → `TransactionController` ...).
- What is this Report, Page or Web Form made of, and is it wired correctly? Run `record "General Ledger"` and `check --rule FX208` to see.
- Which hooks, client calls or `frm.call` targets are broken, which fields named in frontends do not exist, and which `required_apps` are missing? Run `check` to see.
- Which frontend code breaks when this field is renamed? Run `doctype <Name>` and `refs "<DocType>.<field>" --kind field`.
- Which frappe-ui version does each frontend build against, and what props, emits and slots does `<Dialog>` take in crm's copy? Run `apps` and `component Dialog --app crm` to see. Each distinct frappe-ui copy in `node_modules` is indexed as its own library.
- What does this diff touch, and what depends on it? Run `impact` to see.

## Install

From GitHub:

```sh
uv tool install "git+https://github.com/diptanilsaha/frappe-ast.git"
uv tool upgrade frappe-ast      # later, to get the latest main
```

To pin a version, add `@<branch, tag or commit>` to the URL, for example `...frappe-ast.git@<ref>`. To run it once without installing, use `uvx --from "<same URL>" frappe-ast status`.

From a local checkout, when you work on the tool itself (code changes take effect without a reinstall):

```sh
uv tool install --editable ~/benches/frappe-ai-toolchains/frappe-ast
```

Without any install, use `uv run --project ~/benches/frappe-ai-toolchains/frappe-ast frappe-ast ...`.

Then link the bench agent guide into each bench root. This makes AGENTS.md and CLAUDE.md symlinks, and skips any that already exist:

```sh
~/benches/frappe-ai-toolchains/frappe-ast/harness/install.sh ~/benches/bench16 ~/benches/bench15
```

## Use

```sh
cd ~/benches/bench16
frappe-ast doctype "Quotation"
frappe-ast method erpnext.selling.doctype.quotation.quotation.make_sales_order
frappe-ast outline apps/erpnext/erpnext/selling/doctype/quotation/quotation.js
frappe-ast check apps/erpnext/erpnext/selling
frappe-ast impact --base origin/develop --apps erpnext
frappe-ast --help
```

The index lives at `~/.cache/frappe-ast/<bench>-<hash>.sqlite`. Each query refreshes changed files first. A full bench indexes in 4–10 s.

- `harness/AGENTS.md` is the guide for agents that work in a bench.
- `AGENTS.md` is the guide for agents that work on this tool.
