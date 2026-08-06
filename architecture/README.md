# Architecture

The Helion Nova platform component tree — every server, service, and tier the
platform is planned to run, in one place.

| File | Role |
| --- | --- |
| [`universe.yaml`](./universe.yaml) | **Source of truth.** Hand-edited. |
| [`scaffold.py`](./scaffold.py) | Generator. Reads the spec, writes everything below. |
| [`page.py`](./page.py) | Renders the spec as a self-contained browsable page. |
| [`test_scaffold.py`](./test_scaffold.py) | Tests for the generator, chiefly the validator. |
| [`test_page.py`](./test_page.py) | Tests for the page generator. |
| [`ARCHITECTURE.md`](./ARCHITECTURE.md) | Generated. Full tree, per-tier tables, alias index. |
| [`universe/`](./universe/) | Generated. One directory per node, each with a `README.md`. |

## Working on it

```sh
pip install pyyaml
python3 architecture/scaffold.py          # regenerate after editing the spec
python3 architecture/scaffold.py --check  # CI: fail if output is stale
python3 -m unittest discover -s architecture -t architecture   # tests
```

Both the tests and the staleness check run in CI on any change under
`architecture/`.

## The browsable page

`page.py` renders the whole tree as one self-contained page — every component
as a chip with its status and dependency degree, plus the tier map, the
load-bearing chart, and the coupling matrix, over a filter box.

```sh
python3 architecture/page.py --standalone -o /tmp/architecture.html   # open locally
python3 architecture/page.py -o /tmp/fragment.html                    # for publishing
```

The output is **not committed** — regenerate it when you want a current view, so
it can never disagree with the spec. Every figure on the page is computed from
`universe.yaml`; none is written by hand.

The default output is a fragment with no `<html>`/`<head>`/`<body>`, because a
publishing host supplies its own skeleton and a nested document breaks it.
`--standalone` wraps it into a real document you can open in a browser.

Two constraints are load-bearing and fail *silently* rather than erroring, so
`test_page.py` asserts both: no external resources of any kind (a blocked font
would just fall back to something arbitrary), and no document wrapper on the
fragment.

Edit `universe.yaml` and regenerate. Never hand-edit `ARCHITECTURE.md` or
anything under `universe/` — the generator deletes and rewrites that tree, so
manual changes are lost on the next run.

`--check` regenerates into place and reports whether anything changed, so a
stale commit fails loudly instead of drifting.

## Spec format

```yaml
- name: Rate Limiter
  description: Per-tenant, per-key, and per-route quota enforcement with burst budgets.
  status: building               # optional; planned | building | running
  owner: platform-edge           # optional; lowercase team slug
  aliases: [Throttle Server]     # optional
  depends_on: [Cache Cluster]    # optional; other components, by name or alias
  children: []                   # optional; omit for a leaf component
```

`depends_on` turns the containment tree into a dependency graph. Targets are
matched by name **or alias**, so an edge written against an old name still
resolves. Dependencies belong on components — a tier cannot declare one.

Each component's page gets a **Depends on** / **Depended on by** section, and
`ARCHITECTURE.md` gains a dependency summary: the most depended-on components, a
tier coupling matrix, and a diagram of the strong links.

`status` and `owner` are **inherited** — set them once on a tier and every
component beneath it picks them up, unless that component overrides it. `status`
defaults to `planned` and `owner` to `unassigned`, so a new component is honestly
marked as not-yet-built until someone says otherwise.

The generated docs roll these up: a status table and an ownership table in
`ARCHITECTURE.md`, a per-tier breakdown on each tier page, and a status badge on
every component row.

> The current tier owners (`platform-edge`, `data-platform`, `security`, …) are
> placeholders standing in for real teams. Replace them — it is one line per tier.

Directory names are slugs derived from `name` — `HTTP/3 Gateway` becomes
`http-3-gateway`, `Identity & Security` becomes `identity-and-security`. Renaming
a component moves its directory, so treat renames as a real change.

`aliases` records a name a component used to go by. Every alias is collected into
the alias index at the bottom of `ARCHITECTURE.md`, which is how an older diagram
or doc still resolves to the node that superseded it.

The generator validates the spec before writing anything and refuses to run on:

- a node missing a name or description
- a `status` outside `planned | building | running`
- an `owner` that is not a lowercase team slug
- sibling names that slugify to the same directory
- duplicate component names anywhere in the tree
- an alias claimed by two components, or shadowing a real component name
- a `depends_on` naming an unknown component, a group, or the component itself
- duplicate entries within one `depends_on`
- **a dependency cycle** — a loop means nothing in it can start, so the
  generator reports the full path and refuses to write

Every rule above has a test in [`test_scaffold.py`](./test_scaffold.py)
asserting it actually fires. A rule with no test is a rule that can silently
stop working, so the suite was checked by breaking the generator eight
different ways — dropping cycle detection, breaking alias resolution, and so on
— and confirming the tests caught all eight.

## Adding to it

Four places, depending on what you are adding.

### A command `hn` understands

Three edits in [`command.py`](./command.py), then a test. Say you want
`riskiest`, listing components with the widest blast radius:

```python
# 1. a handler. It takes the spec and the regex match, returns a Reply.
def cmd_riskiest(spec: dict, m: re.Match) -> Reply:
    up = scaffold.adjacency(spec, reverse=True)
    scored = sorted(
        ((len(scaffold.reachable(up, n["name"])), n["name"]) for n in scaffold.leaves(spec)),
        reverse=True,
    )[:10]
    return Reply(
        "**Widest blast radius:**\n\n"
        + "\n".join(f"- {name} — {count} components" for count, name in scored),
        summary="riskiest",
    )

# 2. a route, in ROUTES. First match wins, so put specific patterns above
#    general ones.
(r"^\s*(riskiest|most dangerous|biggest risk)\s*$", cmd_riskiest),

# 3. add the phrasing to cmd_help, or a test will fail: the help text is
#    checked against what the interpreter actually knows.
```

Then a test in [`test_command.py`](./test_command.py):

```python
def test_riskiest(self):
    self.assertIn("Object Storage", ask("riskiest").text)
```

A handler returning `Reply(..., changed=True)` mutates `spec` in place; the
caller validates the whole tree afterwards and refuses to write if the edit
broke anything, so a handler does not need to check for cycles itself.

### A script you run

Drop it in [`bin/`](../bin/) next to `hn` and `hn-up`, and `chmod +x` it.
Nothing registers scripts — they are just executables on a path you type.

### Something that reads the spec and writes files

Alongside `scaffold.py` and `page.py` in this directory. Import `scaffold`
for the tree and the graph helpers rather than parsing the YAML again:

```python
import scaffold, yaml
spec = yaml.safe_load(scaffold.SPEC.read_text())
scaffold.leaves(spec)            # every component
scaffold.adjacency(spec)         # name -> what it depends on
scaffold.reachable(graph, name)  # transitive closure
scaffold.build_layers(spec)      # topological layers
```

### Something that runs on GitHub

A workflow in [`.github/workflows/`](../.github/workflows/). Gate anything
that can commit on the repo owner — `command.yml` shows the pattern, and an
ungated one hands write access to anyone who can open an issue.

### Tests

Any `architecture/test_*.py` is picked up automatically:

```sh
python3 -m unittest discover -s architecture -t architecture
```

CI runs them before the staleness check, so a broken generator fails before
its output is compared.

## Scope

This tree is a **map, not an inventory** — a component having a directory does
not mean it is running. Every component carries a status, and all 157 are
currently `planned`. As components get built, the implementation, config, and
runbook belong in that component's directory, and its `status` in the spec is the
thing to update first.

Two tiers sit at the top:

- **Global Infrastructure** — cross-region, cross-cloud, cross-tenant control.
  Decides where capacity lives and where traffic goes.
- **Server Infrastructure** — what runs inside a region, split into gateway,
  compute, AI, data, identity, messaging, observability, platform operations,
  and developer platform.
