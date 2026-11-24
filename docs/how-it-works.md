# How frappe-ast works

frappe-ast reads a bench once into a SQLite index of facts, then answers each command by
querying those facts. This page follows that path: how the index is built, what it holds, and
the flow behind each command. For sample output, see the [call graph](call-graph.md) and
[argument flow](argument-flow.md) guides.

```mermaid
flowchart LR
  subgraph bench["bench/apps/*"]
    py[".py"]
    js[".js .ts .vue"]
    hk["hooks.py"]
    js2["DocType and record JSON"]
  end
  subgraph index["SQLite index"]
    facts["files · symbols · calls · refs · flows · findings"]
    meta["hooks · doctypes · fields · permissions · doctype_links · meta"]
  end
  py -->|"Python analyzer (ast)"| facts
  js -->|"JS / Vue analyzer (tree-sitter)"| facts
  hk -->|"hooks evaluator"| meta
  js2 -->|"DocType and record readers"| meta
  js2 --> facts
  index --> q["query: resolver · call graph · forms"] --> out["text, one fact per line, or --json"]
```

## 1. Finding the bench

Every command starts by finding the bench: `--bench`, or the nearest folder above the current
directory that holds both `apps/` and `sites/`. Each app is a folder under `apps/` with a
`hooks.py`. The index lives at `~/.cache/frappe-ast/<bench>-<hash>.sqlite` unless `--db` names
another file.

## 2. Building and refreshing the index

Every command except `outline`, `status` and `index` itself refreshes the index before it
answers; `--no-refresh` skips that.

```mermaid
flowchart TD
  start["command starts"] --> fresh{"--no-refresh?"}
  fresh -->|yes| query["answer from the index as it is"]
  fresh -->|no| hash{"tool source hash<br/>same as the index's?"}
  hash -->|no| clear["forget every file:<br/>all will be parsed again"] --> plan
  hash -->|yes| plan["walk every app and frappe-ui copy,<br/>compare each file's mtime and size"]
  plan --> todo["changed or new files"]
  plan --> gone["files and apps no longer there"] --> drop["delete their rows"]
  todo --> many{"200 files or more?"}
  many -->|yes| pool["parse in a process pool"]
  many -->|no| one["parse in this process"]
  pool --> save["replace each file's rows"]
  one --> save
  save --> appmeta["per app: evaluate hooks.py,<br/>read every DocType JSON"]
  appmeta --> commit["commit, record indexed_at"] --> query
```

An analyzer reads one file and nothing else, which is what lets a refresh reparse only what
changed:

- **Python** (stdlib `ast`): symbols with signatures and decorators (`@frappe.whitelist`,
  `allow_guest`, `methods`), calls resolved through the file's imports, what each call is made
  on (an instance, a document of a named DocType), references the Frappe APIs make (DocTypes,
  dotted method paths, realtime events, custom fields, monkey patches), argument flow, and
  per-file check findings. Syntax newer than the running Python is rewritten first.
- **JS, TS, Vue** (tree-sitter): symbols, `frappe.call` / `frm.call` / frappe-ui targets, form
  events, fields named next to a DocType, components and their props, emits and slots.
- **Records:** a Report, Page, Web Form, Print Format, Number Card, ... JSON becomes one symbol,
  with references to the DocTypes and fields it names.
- **hooks.py** is evaluated without importing it: module-level names, `tuple()` / `list()`, `+`
  and subscript assignment are folded, so a key built in a loop still lists every entry.

The first run parses everything (seconds to a minute, depending on the bench); later runs
usually take under a second. A schema change (`SCHEMA_VERSION`) rebuilds the tables.

## 3. What the index holds

| Table | One row per | Filled by |
|---|---|---|
| `files` | indexed file: path, app, language, parse error | indexer |
| `symbols` | function, method, class, variable, component, record | analyzers |
| `calls` | call site: caller, callee as written, resolved target, what it is called on, first literal argument | Python and JS analyzers |
| `refs` | reference a Frappe API makes: DocType, dotted method, form event, field, import, callback, patch, ... | analyzers |
| `flows` | (call, argument, source) inside one function | Python analyzer |
| `findings` | per-file check finding | analyzers |
| `hooks` | `hooks.py` entry, with up to three keys of nesting | hooks evaluator |
| `doctypes`, `fields`, `permissions`, `doctype_links` | DocType, field, permission row, connection / action / state | DocType reader |
| `meta` | schema version, tool hash, bench path, frappe-ui copies, frontends | indexer |

Analyzers record only what one file says. Joining facts across files and apps is the job of the
queries.

## 4. What the queries share

- **Resolver.** Turns a dotted path into a symbol, following re-exports (`from .api import run`
  in a package `__init__`); finds a DocType's controller class next to its JSON; looks a method
  up on a class and its bases, including bases in other apps.
- **Call graph.** Turns `calls` rows and references into typed edges: plain calls, inherited
  and overriding methods, instances, document methods, the document lifecycle, `doc_events`,
  hook dispatch, typed module attributes, callbacks and dotted strings.
- **Form resolution.** Works out which desk form a piece of JS runs on: the DocType folder,
  `doctype_js` hooks, `frappe.ui.form.on`, and for shared code, the forms that call it or
  install its class.
- **Rendering.** Text output puts one fact per line, `path:line` first, and leaves out empty
  sections; `--limit` caps each section. `--json` prints the full result unchanged.

## 5. Command by command

### `index`

Runs the refresh in section 2 and prints how many files it parsed, removed and kept.

```mermaid
flowchart LR
  a["index [--apps a,b] [--full]"] --> full{"--full?"}
  full -->|yes| clear["forget every file"] --> refresh
  full -->|no| refresh["refresh (section 2),<br/>only --apps if given"]
  refresh --> out["parsed · removed · files · seconds"]
```

### `status`

Reads the index without refreshing it.

```mermaid
flowchart LR
  a["status"] --> meta["meta: path, indexed_at,<br/>tool hash, schema"]
  a --> files["files: counts per app,<br/>files with parse errors"]
  meta --> out["where the index is and how fresh"]
  files --> out
```

### `outline`

Parses one file straight from disk; the index is not used.

```mermaid
flowchart LR
  a["outline file [--calls]"] --> lang{"language from<br/>the extension"}
  lang --> an["that analyzer, on this file only"]
  an --> sym["symbols as a tree"]
  an --> refs["Frappe references, grouped by kind"]
  an --> find["per-file findings"]
  an -.->|"--calls"| calls["every call and its resolved target"]
```

### `find`

```mermaid
flowchart LR
  a["find pattern"] --> m{"pattern shape"}
  m -->|"name"| e["symbols.name = pattern"]
  m -->|"dotted suffix"| s["qualname ends with .pattern"]
  m -->|"glob with *"| g["name matches the glob"]
  e --> f["filter by --kind, --app, --lang"]
  s --> f
  g --> f
  f --> out["path:line kind qualname"]
```

### `callers`

```mermaid
flowchart LR
  a["callers dotted"] --> r["resolver: symbol, following re-exports"]
  r --> res["calls whose resolved target<br/>is the symbol"]
  r --> un["calls of the same method name<br/>that did not resolve"]
  res --> out["resolved"]
  un --> out2["unresolved_same_name:<br/>candidates to read"]
```

### `callees`

```mermaid
flowchart LR
  a["callees qualname"] --> c["calls where caller = qualname"] --> out["line · callee · resolved target"]
```

### `refs`

```mermaid
flowchart LR
  a["refs target [--kind k]"] --> r["refs rows naming the target:<br/>DocType, DocType.field, DocType:event,<br/>component, realtime event, import"]
  r --> out["path:line kind via scope"]
```

### `sql`

```mermaid
flowchart LR
  a["sql query"] --> ro["connection set to read-only<br/>(pragma query_only)"] --> run["run the query"] --> out["rows"]
```

### `method`

```mermaid
flowchart TD
  a["method dotted"] --> r{"resolver finds it?"}
  r -->|no| sim["similar names, overrides<br/>and hooks naming the path"]
  r -->|yes| names["every path a caller can use:<br/>the query, the qualname, re-exports"]
  names --> ov["override_whitelisted_methods → overridden_by"]
  names --> pt["monkey patches, regional_overrides → patched_by / patches"]
  names --> hk["hooks naming it → hooks"]
  names --> pc["resolved calls → python_callers"]
  names --> sr["dotted strings, /api/method/ URLs → string_refs"]
  names --> cc["calls through a constant → constant_calls"]
  r -->|yes| mc["same-name calls on unknown receivers → maybe_callers"]
  r -->|yes| dc["form resolution: frm.call, frappe.call doc,<br/>frappe-ui, Button fields → client_calls"]
  r -->|yes| ce["its own calls → callees"]
```

### `doctype`

```mermaid
flowchart TD
  a["doctype name [--fields]"] --> d{"DocType indexed?"}
  d -->|no| sim["similar names and code that names it"]
  d -->|yes| json["DocType JSON: settings, schema,<br/>permission rows, connections, actions, states"]
  d -->|yes| ctl["controller class next to the JSON, and its bases"]
  d -->|yes| hk["hooks: doc_events incl. '*',<br/>override_doctype_class, extend_doctype_class"]
  d -->|yes| fe["form events, also from shared JS"]
  d -->|yes| ln["DocTypes that link to it or embed it as a child table"]
  d -->|yes| rec["records that target it"]
  d -->|yes| cl["methods and fields client code names next to it"]
  json -.->|"--fields"| fl["depends_on, fetch_from,<br/>defaults, permlevel per field"]
```

### `form`

The order follows desk itself: `frappe/desk/form/meta.py` for scripts and
`frappe/public/js/frappe/form/script_manager.js` for handlers.

```mermaid
flowchart TD
  a["form DocType [field or event]"] --> scripts["scripts desk loads, in order:<br/>name.js · regional/country.js · _list · _kanban ·<br/>_calendar · _tree · every app's doctype_js"]
  scripts --> chain["JS controller class installed as frm.cscript,<br/>and its extends chain"]
  chain --> ev{"field or event given?"}
  ev -->|no| all["handlers per event"]
  ev -->|yes| order["handlers in call order:<br/>1. frappe.ui.form.on(DocType), load order<br/>2. frappe.ui.form.on('*')<br/>3. frm.cscript[event], most derived<br/>4. frm.cscript['custom_' + event]"]
  order --> child["child-table field: the child DocType's handlers<br/>and the parent's cscript method"]
```

### `record`

```mermaid
flowchart LR
  a["record name [--kind k]"] --> s["record symbol: kind:name"]
  s --> j["its JSON"]
  s --> code[".py · .js · .html beside it"]
  s --> t["DocTypes and fields it targets"]
  s --> l["who links to it"]
  s --> p["JSON and code that disagree<br/>(no execute, another registered name)"]
```

### `endpoints`

```mermaid
flowchart LR
  a["endpoints [--app] [--guest] [--path]"] --> s["symbols with whitelisted = 1"]
  s --> f["filter: app, allow_guest, path prefix"] --> out["path:line qualname, allow_guest, HTTP methods"]
```

### `hooks`

```mermaid
flowchart LR
  a["hooks [--app] [--key] [--match]"] --> h["hooks rows, evaluated at index time"]
  h --> f["filter: app, key, substring of<br/>DocType, event or value"] --> out["app key p1 p2 p3 → value  path:line"]
```

### `component`

```mermaid
flowchart TD
  a["component Name [--app a]"] --> n["PascalCase and kebab-case names"]
  n --> def["definitions: app components<br/>and each frappe-ui copy"]
  n --> imp["import paths that name it"]
  n --> use["where it is rendered"]
  use --> fe["for each frontend that renders it:<br/>the frappe-ui copy it builds against"]
  def --> api["props, emits, slots from its declared types"]
  a -.->|"--app"| keep["keep that app's usages<br/>and the copy it uses"]
```

### `apps`

```mermaid
flowchart LR
  a["apps"] --> v["each app: __version__"]
  a --> req["required_apps from hooks,<br/>flag the ones not in the bench"]
  req --> dep["dependents: who requires it"]
  a --> fe["frontends and their frappe-ui copy,<br/>STALE when node_modules ≠ the pin"]
```

### `edges`

[Guide](call-graph.md#edges-what-a-function-makes-run).

```mermaid
flowchart TD
  a["edges qualname [--kind] [--app]"] --> c["calls out of the function"]
  a --> r["refs out of it: callbacks, dotted strings"]
  c --> t{"how the target is known"}
  t -->|"resolved name"| cl1["call · Cls() → __init__"]
  t -->|"self.m()"| inh["inherited · override (≤ 5 subclasses)"]
  t -->|"x = Cls() then x.m()"| ins["instance"]
  t -->|"doc of a named DocType"| doc["doc_method · lifecycle · doc_event"]
  t -->|"frappe.get_hooks('key')"| hook["hook → each function under the key"]
  t -->|"typed module attribute"| prx["proxy"]
  r --> ref["callback · dispatch · dotted"]
  cl1 --> out["path:line caller → callee kind"]
  inh --> out
  ins --> out
  doc --> out
  hook --> out
  prx --> out
  ref --> out
```

### `paths`

```mermaid
flowchart LR
  a["paths from to [--depth n]"] --> g["every edge of the call graph"]
  g --> bfs["breadth-first search from 'from',<br/>up to --depth calls"]
  bfs --> out["shortest routes first,<br/>each step with its edge kind"]
```

### `surface`

[Guide](call-graph.md#surface-entry-points).

```mermaid
flowchart TD
  a["surface [--kind k] [--reached]"] --> roots["entry points with their kinds"]
  s1["@frappe.whitelist → api / guest_api"] --> roots
  s2["werkzeug Rule endpoint → route"] --> roots
  s3["frappe.app.application, request hooks → request"] --> roots
  s4["get_context in www/, pages, web forms → www"] --> roots
  s5["controller lifecycle method → doc_lifecycle"] --> roots
  s6["report execute → report"] --> roots
  s7["hooks.py values → doc_event, permission,<br/>scheduler, template, hook"] --> roots
  roots --> r{"--reached?"}
  r -->|no| out["function and its kinds"]
  r -->|yes| bfs["walk the call graph from all of them at once"] --> out2["every function reached:<br/>fewest calls from any entry point,<br/>every kind that gets there"]
```

### `reach`

```mermaid
flowchart LR
  a["reach qualname [--depth n]"] --> back["walk edges backwards<br/>from the function"]
  back --> hit{"an entry point?"}
  hit -->|yes| route["record the route, one per entry point"]
  hit -->|no| back
  route --> out["root and kinds, then each step<br/>path:line caller -kind-> callee"]
```

### `flow`

[Guide](argument-flow.md).

```mermaid
flowchart TD
  a["flow qualname [--to] [--source] [--direct] [--follow]"] --> rows["flows rows of the function<br/>(recorded at index time)"]
  rows --> f{"--follow?"}
  f -->|no| filt["filter: --to, --source, --direct"] --> out["path:line caller source callee arg via"]
  f -->|yes| bind["argument → parameter of each callee the call graph resolves<br/>(position, keyword, *args, **kwargs, self skipped)"]
  bind --> inner["that function's flows from the parameter"]
  inner --> depth{"--depth reached?"}
  depth -->|no| bind
  depth -->|yes| out2["rows with the original source,<br/>via from every function, and 'through'"]
  inner --> out2
```

How a single function's flows are recorded at index time:

```mermaid
flowchart LR
  p["parameters, form_dict,<br/>request parts"] --> env["what each local name holds"]
  env -->|"assignment: replace<br/>branch, loop: add"| env
  env --> args["each call's arguments"]
  args --> row["flows row: source, arg,<br/>via (steps, outermost first)"]
```

### `check`

```mermaid
flowchart TD
  a["check [path] [--rule ids] [--no-cross]"] --> file["per-file findings stored at index time (FA1xx)"]
  a --> cross{"--no-cross?"}
  cross -->|no| x["cross-file rules over the index (FX2xx)"]
  x --> x1["hooks.py paths that do not resolve"]
  x --> x2["client calls to missing or not whitelisted methods"]
  x --> x3["DocType methods and fields client code names but the DocType lacks"]
  x --> x4["form handlers for names that are no field or event"]
  x --> x5["required_apps not in the bench"]
  x --> x6["records whose JSON and code disagree"]
  x --> x7["patches on functions that no longer exist"]
  file --> f["filter: path, --rule"] --> out["path:line rule severity message"]
  x1 --> f
  x2 --> f
  x3 --> f
  x4 --> f
  x5 --> f
  x6 --> f
  x7 --> f
```

`--list-rules` prints each rule's id and meaning.

### `impact`

```mermaid
flowchart TD
  a["impact [--base ref] [--apps a,b]"] --> diff["git diff -U0 base in each app<br/>(uncommitted work vs HEAD by default)"]
  diff --> lines["changed lines per file"]
  lines --> sym["innermost symbol around each line"]
  sym --> exp["whitelisted? → exposure"]
  sym --> c["resolved callers, under every re-export path"]
  sym --> h["hooks naming it"]
  sym --> s["dotted strings and URLs naming it"]
  sym --> p["patches that replace it"]
```

Untracked files are not part of the diff.

## 6. What it does not do

frappe-ast never imports or runs app code, and needs no site or database. That keeps it safe to
run anywhere and on any bench version, and it is also its limit: values only known at run time,
site data (Custom Fields, Client Scripts, Property Setters, Custom DocPerm rows) and calls
through names built at run time are outside what it can see.
[`harness/AGENTS.md`](../harness/AGENTS.md#what-static-analysis-cannot-see) keeps the full list.
