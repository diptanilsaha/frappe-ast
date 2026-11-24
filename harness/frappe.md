# Frappe facts the code does not show

Read this before you debug a desk form, a frappe-ui call, a report, realtime events or
transaction behaviour. Each fact comes from frappe's own code, not from the app in front of you.

## Desk forms

- Desk loads a form's scripts in this order (`frappe/desk/form/meta.py`): `<name>.js`, then `regional/<country>.js` (only when it matches the System Settings country), `<name>_list.js`, `_kanban`, `_calendar` and `_tree`, then each app's `doctype_js` hook files. No other JS in the DocType folder loads by itself. Client Scripts from the database run as well.
- For one form event, desk calls these handlers in order (`script_manager.js`): the `frappe.ui.form.on("<DocType>")` handlers in load order, the `frappe.ui.form.on("*")` handlers, `frm.cscript[event]`, and `frm.cscript["custom_" + event]`. `frm.cscript` is the JS controller class that `cur_frm.script_manager.make(X)` or `extend_cscript(cur_frm.cscript, new X({frm}))` installs, for example `erpnext.selling.QuotationController`, which extends `SellingController`, which extends `TransactionController`. A child-table field change runs `frappe.ui.form.on("<Child DocType>")[field]` and the parent form's `cscript[field]`. In child handlers, `frm` is still the parent form.
- `cur_frm` is whichever form is open now. After `frappe.new_doc(...)` or a route change, it is another form. In new code, use the `frm` argument.
- `frm.add_fetch(link, source, target)` without a fourth argument applies to every DocType on the form (`"*"`), including child tables.
- `frm.call("x")`, `frm.call({doc: frm.doc, method: "x"})` and `frappe.call({doc: frm.doc, method: "x"})` (desk sends `run_doc_method`) call the whitelisted **controller method** `x`. `frm.call({method: "x"})` without `doc` calls the module-level function `<controller module>.x`. A dotted method is called as given.
- erpnext writes form code once in `public/js` and installs it on many forms: a JS controller class (`SalesInvoiceController` extends `SellingController` extends `TransactionController`), a class a form creates with `new erpnext.utils.CRMNotes({frm})`, a setup function a form calls with its DocType (`erpnext.accounts.taxes.setup_tax_validations("Sales Invoice")` runs `frappe.ui.form.on(doctype, {...})`), `frappe.ui.form.on(this.frm.doctype, "field", fn)` and `this.frm.doctype + " Item"` inside a class, and a helper forms call (`hrms.fetch_geolocation(frm)`). `form` and `method` follow each way: each `frm.call` in that code counts for every form it runs on, looked up on each form's controller and its bases, and `form` lists the handlers it registers with their `public/js` path.
- The server ignores `depends_on`, `mandatory_depends_on` and `read_only_depends_on`. Only the desk UI evaluates them.

## Server methods and URLs

- A string that is a whole `/api/method/<dotted>` URL, with or without a query string (`open_url_post`, `window.open`, an `href`), names that method. `method` lists it in `string_refs` with `url` as via. FX202 does not check these URLs, because code can build a path from a URL prefix.
- On whitelisted functions, type annotations are enforced at request time (`validate_argument_types`). A wrong annotation rejects valid calls.
- Dotted strings in `hooks.py` fail only at run time, when the hook fires. `check --rule FX201` finds the dead ones.

## frappe-ui

- `createResource({url: "app.api.fn"})` expands a dotted path. `useCall({url})` uses the URL as given, so it needs `/api/v2/method/app.api.fn`. `createListResource`, `useList` and `useDoc` accept `url` too, which replaces the default list or document method.
- frappe-ui runs DocType code in two ways. **Controller methods** (`@frappe.whitelist()` on the class): `useDoc({methods: {k: "m"}})`, `createDocumentResource({whitelistedMethods})`, `useDoctype(X).runDocMethod.submit({name, method})`, and `/api/v2/document/X/<name>/method/m`. **Module functions** in the controller file: `useDoctype(X).runMethod.submit({method})` and `/api/v2/method/X/m`. The index records both as `<DocType>:<method>`, and FX204 checks both.
- frappe-ui is not one library. Each frontend builds against its own `node_modules/frappe-ui`, and the versions differ (on maintain-bench: crm `1.0.0-rc.2`, lms and helpdesk `1.0.0-rc.1`, gameplan `1.0.2`). Before you use or debug a frappe-ui API, run `apps` or `component <Name> --app <app>` and read the source of *that* copy. The index holds each distinct copy as `frappe-ui@<version>`; a `~<hash>` suffix marks a copy with the same version but different files (a git build, a patched `node_modules`).
- `apps` marks a frontend `STALE` when `node_modules` does not match the version `package.json` pins. The code runs against the older copy until someone runs `yarn install`.

## Reports, pages and realtime

- A standard Report is three files that must agree: `<name>.json` (type, `ref_doctype`, roles), `<name>.py` with a module-level `execute(filters)`, and `<name>.js`, which registers `frappe.query_reports["<exact report name>"]`. A Page registers `frappe.pages["<page_name>"]`. `check --rule FX208` finds a mismatch.
- `frappe.publish_realtime("event")` in Python reaches `frappe.realtime.on("event")` in JS only when the names match exactly. `refs <event> --kind realtime` lists both sides.

## Documents and transactions

- `frappe.get_all` ignores user permissions; `frappe.get_list` applies them. `frappe.db.set_value` and `frappe.db.sql` skip permissions, validations and controller hooks. `doc.save()` and `doc.insert()` check permissions unless `ignore_permissions` is set.
- The request commits when it succeeds. `frappe.db.commit()` in an endpoint or a lifecycle method commits partial work that a later error cannot roll back. Inside `doc_events` handlers, transaction control is disabled.

## Versions

`extend_doctype_class` exists only in v16 and later. Per bench: `bench15` is v15 (Python 3.13); `bench16` is v16 (Python 3.14); `maintain-bench` is develop / 17.0.0-dev (Python 3.14) with the Vue apps crm, gameplan, helpdesk and lms. Check the version before you use an API from another bench.
