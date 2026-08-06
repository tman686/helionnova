# Architecture

The Helion Nova platform component tree — every server, service, and tier the
platform is planned to run, in one place.

| File | Role |
| --- | --- |
| [`universe.yaml`](./universe.yaml) | **Source of truth.** Hand-edited. |
| [`scaffold.py`](./scaffold.py) | Generator. Reads the spec, writes everything below. |
| [`ARCHITECTURE.md`](./ARCHITECTURE.md) | Generated. Full tree, per-tier tables, alias index. |
| [`universe/`](./universe/) | Generated. One directory per node, each with a `README.md`. |

## Working on it

```sh
pip install pyyaml
python3 architecture/scaffold.py          # regenerate after editing the spec
python3 architecture/scaffold.py --check  # CI: fail if output is stale
```

Edit `universe.yaml` and regenerate. Never hand-edit `ARCHITECTURE.md` or
anything under `universe/` — the generator deletes and rewrites that tree, so
manual changes are lost on the next run.

`--check` regenerates into place and reports whether anything changed, so a
stale commit fails loudly instead of drifting.

## Spec format

```yaml
- name: Rate Limiter
  description: Per-tenant, per-key, and per-route quota enforcement with burst budgets.
  aliases: [Throttle Server]     # optional
  children: []                   # optional; omit for a leaf component
```

Directory names are slugs derived from `name` — `HTTP/3 Gateway` becomes
`http-3-gateway`, `Identity & Security` becomes `identity-and-security`. Renaming
a component moves its directory, so treat renames as a real change.

`aliases` records a name a component used to go by. Every alias is collected into
the alias index at the bottom of `ARCHITECTURE.md`, which is how an older diagram
or doc still resolves to the node that superseded it.

The generator validates the spec before writing anything and refuses to run on:

- a node missing a name or description
- sibling names that slugify to the same directory
- duplicate component names anywhere in the tree
- an alias claimed by two components, or shadowing a real component name

## Scope

This tree is a **map, not an inventory** — a component having a directory does
not mean it is running. Each leaf `README.md` carries a status line; the default
is "not implemented". As components get built, the implementation, config, and
runbook belong in that directory, and the status line is the thing to update
first.

Two tiers sit at the top:

- **Global Infrastructure** — cross-region, cross-cloud, cross-tenant control.
  Decides where capacity lives and where traffic goes.
- **Server Infrastructure** — what runs inside a region, split into gateway,
  compute, AI, data, identity, messaging, observability, platform operations,
  and developer platform.
