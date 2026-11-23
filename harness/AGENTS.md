# Frappe bench: agent guide

This bench holds Frappe apps under `apps/<app>/`. Python runs on the server; desk JS (`*.js` next to a DocType, jQuery and vanilla JS, often with a JS controller class) and Vue frontends (`apps/<app>/frontend/src`, `helpdesk/desk/src`) run in the browser. Most behaviour is wired by name: dotted strings in `hooks.py`, `frappe.call("app.module.fn")`, `frm.call("method")`, DocType names in strings. Grep finds the text. It does not resolve imports, re-exports, hook folding, or which app adds handlers to a DocType. `frappe-ast` does.

**Trace before you edit.** Before you change or debug server code, a DocType or a client call, run the `frappe-ast` query for it. Read files only after the query tells you which lines matter.

## frappe-ast

```sh
frappe-ast <command> [args] [--json] [--limit N]
# not on PATH, from the bench root (AGENTS.md links into the frappe-ast clone):
# uv run --project "$(dirname "$(dirname "$(readlink AGENTS.md)")")" frappe-ast ...
```

The tool finds the bench by walking up from the cwd. Each query first refreshes the index for changed files (under a second). The first run builds the full index in about 10 s. Paths in the output are relative to the bench root, as `path:line`.

| Question | Command |
|---|---|
| What is in this file? (read this before a file over 300 lines) | `outline <file> [--calls]` |
| Everything about a DocType | `doctype "Sales Invoice" [--fields]` |
| A desk form: the scripts desk loads (every app), the JS controller class chain, and what runs for an event or field change, in run order | `form "Sales Invoice"` · `form "Sales Invoice" item_code` |
| A standard Report, Page, Web Form, Print Format, Number Card, Dashboard Chart, Workspace or Notification: its JSON, the .py / .js beside it, and who links to it | `record "General Ledger"` · `record permission-manager --kind page` |
| Who defines, exposes, overrides, hooks or calls this method | `method erpnext.x.y.fn` |
| Where is a symbol defined | `find make_gl_entries` · `find 'get_*_details' --lang python` |
| Python callers / callees | `callers <dotted>` · `callees <qualname>` |
| Who references a DocType, field, component, form event, realtime event or import | `refs "Item"` · `refs "CRM Task.due_date" --kind field` · `refs "Sales Invoice:customer" --kind form_event` · `refs list_update --kind realtime` · `refs "frappe.api.ApiVersion" --kind import_name` (Python: absolute name; JS: exported name) |
| HTTP API surface | `endpoints --app crm [--guest]` |
| A Vue component: the definition (app or frappe-ui copy), its props, emits and slots, who renders it | `component Dialog --app crm` · `component text-editor` |
| Apps, versions, `required_apps` (missing ones too), frontends and their frappe-ui version | `apps` |
| Hook entries across all apps (folded statically) | `hooks --key doc_events --match "Sales Invoice"` |
| What a diff touches, and who depends on it | `impact --base origin/develop --apps erpnext` |
| Broken wiring and risky patterns | `check [path]` · `check --list-rules` |
| Anything else | `sql "select ... from refs where ..."` (tables: files symbols calls refs findings hooks doctypes fields; records are symbols with `f.lang = 'record'`) |

Use `--json` when you filter the output with code. Read the text output yourself.

## Workflows

**Orient on a DocType.** Run `doctype <Name>`. You are oriented when you can name: the controller class and its bases, the lifecycle methods it implements, every `doc_events` handler from every app (including `"*"`), `override_doctype_class` / `extend_doctype_class`, the form JS events, and the DocTypes that link to it or embed it as a child table. `definition.flags` holds the DocType settings (naming, `search_fields`, `title_field`, `track_changes`, ...). `permissions` lists the JSON permission rows (role, permlevel, `if_owner`, rights), and `schema.permlevels` names the fields behind each permlevel above 0. `connections`, `actions` and `states` come from the JSON as well. Add `--fields` when you need `depends_on`, `mandatory_depends_on`, `fetch_from`, `link_filters`, defaults or a field's permlevel.

**Change a server function or method.** Run `method <dotted>` first. Account for each row in `python_callers`, `maybe_callers`, `string_refs` (JS, Vue, `/api/method/` URLs, `frappe.enqueue`), `constant_calls` (`call(API.get)` with the path in a constant of another file), `client_calls` (`frm.call`, `frappe.call({doc})`, frappe-ui `useDoc` / `useDoctype` / `createListResource` handles / v2 URLs, explicit `run_doc_method`, frappe-react-sdk, and Button fields whose `options` name the method), `maybe_client_calls` and `hooks` before you change the signature or the behaviour. When `overridden_by` is not empty, another app replaces this endpoint, so your change may never run. `patched_by` lists code that replaces the function at run time: a monkey patch (`module.f = g`, `setattr(Class, "m", g)`, a wrapper such as frappe's recorder on `Document.run_method`, also through `frappe.db`), or `regional_overrides` (erpnext `@allow_regional`: the hook's function runs for companies in that country). Keep the signature compatible with each replacement, and read it: your change may not run where it is patched. `patches` on the replacement lists what it replaces. Callers that name a re-export path of the function (`from .api import run` in a package `__init__`) count too. Paths that code builds are resolved when each part is a literal, a constant, a local variable or a `this.x` set once in the file (`${METHOD}.x`, `prefix + method`), and a function that passes its parameter through as the method (`api("get_x")`) gives a ref at each call site with a literal.

**Debug "the button does nothing" or "method not found".** Run `check <form js or vue file> --rule FX202,FX203,FX204,FX207`, then `method` on the target.

**Debug a desk form ("changing this field does nothing", "the value gets overwritten").** Run `form <DocType> <fieldname or event>`. `runs` lists every handler in the order desk calls them, across apps, with `path:line`. A `[Child.field, table t]` prefix means the event fires on a child-table row. `cscript.x` comes from the JS controller class in `controller`, and the most derived definition wins. You have traced the event when you have read each `runs` line. Before you add a handler, check whether one of them already sets the value.

**Rename or remove a DocType field.** Run `doctype <Name>`: `client_fields` lists the fields that frontends name next to that DocType (`fields`, `filters`, `fieldname`, `frappe.client.insert` docs), and `custom_fields` lists the ones apps add. Run `refs "<DocType>.<field>" --kind field` for each field you touch; it also covers records such as reports, cards and web forms. `form <DocType>` (`fields_used`) shows desk form uses. Python uses of the field are not field refs, so also grep for the fieldname in the app's Python. You are done when every listed ref is updated and `check apps/<app> --rule FX207` reports no finding that names the old fieldname.

**Debug "this value changed on save" or "save is slow".** Run `doctype <Name>`. For each lifecycle method, Frappe runs the controller method first, then the `doc_events` handlers for that DocType, then the `"*"` handlers. Handlers from different apps run in the site's installed-app order. Then run `callees <Controller.validate>` and follow the calls.

**Finish a change.** Run `impact --base <ref>`. This covers uncommitted work against `HEAD` by default. Untracked files are not in the diff. Then run `check <each changed path>`. You are done when every caller, client call and hook that `impact` lists is either updated or confirmed unaffected, and `check` reports no new errors or warnings in the files you touched.

## When frappe-ast is wrong: report the miss

A **miss** is any time the tool's answer disagrees with the code. Every miss you report is how the tool gets fixed, so report each one. These are misses:

- A query leaves out something that the code has: a caller, a hook, a client call, a field ref or a definition. Typically you found it with grep or by reading.
- A query or `check` finding claims something that the code contradicts. An example is an FX error on a method that exists and is whitelisted.
- A command crashes, prints a traceback, or `status` counts files under `with_errors`. To list them, run `sql "select path, error from files where error != ''"`.
- The code uses a Frappe or frappe-ui pattern that no query covers, for example a new way to call a server method.

A gap that "What static analysis cannot see" (below) already lists is not a miss.

**When you find a miss, open an issue** on `diptanilsaha/frappe-ast` with `gh`:

1. Confirm it against the source. Read the lines that prove the tool is wrong, and keep their `path:line`.
2. Search for an issue about the same pattern, open or closed:
   ```sh
   gh issue list -R diptanilsaha/frappe-ast --label frappe-ast,miss --state all --search "<pattern words>"
   ```
   If an open issue covers it, add your case with `gh issue comment <n> --body-file <file>` and go to step 4. If a closed one covers it, the fix did not hold. Open a new issue that links the closed one.
3. Write the body to a temporary file from this template, then open the issue:
   ```markdown
   - bench: <bench folder name>   analyzer: <`analyzer` line from `frappe-ast status`>
   - command: `frappe-ast ...` (exactly as you ran it)
   - kind: missing result | wrong result | crash | unsupported pattern
   ## Got
   <the relevant output lines, trimmed>
   ## Expected
   <what the tool should have said>
   ## Evidence
   <bench-relative path:line of the code that proves it, with the 1-5 lines that matter>
   ```
   ```sh
   gh issue create -R diptanilsaha/frappe-ast --label frappe-ast,miss \
     --title "frappe-ast: <one line: what the tool got wrong>" --body-file <file>
   ```
   Write it as a plain bug report. Use bench-relative paths (`apps/crm/...`), not home-directory paths, and add no tool or model attribution.
4. Continue your task. For the rest of the task, take the answer from the code you read, and treat the tool's output as incomplete for that pattern.

You have reported the miss when the issue's Got, Expected and Evidence are enough for someone to reproduce it without your conversation. Put the issue URL in your final answer to the user. If `gh` is not installed or not logged in, put the filled-in template in your final answer instead, so that the user can open the issue.

## Frappe facts the code does not show

- Desk loads a form's scripts in this order (`frappe/desk/form/meta.py`): `<name>.js`, then `regional/<country>.js` (only when it matches the System Settings country), `<name>_list.js`, `_kanban`, `_calendar` and `_tree`, then each app's `doctype_js` hook files. No other JS in the DocType folder loads by itself. Client Scripts from the database run as well.
- For one form event, desk calls these handlers in order (`script_manager.js`): the `frappe.ui.form.on("<DocType>")` handlers in load order, the `frappe.ui.form.on("*")` handlers, `frm.cscript[event]`, and `frm.cscript["custom_" + event]`. `frm.cscript` is the JS controller class that `cur_frm.script_manager.make(X)` or `extend_cscript(cur_frm.cscript, new X({frm}))` installs, for example `erpnext.selling.QuotationController`, which extends `SellingController`, which extends `TransactionController`. A child-table field change runs `frappe.ui.form.on("<Child DocType>")[field]` and the parent form's `cscript[field]`. In child handlers, `frm` is still the parent form.
- `cur_frm` is whichever form is open now. After `frappe.new_doc(...)` or a route change, it is another form. In new code, use the `frm` argument.
- `frm.add_fetch(link, source, target)` without a fourth argument applies to every DocType on the form (`"*"`), including child tables.
- A standard Report is three files that must agree: `<name>.json` (type, `ref_doctype`, roles), `<name>.py` with a module-level `execute(filters)`, and `<name>.js`, which registers `frappe.query_reports["<exact report name>"]`. A Page registers `frappe.pages["<page_name>"]`. `check --rule FX208` finds a mismatch.
- `frappe.publish_realtime("event")` in Python reaches `frappe.realtime.on("event")` in JS only when the names match exactly. `refs <event> --kind realtime` lists both sides.
- `frm.call("x")`, `frm.call({doc: frm.doc, method: "x"})` and `frappe.call({doc: frm.doc, method: "x"})` (desk sends `run_doc_method`) call the whitelisted **controller method** `x`. `frm.call({method: "x"})` without `doc` calls the module-level function `<controller module>.x`. A dotted method is called as given.
- erpnext writes form code once in `public/js` and installs it on many forms. `form` and `method` follow each way: a JS controller class (`SalesInvoiceController` extends `SellingController` extends `TransactionController`), a class that a form creates with `new erpnext.utils.CRMNotes({frm})`, a setup function that a form calls with its DocType (`erpnext.accounts.taxes.setup_tax_validations("Sales Invoice")` runs `frappe.ui.form.on(doctype, {...})`), `frappe.ui.form.on(this.frm.doctype, "field", fn)` and `this.frm.doctype + " Item"` inside a class, and a helper that forms call (`hrms.fetch_geolocation(frm)`). Each `frm.call` in that code counts for every form it runs on, and the method is looked up on each form's controller and its base classes. `form` lists the handlers this code registers with their `public/js` path.
- A string that is a whole `/api/method/<dotted>` URL, with or without a query string (`open_url_post`, `window.open`, an `href`), names that method. `method` lists it in `string_refs` with `url` as via. FX202 does not check these URLs, because code can build a path from a URL prefix.
- `createResource({url: "app.api.fn"})` (frappe-ui) expands a dotted path. `useCall({url})` uses the URL as given, so it needs `/api/v2/method/app.api.fn`. `createListResource`, `useList` and `useDoc` accept `url` too, which replaces the default list or document method.
- frappe-ui runs DocType code in two ways. **Controller methods** (`@frappe.whitelist()` on the class): `useDoc({methods: {k: "m"}})`, `createDocumentResource({whitelistedMethods})`, `useDoctype(X).runDocMethod.submit({name, method})`, and `/api/v2/document/X/<name>/method/m`. **Module functions** in the controller file: `useDoctype(X).runMethod.submit({method})` and `/api/v2/method/X/m`. The index records both as `<DocType>:<method>`, and FX204 checks both.
- frappe-ui is not one library. Each frontend builds against its own `node_modules/frappe-ui`, and the versions differ (on maintain-bench: crm `1.0.0-rc.2`, lms and helpdesk `1.0.0-rc.1`, gameplan `1.0.2`). Before you use or debug a frappe-ui API, run `apps` or `component <Name> --app <app>`, and read the source of *that* copy. The index holds each distinct copy under the label `frappe-ui@<version>`. A `~<hash>` suffix means a copy that has the same version but different files, for example a git build or a patched `node_modules`.
- `apps` marks a frontend `STALE` when `node_modules` does not match the version that `package.json` pins. The code then runs against the older copy until someone runs `yarn install`.
- `frappe.get_all` ignores user permissions; `frappe.get_list` applies them. `frappe.db.set_value` and `frappe.db.sql` skip permissions, validations and controller hooks. `doc.save()` and `doc.insert()` check permissions unless `ignore_permissions` is set.
- The request commits when it succeeds. `frappe.db.commit()` in an endpoint or a lifecycle method commits partial work that a later error cannot roll back. Inside `doc_events` handlers, transaction control is disabled.
- On whitelisted functions, type annotations are enforced at request time (`validate_argument_types`). A wrong annotation rejects valid calls.
- The server ignores `depends_on`, `mandatory_depends_on` and `read_only_depends_on`. Only the desk UI evaluates them.
- Dotted strings in `hooks.py` fail only at runtime, when the hook fires. `check --rule FX201` finds the dead ones.
- `extend_doctype_class` exists only in v16 and later. Version per bench: `bench15` is v15 (Python 3.13); `bench16` is v16 (Python 3.14); `maintain-bench` is develop / 17.0.0-dev (Python 3.14) with the Vue apps crm, gameplan, helpdesk and lms. Check the version before you use an API from another bench.

## What static analysis cannot see

- A `frappe.call({doc})` whose doc is not the form's (`doc: connected_app`, a `.then((doc) => ...)` callback) runs `x` on that doc's DocType. When a local `frappe.get_doc("X")` names it, the call counts for `X`. Otherwise `method` lists it in `maybe_client_calls` for every whitelisted method of that name.
- The POS page (`selling/page/point_of_sale`) builds its form with `new frappe.ui.form.Form(<doctype from the POS Profile>)`, so calls on its `this.frm` are `maybe_client_calls`. Other code that takes any `frm` and that no form calls is the same.
- `/api/method/` URLs in `.html` templates are not indexed, and a URL built from a value only known at run time (`"/api/method/" + opts.method`) names no method.
- A wrapper defined in another file (`call_format(name, "get_version_fields")` with `call_format` imported) and a method name passed as an option to shared code (`balances_method: "get_supplier_payables"`) are not followed.
- JS kept inside Python strings (crm `erpnext_crm_settings.py`) and method names in config that another renderer calls (Notification `preview_fields: [{method}]`) are not indexed.
- Monkey patches on objects other than modules, classes and `frappe.db` / `frappe.cache` (`doc.validate = ...` on one instance), and patches whose replacement is not a function name (`f = make_wrapper(); module.x = f`), are not followed.
- When frappe-ast runs on Python 3.11 against v16 code, an f-string that only 3.12+ parses (quotes of its own kind inside `{}`) reads as an empty string: calls and paths inside it are not indexed. Run frappe-ast on 3.14 for v16 benches to avoid this.
- Built frontends committed under `public/<ui>/assets` or `public/app_builds` are not indexed: read the source frontend.
- Frontends without `node_modules` (`apps` reports "not installed") have no frappe-ui source in the index. Their frappe-ui calls are still traced; definitions are not.
- Apps in `required_apps` that are not in the bench (FX206): calls into them are not checked.
- Site data: Custom Fields, Property Setters, Client Scripts, Server Scripts and Workflows live in the database. Query them with `frappectl` (for example `frappectl doc list "Custom Field" -f dt="Sales Invoice"`). The index does know the custom fields an app ships, in `create_custom_fields(...)` and `fixtures/custom_field.json`. An FX205 or FX207 "no field" finding can still be a Custom Field that was made on the site.
- Permissions changed on the site (Role Permissions Manager writes Custom DocPerm rows, which replace the JSON rows), User Permissions, and `has_permission` / `permission_query_conditions` results. `doctype` shows only the rows the JSON ships. Connections that a `<name>_dashboard.py` returns are code, not JSON rows: read that file.
- Desk fields that a script creates at run time (`make_control`, dialog fields) or assigns (`frm.doc.x = ...`) count as defined only within the same file. FX207 skips `cur_frm` and test files.
- DocTypes in variables: `call("frappe.client.set_value", {doctype: props.doctype, ...})` has no DocType to record. Generic list and form views (as in crm) read fields this way.
- Dynamic dispatch: `frappe.get_attr(var)`, `getattr(doc, name)`, DocType names in variables, and `doc.method()` on a doc of unknown type. `callers` lists these calls as `unresolved_same_name`, and `method` lists them as `maybe_callers`. Treat them as candidates and confirm each one by reading it.
- Findings are heuristics with stable ids. Before you act on FA1xx findings, read the code they point to. FX20x errors are rarely false positives. When one is a false positive, it is a miss: report it.
