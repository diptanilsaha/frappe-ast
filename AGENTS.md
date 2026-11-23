# frappe-ast: working on the tool

`frappe-ast` statically indexes a Frappe bench (Python, JS/TS, Vue, hooks.py, DocType JSON) into SQLite and answers Frappe-shaped questions. `harness/AGENTS.md` is the guide for agents that *use* the tool inside a bench. This file is for agents that change the tool.

## Data flow

```
workspace.py   Bench/App discovery, file walk, module names
libraries.py   frappe-ui copies per frontend (node_modules or submodule), one index unit per version+fingerprint
analyze.py     picks an analyzer per file (also fixtures/custom_field.json and records)  ->  model.FileFacts
records.py     <module>/<kind>/<name>/<name>.json for Report, Page, Web Form, Number Card, ... (RECORD_KINDS table)
  python/      stdlib ast: analyzer.py, checks.py (FA1xx), hooks.py (static evaluator), frappe_api.py (API tables),
               custom_fields.py (create_custom_fields shapes), patches.py (monkey patches), compat.py (rewrites 3.12–3.14 syntax when the running Python cannot parse it)
  js/          tree-sitter: analyzer.py (walker), nodes.py (node readers), frappe_patterns.py (API tables),
               desk.py (frm field calls, frm.call targets, JS controller classes, cscript, realtime),
               form_setup.py (shared form code: setup functions, namespaces, `new X({frm})`), ts_types.py (TS types, *Props),
               frappe_ui.py (useDoc / useDoctype / v2 URLs -> "<DocType>:<method>"), client_api.py (frappe.client.*
               DocTypes, field refs), constants.py (method paths in constants), method_values.py (built paths, wrappers that pass a method name, run_doc_method, page methods), vue.py (SFC split, props/emits/slots)
doctype_json.py DocType JSON -> doctypes (settings as flags), fields, permissions, doctype_links (connections, actions, states)
indexer.py     incremental: (mtime, size) per file; a hash of this package's source forces a full reparse
store.py       SQLite schema; bump SCHEMA_VERSION when you change a table
queries/       resolver.py (dotted paths, re-exports, base classes), method + doc_calls (the Python method a frm.call runs), patches (monkey patches, regional_overrides, FX209), contexts (which forms shared desk JS runs on), doctype + doctype_schema, impact, check (FX2xx),
               doc_checks (FX204, FX207), forms (which form a JS file serves, load order, JS class chain),
               form (`form` command), record (`record` command, FX208), lookup, outline, component + component_api (Vue components across
               apps and frappe-ui), apps (required_apps, frontends)
commands/      one Command class per subcommand (base, index, code, reports, review)  ->  render.py or --json
```

## Invariants

- The tool runs on Python 3.11+ (frappe v15 needs 3.11, v16 needs 3.14), so do not use 3.12+ syntax or APIs in `src/`. Run the tests on 3.11 too: `uv run --python 3.11 pytest -q`.
- Static only. Never import or execute app code, including hooks.py. The tool must run without a Frappe environment, against any bench version.
- Analyzers are pure functions of (path, source). Cross-file reasoning lives in `queries/`. That split is what makes incremental indexing correct.
- A Frappe API pattern is a row in a table: `python/frappe_api.py` or `js/frappe_patterns.py`. Add rows there, not `if` branches in the walkers.
- A rule gets a stable id (FA1xx per file, FX2xx cross file), a line in its module docstring (`check --list-rules` prints those docstrings), and a fixture case. A rule never changes meaning; retire the id instead.
- Patterns come from real code. Before you add one, find it in `~/projects/frappe-ui` (the library source) and in the crm, helpdesk, lms or gameplan frontends on maintain-bench, and keep the shape the apps actually write.
- Ref targets: `doc_method` / `module_method` are `x` when the DocType is the form's (frm.call; `queries/contexts.py` finds the form), `<DocType>:x` when the call names it, and `?:x` for a doc of a DocType the code does not name. `setup_event` is `<tag>:<event>` for frappe.ui.form.on with a DocType that is not a literal (`@self`, `@self Item`, or `$` for the setup function's first parameter), `setup_call` is a DocType literal passed to an app function (via is the function), and `js_new` is a class an app creates with `new`. `field` is `<DocType>.<field>`. `custom_field` has the field as target and the DocType as `via`. `method_const` is a constant's name, matched to `const` symbols in the same app. `form_field` is `field` or `table.field` (`*.field` for add_fetch) with no DocType: queries resolve the form from the file (`queries/forms.py`). `script_field` is a name the file defines itself. `form_controller` is the JS class a form installs as `frm.cscript`, and `cscript_event` is a handler assigned to `frm.cscript`. `realtime` carries `publish` (Python) or `listen` (JS) as access. `record_link` is `<kind>:<name>`. `doctype_handle` is the DocType a frappe-ui handle (`useDoctype`, `createListResource`) binds, with the variable as via. `monkey_patch` (Python) has the patched dotted path as target and the replacement as via (`<lambda>`, or `<function>.<name>` for a wrapper the patching function defines); queries count it only when the replacement is a function. `handle_method` is `<variable>:<method>` for `<variable>.runDocMethod/runMethod.submit` on a handle from another file (via is the runner): queries match it to `doctype_handle` refs in the same app.
- Desk semantics come from frappe's own code: `frappe/desk/form/meta.py` (script load order) and `frappe/public/js/frappe/form/script_manager.js` (handler order). Read them on the bench before you change `queries/forms.py` or `queries/form.py`.
- Library units (`frappe-ui@...`) are indexed for lookup, never checked, and their tests are not indexed.
- Output is for agents: one line per fact, `path:line` first, no empty sections. Every query must stay useful at `--limit 5`.

## Change workflow

1. Reproduce on a real bench first: `uv run frappe-ast <cmd> --bench ~/benches/bench16` (also bench15 and maintain-bench; see `harness/AGENTS.md` for the versions).
2. Add the case to the synthetic bench in `tests/bench` (real app files: `demo`, and `demo_fr` for one app that patches another). Add an assertion to the test module of that area: `test_python.py` or `test_js.py` for analyzer facts (use the `analyze` fixture), `test_method.py`, `test_forms.py` or `test_doctypes.py` for queries, `test_checks.py` for an FX rule, `test_index.py` for indexing. Watch it fail.
3. Make the change. Done when all of these are clean (run them from the repository root):
   ```sh
   uv run pytest -q
   uvx ruff check src tests && uvx ruff format --check src tests
   uv run frappe-ast check --bench ~/benches/bench16 --rule FX201,FX202,FX203,FX204,FX207,FX208
   ```
   As of 2026-11-23, FX201–FX206, FX208 and FX209 report only true positives (FX209 reports nothing on the three benches): 3 FX201 in erpnext hooks.py (`get_print_settings`, `FindItemBot`, `regional.france.utils.test_method`) and 1 FX202 (`frappe.www.list.get`). On maintain-bench there is also FX204 for the GP Invitation `accept_invitation` button and for disclose `call_action(frm, "fetch")` (5: `frm.call({method})` without `doc` runs a module function, and these are class methods), and FX206 for helpdesk (`telephony`) and lms (`payments`). FX207 reports 52 on bench16, 40 on bench15 and 51 on maintain-bench. A sample of them was read, and all were true positives: dead references to removed fields (`modules_access`, `sb1`, `insert_code`) and real bugs (`setup_wizard.js` System Settings `timezone`, erpnext `budget.js` `fiscal_year`, `appointment.js` `lead`, the helpdesk SLA `document_type`, crm `assignment_rule_name`). Too many to list, so diff them: save `check --json` before your change and compare it after. Each new finding is a false positive until you have read the code. A new FX finding on a real bench is a false positive until you have read the code and confirmed it.

## Feedback loop

Agents that use the tool inside a bench report **misses** as GitHub issues on `diptanilsaha/frappe-ast`, with the labels `frappe-ast` and `miss`. `harness/AGENTS.md` ("When frappe-ast is wrong") defines a miss and holds the issue template. When you start work on this tool, list the open misses first, and triage each one, oldest first:

```sh
gh issue list --label frappe-ast,miss --state open
gh issue view <n> --comments        # later comments can add cases of the same miss
```

1. **Reproduce.** Run the issue's exact command against its bench (`~/benches/<bench>`). If the `analyzer` hash in `status` differs from the issue's, the miss may already be fixed: rerun to see if it still happens.
2. **Locate the cause.** Compare the code under Evidence with what the index holds (`outline <file>`, `sql "select * from refs where file_id = (select id from files where path = '...')"`). The layer is then plain:
   - The facts are missing or wrong: an **analyzer** bug (`python/`, `js/`). It is also an analyzer bug when the pattern is new; then add a table row in `frappe_api.py` or `frappe_patterns.py`.
   - The facts are right but the answer is wrong: a **query** bug (`queries/`).
   - The answer is right but the agent misread it: the harness doc needs a fix (`harness/AGENTS.md`).
   - Static analysis cannot know the answer: a **limit**. Add it to "What static analysis cannot see" in `harness/AGENTS.md`, so that agents stop reporting it.
3. **Fix** it through the change workflow above. Put the issue's Evidence into `tests/bench` in its smallest form, and watch the test fail before you fix it. Put `(#<n>)` at the end of the commit subject.
4. **Close** it with a resolution comment: the layer, a one-line cause, and the test name (or the doc line, for a limit).
   ```sh
   gh issue close <n> --comment "<layer>: <cause>. Test: <test name>."
   ```
   Close a limit with `--reason "not planned"`.

An issue is closed when its command gives the Expected output, or the limit is documented, and the change workflow checks are clean. An issue you cannot reproduce stays open. Comment on it with what you ran and what you saw, and add the `question` label.

When an issue shows a Frappe pattern, look for every other shape of that pattern before you close it. Search `~/projects/frappe-ui` and the frontends on maintain-bench. Fix the whole class, so the same miss does not come back in another form.

## Style

Tabs, Frappe-style. Files stay under 300 lines; split by responsibility. Classes for anything with state, public methods first, helpers at the bottom. Comments explain why something is surprising, in one line.
