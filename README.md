# helionnova

Nuclear energy — Helion Nova Energy Inc., Lakeland, FL.

The repo holds a map of the platform we plan to build, and **a way to talk to
that map in plain English** — from a phone, a terminal, or a GitHub issue.

```
$ hn status
**180 components** across 11 tiers.
- 180 planned, 0 building, 0 running
- 333 dependency edges, 0 cycles
- most depended on: Object Storage (25), Metrics (15), Cache Cluster (11)

$ hn what depends on object storage
**25** depend on **Object Storage**:
- Archive Manager
- Artifact Repository
…
```

## Talking to it

```sh
./bin/hn help                            # what it understands
./bin/hn status
./bin/hn what depends on object storage
./bin/hn find kafka
./bin/hn mark inference as building
```

Local by default: no network, no token, answers immediately, and **never
writes**. That is the mode to use for trying things out.

To make a change stick, send it to GitHub instead:

```sh
export GH_TOKEN=...                      # github.com/settings/tokens
./bin/hn --remote mark inference as building
```

That runs the [Command workflow](.github/workflows/command.yml) on GitHub's
machines, which edits the spec, regenerates everything downstream, runs the
tests, and commits. Expect 20–60 seconds — nearly all of it the runner booting.

You can also just **comment on any issue** in the repo. The reply comes back in
the thread, which is the easiest way to use it from a phone.

Only the repository owner is obeyed. Anyone can comment on a public repo and
the workflow can commit, so an ungated version would hand write access to
whoever turned up.

### What it understands

| Asking | |
| --- | --- |
| `status` | counts, dependency edges, cycles |
| `tiers` | every tier, with its owner |
| `show <tier>` | that tier's components |
| `what depends on <component>` | everything that would break without it |
| `what does <component> need` | its upstreams |
| `find <text>` | search names, aliases, descriptions |

| Changing | |
| --- | --- |
| `add <name> to <tier>: <description>` | new component |
| `mark <component> as planned\|building\|running` | lifecycle |
| `set owner of <tier> to <team-slug>` | ownership |
| `make <component> depend on <component>` | new edge |

This is pattern matching with some slack around the phrasing — not language
understanding. It handles what it was taught and says so plainly when it
doesn't recognise something.

**It cannot corrupt the map.** Every edit re-validates the whole tree before
anything is written. An edit that would create a duplicate name, a dangling
dependency, or a cycle is refused, and the reply explains why.

## The map itself

[`architecture/`](./architecture/) holds the `Universe` tree — 180 components
across 11 tiers, wired into a 333-edge dependency graph.

[`universe.yaml`](./architecture/universe.yaml) is the source of truth.
[`ARCHITECTURE.md`](./architecture/ARCHITECTURE.md) and the
[`universe/`](./architecture/universe/) directory tree are generated from it:

```sh
pip install pyyaml
python3 architecture/scaffold.py
```

It is a **map, not an inventory** — all 180 components are `planned`. Nothing
here is deployed, and there is no server behind it beyond the GitHub Actions
runner that answers your messages. See
[`architecture/README.md`](./architecture/README.md) for how to change it.

## Everything else

| Path | What it is |
| --- | --- |
| [`bin/hn`](./bin/hn) | The client. Local by default, `--remote` to commit. |
| [`architecture/command.py`](./architecture/command.py) | Interprets the message and acts. |
| [`architecture/page.py`](./architecture/page.py) | Renders the tree as a browsable page. |
| [`index.html`](./index.html) | The public site — a single self-contained page. |
| [`TERMUX.md`](./TERMUX.md) | Cloning, editing, and pushing from Android. |

## Tests

```sh
python3 -m unittest discover -s architecture -t architecture
```

96 tests, stdlib only. They run in CI on every change under `architecture/`,
before the staleness check.
