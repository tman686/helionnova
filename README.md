# helionnova

Nuclear energy — Helion Nova Energy Inc., Lakeland, FL.

## Contents

| Path | What it is |
| --- | --- |
| [`index.html`](./index.html) | The public site — a single self-contained page. |
| [`architecture/`](./architecture/) | The platform component tree: what the platform is planned to run. |

## Architecture

[`architecture/`](./architecture/) holds the `Universe` component hierarchy — 157
components across Global Infrastructure and nine Server Infrastructure tiers.

[`architecture/universe.yaml`](./architecture/universe.yaml) is the source of
truth; [`ARCHITECTURE.md`](./architecture/ARCHITECTURE.md) and the
[`universe/`](./architecture/universe/) directory tree are generated from it:

```sh
pip install pyyaml
python3 architecture/scaffold.py
```

It is a map, not an inventory — a component having a directory does not mean it
is running. See [`architecture/README.md`](./architecture/README.md) for how to
change it.
