# frappe-ast: working on the tool

`frappe-ast` statically indexes a Frappe bench (Python, JS/TS, Vue, `hooks.py`, DocType JSON) into SQLite and answers Frappe-shaped questions from the facts it holds. This file is for agents that change the tool; `harness/AGENTS.md` is for agents that use it inside a bench.

## Layers

Analyzers extract **facts** from one file; queries join facts across files and apps. Each module's docstring says what it covers.

```
workspace.py, libraries.py   find the bench, its apps, files and frappe-ui copies
analyze.py                   picks the analyzer per file  ->  model.FileFacts
  python/                    stdlib ast; Frappe API patterns are rows in frappe_api.py
  js/                        tree-sitter; Frappe API patterns are rows in frappe_patterns.py
  doctype_json.py, records.py   DocType JSON and standard records
indexer.py, store.py         incremental refresh; the SQLite schema (SCHEMA_VERSION)
queries/                     resolver, callgraph, surface, flow, method, doctype, forms, check, impact, ...
commands/                    one Command class per subcommand  ->  render.py or --json
```

[docs/how-it-works.md](docs/how-it-works.md) follows a command from refresh to output, with a diagram per command.

## Invariants

- **Static only.** The tool reads source and never imports or executes app code, `hooks.py` included, so it runs without a Frappe environment against any bench version.
- **Python 3.11 syntax and APIs in `src/`.** frappe v15 runs on 3.11, v16 on 3.14, and the tool installs next to either.
- **Analyzers are pure functions of (path, source).** Anything that needs a second file lives in `queries/`. That split is what makes incremental indexing correct.
- **A Frappe API pattern is a table row** in `python/frappe_api.py` or `js/frappe_patterns.py`, rather than a branch in a walker.
- **Patterns come from real code.** Find the shape in `~/projects/frappe-ui` and in the crm, helpdesk, lms or gameplan frontends on maintain-bench before you add it, and keep the shape the apps write.
- **Facts, not judgements.** A row says what the code does; what that means for a reader is the reader's call.
- **Edges come only from what the code names**: a resolved call, a DocType literal, a hook key, a declared type. A missing edge hides a route; a name-matched edge would invent one. Each edge kind has a line in `queries/callgraph.py`'s docstring and each entry-point kind in `queries/surface.py`'s.
- **Row formats are documented** in [docs/facts.md](docs/facts.md). A change to a ref kind, a `calls` column or `flows` updates that page in the same commit.
- **A rule keeps its meaning.** It gets a stable id (FA1xx per file, FX2xx cross file), a line in its module's docstring (`check --list-rules` prints them) and a fixture case; to change what it means, retire the id.
- **Desk order comes from frappe's own code**: `frappe/desk/form/meta.py` (script load order) and `frappe/public/js/frappe/form/script_manager.js` (handler order). Read them on the bench before you change `queries/forms.py` or `queries/form.py`.
- **Library units** (`frappe-ui@...`) are indexed for lookup only: never checked, and their tests are skipped.
- **Output is for agents**: one line per fact, `path:line` first, no empty sections, useful at `--limit 5`. When a change alters what a command prints, rerun the samples that `docs/` shows for it.

## Change workflow

1. **Reproduce on a real bench**: `uv run frappe-ast <cmd> --bench ~/benches/bench16` (bench15 and maintain-bench too; `harness/AGENTS.md` lists their versions). Save `uv run frappe-ast check --json --bench ~/benches/bench16 > before.json` now, for step 3.
2. **Make it fail in `tests/bench`**, the synthetic bench (`demo`, and `demo_fr` for an app that patches another). Put the case in its smallest real form and the assertion in the module for that layer:

   | Layer | Test module |
   |---|---|
   | Python / JS analyzer facts (the `analyze` fixture) | `test_python.py`, `test_js.py` |
   | Queries | `test_method.py`, `test_forms.py`, `test_doctypes.py`, `test_flow.py` |
   | Call graph, entry points (one case per kind) | `test_callgraph.py`, `test_surface.py` |
   | FX rules | `test_checks.py` |
   | Indexing | `test_index.py` |

   To assert on a line, tag it in the fixture with a trailing `# @tag` and look it up with `support.tagged_lines`, so fixture edits never shift an assertion. Watch the test fail.
3. **Make the change.** Done when all of these are clean, run from the repository root:
   ```sh
   uv run pytest -q && uv run --python 3.11 --isolated pytest -q
   uvx ruff check src tests && uvx ruff format --check src tests
   uv run frappe-ast check --json --bench ~/benches/bench16 > after.json   # diff against before.json
   ```
   Each finding in `after.json` that is not in `before.json` is a false positive until you have read the code it points to and confirmed it.

## Feedback loop

Agents that use the tool report **misses** as GitHub issues on `diptanilsaha/frappe-ast`, labelled `frappe-ast` and `miss`; `harness/AGENTS.md` ("When frappe-ast is wrong") defines a miss and holds the template. Start each session on this tool by triaging the open misses, oldest first:

```sh
gh issue list --label frappe-ast,miss --state open
gh issue view <n> --comments        # later comments can add cases of the same miss
```

1. **Reproduce.** Run the issue's exact command against its bench. If the `analyzer` hash in `status` differs from the issue's, the miss may already be fixed.
2. **Locate the layer.** Compare the code under Evidence with what the index holds (`outline <file>`, `sql "select * from refs where file_id = (select id from files where path = '...')"`):
   - facts missing or wrong, or a new pattern: **analyzer** (`python/`, `js/`; a new pattern is a table row);
   - facts right, answer wrong: **query** (`queries/`);
   - answer right, agent misread it: **harness** (`harness/AGENTS.md`);
   - unknowable statically: **limit**; add it to "What static analysis cannot see" in `harness/AGENTS.md`.
3. **Fix** through the change workflow, with the issue's Evidence as the fixture case. End the commit subject with `(#<n>)`.
4. **Generalise.** Search `~/projects/frappe-ui` and the maintain-bench frontends for every other shape of the same pattern, and fix the class.
5. **Close** with the layer, a one-line cause and the test name (or the doc line, for a limit): `gh issue close <n> --comment "<layer>: <cause>. Test: <test name>."`, adding `--reason "not planned"` for a limit.

An issue is closed when its command gives the Expected output (or the limit is documented) and the change workflow is clean. An issue you cannot reproduce stays open: comment with what you ran and saw, and add the `question` label.

## Style

Tabs, Frappe-style. Files under 300 lines, split by responsibility. Classes for anything with state, public methods first, helpers at the bottom. A comment explains, in one line, why something is surprising.
