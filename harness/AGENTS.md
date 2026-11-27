# Frappe bench: agent guide

This bench holds Frappe apps under `apps/<app>/`. Python runs on the server; desk JS (next to each DocType, often with a JS controller class) and Vue frontends (`apps/<app>/frontend/src`) run in the browser. Most behaviour is wired by name: dotted strings in `hooks.py`, `frappe.call("app.module.fn")`, `frm.call("method")`, DocType names in strings. Grep finds the text; it does not resolve imports, re-exports, hook folding, or which app adds handlers to a DocType. `frappe-ast` does.

**Trace before you edit.** Before you change or debug server code, a DocType or a client call, run the `frappe-ast` query for it, and read files only once the query has told you which lines matter.

## frappe-ast

```sh
frappe-ast <command> [args] [--json] [--limit N]
# not on PATH, from the bench root (AGENTS.md links into the frappe-ast clone):
# uv run --project "$(dirname "$(dirname "$(readlink AGENTS.md)")")" frappe-ast ...
```

It finds the bench from the current directory and refreshes the index for changed files before each query (under a second; the first run indexes the whole bench in up to a minute). Output is one fact per line, `path:line` first, relative to the bench root; a `refs` row that stands for several uses in one function lists each line (`path:361,374,375`). `--json` adds `col`, the 1-based column. Read the text output yourself; use `--json` when code filters it.

| Question | Command |
|---|---|
| What is in this file? (before you read one over 300 lines) | `outline <file> [--calls]` |
| Everything about a DocType | `doctype "Sales Invoice" [--fields]` |
| A desk form: its scripts from every app, the JS controller chain, what runs for an event or field, in run order | `form "Sales Invoice"` · `form "Sales Invoice" item_code` |
| A standard Report, Page, Web Form, Print Format, Number Card, ...: its JSON, the code beside it, who links to it | `record "General Ledger"` · `record permission-manager --kind page` |
| Who defines, exposes, overrides, hooks or calls this method | `method erpnext.x.y.fn` |
| Where a symbol is defined | `find make_gl_entries` · `find 'get_*_details' --lang python` |
| Python callers / callees | `callers <dotted>` · `callees <qualname>` |
| Everything a function makes run: calls, inherited, overriding and `super()` methods, `with` blocks, `@property` access, document lifecycle, `doc_events`, hooks, functions passed to `enqueue` | `edges <qualname> [--kind lifecycle]` · `paths <from> <to>` |
| What makes a function run (a request, website page, REST route, document save, hook, job), and by which route | `reach <qualname>` · `surface --kind route` · `surface --reached` |
| Where a function's parameters and request values go: which call, which argument, unchanged or through what | `flow <qualname> [--follow]` · `flow --to frappe.get_doc --source form_dict --direct` |
| Who references a DocType, field, component, form event, realtime event or import | `refs "Item"` · `refs "CRM Task.due_date" --kind field` · `refs "Sales Invoice:customer" --kind form_event` · `refs list_update --kind realtime` · `refs "frappe.api.ApiVersion" --kind import_name` |
| Whitelisted methods | `endpoints --app crm [--guest]` |
| A Vue component: its definition (app or frappe-ui copy), props, emits, slots, who renders it | `component Dialog --app crm` · `component text-editor` |
| Apps, versions, `required_apps` (missing ones too), frontends and their frappe-ui copy | `apps` |
| Hook entries across all apps, evaluated | `hooks --key doc_events --match "Sales Invoice"` |
| What a diff touches, and who depends on it | `impact --base origin/develop --apps erpnext` |
| Broken wiring | `check [path]` · `check --list-rules` |
| Anything else | `sql "select ... from refs where ..."`; tables: files symbols calls refs flows findings hooks doctypes fields permissions doctype_links (records are symbols with `f.lang = 'record'`) |

Before you debug a desk form, a frappe-ui call, a report, realtime events or transactions, read `frappe.md` next to this guide (`$(dirname "$(readlink AGENTS.md)")/frappe.md`): the Frappe behaviour the code does not show.

## Workflows

**Orient on a DocType.** Run `doctype <Name>`. You are oriented when you can name the controller class and its bases, the lifecycle methods it implements, every `doc_events` handler from every app (including `"*"`), `override_doctype_class` / `extend_doctype_class`, the form JS events, and the DocTypes that link to it or embed it. Settings are in `definition.flags`, JSON permission rows in `permissions`, the fields behind each permlevel in `schema.permlevels`. Add `--fields` for `depends_on`, `fetch_from`, `link_filters`, defaults and permlevels per field.

**Change a server function or method.** Run `method <dotted>` first. Account for each row of `python_callers`, `maybe_callers`, `string_refs`, `constant_calls`, `client_calls`, `maybe_client_calls` and `hooks` before you change the signature or the behaviour; callers that name a re-export path count. When `overridden_by` is not empty, another app replaces this endpoint and your change may never run. `patched_by` lists code that runs instead of it (monkey patches, erpnext `regional_overrides`): read each one and keep the signature compatible. `patches`, on a replacement, lists what it replaces.

**Find what makes a function run.** Run `reach <qualname>`. Each row is an entry point (a whitelisted method, a REST `Rule`, the request pipeline, a `www` page, a controller lifecycle method, a hook, a scheduled job) with its shortest route, one `path:line` per step and the edge kind in brackets. No row means no entry point reaches it statically; check "What static analysis cannot see" before you call it dead code. `edges <qualname>` goes the other way.

**Trace an argument ("this value arrives wrong").** Run `flow <qualname> --follow`. Each row is a parameter or request value (`param:name`, `form_dict:limit`, `request.headers:Host`) reaching one argument of one call. An empty `via` means it arrives as the caller sent it, a dict or list included; otherwise `via` lists what it passed through, outermost first, and `through` lists the calls it crossed with the parameter it landed in (or `self.x`, when a method stored it on the instance and another method reads it). Text output prints each `through` chain once, joined by `>`, over the rows at its end; `--json` puts `through` on every row. A value reaches the rows a call returns only when the callee returns it; a `via` step named `call`, or a method of a library, is a call frappe-ast cannot see into. You have traced the value when every row ends at a framework call or a step that converts it.

**Debug "the button does nothing" or "method not found".** Run `check <form js or vue file> --rule FX202,FX203,FX204,FX207`, then `method` on the target.

**Debug a desk form ("changing this field does nothing", "the value gets overwritten").** Run `form <DocType> <fieldname or event>`. `runs` lists every handler in the order desk calls them, across apps; a `[Child.field, table t]` prefix means the event fires on a child-table row, and `cscript.x` is the most derived definition in `controller`. You have traced the event when you have read each `runs` line; check whether one of them already sets the value before you add a handler.

**Rename or remove a DocType field.** Run `doctype <Name>` (`client_fields`, `custom_fields`) and `refs "<DocType>.<field>" --kind field` for each field, which also covers reports, cards and web forms; `form <DocType>` (`fields_used`) shows desk uses. Python uses are not field refs, so also grep the app's Python for the fieldname. You are done when every listed ref is updated and `check apps/<app> --rule FX207` names no old fieldname.

**Debug "this value changed on save" or "save is slow".** Run `doctype <Name>`. For each lifecycle method Frappe runs the controller method, then that DocType's `doc_events` handlers, then the `"*"` handlers, apps in installed order. Then run `edges <Controller.validate>` and follow what each method runs.

**Finish a change.** Run `impact --base <ref>` (uncommitted work against `HEAD` by default; untracked files are not in the diff), then `check <each changed path>`. You are done when every caller, client call and hook `impact` lists is updated or confirmed unaffected, and `check` reports nothing new in the files you touched.

## When frappe-ast is wrong: report the miss

A **miss** is any answer that disagrees with the code, and reporting it is how the tool gets fixed:

- a query leaves out a caller, hook, client call, field ref or definition the code has;
- a query or `check` finding claims something the code contradicts (an FX error on a method that exists and is whitelisted);
- a command crashes, or `status` counts files under `with_errors` (`sql "select path, error from files where error != ''"`);
- the code uses a Frappe or frappe-ui pattern no query covers.

A gap already listed under "What static analysis cannot see" is not a miss.

1. **Confirm** it against the source, and keep the `path:line` of the lines that prove it.
2. **Search** for an issue about the same pattern, open or closed:
   ```sh
   gh issue list -R diptanilsaha/frappe-ast --label frappe-ast,miss --state all --search "<pattern words>"
   ```
   An open one covers it: add your case with `gh issue comment <n> --body-file <file>` and go to step 4. A closed one covers it: the fix did not hold, so open a new issue that links it.
3. **Open** the issue from this template, written to a temporary file:
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
   Write it as a plain bug report with bench-relative paths (`apps/crm/...`), and leave out tool or model attribution.
4. **Continue** your task, taking the answer from the code you read and treating the tool's output as incomplete for that pattern.

You have reported the miss when Got, Expected and Evidence let someone reproduce it without your conversation. Put the issue URL in your final answer; without a working `gh`, put the filled-in template there instead.

## What static analysis cannot see

- **Site data.** Custom Fields, Property Setters, Client Scripts, Server Scripts, Workflows, Custom DocPerm rows and User Permissions live in the database; query them with `frappectl` (`frappectl doc list "Custom Field" -f dt="Sales Invoice"`). The index knows the custom fields an app ships (`create_custom_fields`, `fixtures/custom_field.json`), so an FX205 / FX207 "no field" can still be a field made on the site. `doctype` shows only the permission rows the JSON ships.
- **Names known only at run time.** `frappe.get_attr(var)`, `getattr(doc, name)`, DocTypes in variables (`{doctype: props.doctype}`), `doc.method()` on a document of unknown type, and URLs built from run-time values (`"/api/method/" + opts.method`). `callers` lists the candidates as `unresolved_same_name` and `method` as `maybe_callers`; confirm each by reading it.
- **Forms static analysis cannot pin.** A `frappe.call({doc})` whose doc is not the form's (`doc: connected_app`, a `.then((doc) => ...)`) counts for the DocType a local `frappe.get_doc("X")` names, else lands in `maybe_client_calls`. The POS page builds its form from the POS Profile, and other code that takes any `frm` and no form calls is the same.
- **Code the index does not read.** `/api/method/` URLs in `.html` templates, JS inside Python strings, method names in config another renderer calls (Notification `preview_fields`), built frontends under `public/<ui>/assets` or `public/app_builds`, frappe-ui source for frontends without `node_modules`, and apps in `required_apps` that are not in the bench (FX206).
- **Indirection across files.** A wrapper defined in another file (`call_format(name, "x")` with `call_format` imported), a method name passed as an option to shared code (`balances_method: "x"`), monkey patches on instances (`doc.validate = ...`) or with a replacement that is not a function name, and `<name>_dashboard.py` connections (code, not JSON rows).
- **Fields made at run time.** Desk fields a script creates (`make_control`, dialogs) or assigns (`frm.doc.x = ...`) count as defined only in the same file; FX207 skips `cur_frm` and test files.
- **Call graph** (`edges`, `paths`, `reach`): a method a library calls on an object you hand it, `with` blocks and `@property` access on a receiver whose class the function does not name (`with self.lock:`, `self.conn.session`), `super()` under multiple inheritance (bases are searched depth first, not in MRO order), `self.m()` when more than five subclasses override `m` (the lifecycle edges cover `Document`), and functions in a dict outside any function. An entry point is a static fact; what runs on the way to the function is for you to read.
- **Argument flow** (`flow`): `--follow` crosses the calls `edges` resolves, not the document lifecycle, `doc_events` or hooks. A store on `self` is followed into the methods of its class and their subclasses, but not from a base class in another file; values in globals are not followed, and a value set on another object (`doc.x = v`) is not followed into other functions that read it. A library call keeps every argument in its return value, so a filter or limit passed to a query reaches the rows it returns. Line order stands in for execution order inside a function; across methods, order is not known.
- **Python version.** On Python 3.11 against v16 code, an f-string only 3.12+ parses reads as empty; run frappe-ast on 3.14 for v16 benches.
- **Findings are heuristics.** Read the code before you act on an FA1xx finding. FX20x errors are rarely wrong; when one is, it is a miss.
