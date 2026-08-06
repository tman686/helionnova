#!/usr/bin/env python3
"""Interpret a plain-English message and act on the architecture spec.

    python3 architecture/command.py "what depends on object storage"
    python3 architecture/command.py "mark inference as building"

Reads the message, runs the matching command, prints a Markdown reply, and
exits 0. Exit 2 means the message was not understood; exit 1 means it was
understood but could not be carried out.

This is pattern matching, not language understanding. It recognises a fixed
set of phrasings with some slack around them — it does not reason about
anything it has not been taught. `help` lists what it knows.

Every command that edits the spec re-validates the whole tree before writing.
If an edit would produce a duplicate name, a broken dependency, or a cycle,
nothing is written and the reply says why. A malformed message can waste a
workflow run; it cannot corrupt the spec.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field

import yaml

import scaffold


@dataclass
class Reply:
    text: str
    changed: bool = False
    ok: bool = True
    summary: str = ""
    _handled: bool = field(default=True, repr=False)


# --------------------------------------------------------------------- lookup


def find(spec: dict, term: str):
    """Resolve a component by name or alias, case- and punctuation-tolerant."""
    index = scaffold.build_index(spec)
    key = term.strip().strip(".?!").lower()
    if key in index:
        return index[key]
    squashed = {scaffold.slugify(k): v for k, v in index.items()}
    return squashed.get(scaffold.slugify(key))


def groups(spec: dict) -> dict[str, tuple[dict, list[dict]]]:
    """Every node that holds children, by lowercased name.

    Includes domains like Server Infrastructure, which hold tiers rather than
    components — an owner can legitimately be set at that level and inherited
    down. Use `component_groups` when you mean "a tier you can list".
    """
    out = {}
    for node, ancestors in scaffold.walk(spec):
        if scaffold.children(node) and node is not spec:
            out[node["name"].lower()] = (node, ancestors)
    return out


def component_groups(spec: dict) -> dict[str, tuple[dict, list[dict]]]:
    """Only groups that directly hold components."""
    return {
        name: (node, ancestors)
        for name, (node, ancestors) in groups(spec).items()
        if any(scaffold.is_leaf(c) for c in scaffold.children(node))
    }


def find_group(spec: dict, term: str, components_only: bool = False):
    key = term.strip().strip(".?!").lower()
    table = component_groups(spec) if components_only else groups(spec)
    if key in table:
        return table[key]
    squashed = {scaffold.slugify(k): v for k, v in table.items()}
    return squashed.get(scaffold.slugify(key))


def did_you_mean(spec: dict, term: str, limit: int = 4) -> str:
    """Cheap suggestion: names sharing a word with the term."""
    words = {w for w in re.split(r"\W+", term.lower()) if len(w) > 2}
    hits = [
        n["name"]
        for n in scaffold.leaves(spec)
        if words & {w for w in re.split(r"\W+", n["name"].lower()) if w}
    ]
    return f" Did you mean: {', '.join(hits[:limit])}?" if hits else ""


# ------------------------------------------------------------------- commands


def cmd_help(spec: dict, m: re.Match) -> Reply:
    return Reply(
        "**Commands I understand.** Anything in `<>` is yours to fill in.\n\n"
        "*Asking:*\n"
        "- `status` — component counts, dependency edges, cycles\n"
        "- `what depends on <component>` — everything that would break with it\n"
        "- `what does <component> need` — its upstreams\n"
        "- `find <text>` — search names, aliases, and descriptions\n"
        "- `show <tier>` — list a tier's components\n"
        "- `tiers` — list every tier\n\n"
        "*Changing:*\n"
        "- `add <name> to <tier>: <description>`\n"
        "- `mark <component> as planned|building|running`\n"
        "- `set owner of <tier> to <team-slug>`\n"
        "- `make <component> depend on <component>`\n\n"
        "Every change is validated before it is written. If it would break the "
        "tree, nothing is saved and I say why."
    )


def cmd_status(spec: dict, m: re.Match) -> Reply:
    counts = scaffold.status_rollup(spec, [])
    edges = scaffold.dependency_edges(spec)
    total = scaffold.count_leaves(spec)
    tiers = len(component_groups(spec))
    top = sorted(scaffold.dependents_map(spec).items(), key=lambda kv: -len(kv[1]))[:3]
    lines = [
        f"**{total} components** across {tiers} tiers.",
        "",
        f"- {counts['planned']} planned, {counts['building']} building, "
        f"{counts['running']} running",
        f"- {len(edges)} dependency edges, {len(scaffold.find_cycles(spec))} cycles",
        "- most depended on: " + ", ".join(f"{n} ({len(d)})" for n, d in top),
    ]
    return Reply("\n".join(lines), summary=f"{total} components, {len(edges)} edges")


def cmd_dependents(spec: dict, m: re.Match) -> Reply:
    found = find(spec, m.group("name"))
    if not found:
        return Reply(
            f"No component called **{m.group('name')}**." + did_you_mean(spec, m.group("name")),
            ok=False,
        )
    node = found[0]
    dependents = scaffold.dependents_map(spec).get(node["name"], [])
    if not dependents:
        return Reply(f"Nothing depends on **{node['name']}** yet.")
    return Reply(
        f"**{len(dependents)}** depend on **{node['name']}**:\n\n"
        + "\n".join(f"- {d}" for d in dependents),
        summary=f"{node['name']}: {len(dependents)} dependents",
    )


def cmd_dependencies(spec: dict, m: re.Match) -> Reply:
    found = find(spec, m.group("name"))
    if not found:
        return Reply(
            f"No component called **{m.group('name')}**." + did_you_mean(spec, m.group("name")),
            ok=False,
        )
    node = found[0]
    needs = scaffold.depends_on(node)
    if not needs:
        return Reply(f"**{node['name']}** depends on nothing — it is a foundation.")
    return Reply(
        f"**{node['name']}** needs:\n\n" + "\n".join(f"- {d}" for d in needs),
        summary=f"{node['name']}: {len(needs)} dependencies",
    )


def cmd_find(spec: dict, m: re.Match) -> Reply:
    term = m.group("term").strip().strip(".?!").lower()
    hits = []
    for node in scaffold.leaves(spec):
        haystack = " ".join(
            [node["name"], node.get("description", ""), *(node.get("aliases") or [])]
        ).lower()
        if term in haystack:
            hits.append(node["name"])
    if not hits:
        return Reply(f"Nothing matches **{term}**.", ok=False)
    shown = hits[:20]
    more = f"\n\n…and {len(hits) - len(shown)} more." if len(hits) > len(shown) else ""
    return Reply(
        f"**{len(hits)}** match **{term}**:\n\n" + "\n".join(f"- {h}" for h in shown) + more,
        summary=f"{len(hits)} matches for {term}",
    )


def cmd_tiers(spec: dict, m: re.Match) -> Reply:
    rows = [
        f"- **{node['name']}** — {scaffold.count_leaves(node)} components, "
        f"owner `{scaffold.owner_of(node, ancestors)}`"
        for node, ancestors in component_groups(spec).values()
    ]
    return Reply(f"**{len(rows)} tiers:**\n\n" + "\n".join(rows))


def cmd_show(spec: dict, m: re.Match) -> Reply:
    found = find_group(spec, m.group("tier"), components_only=True)
    if not found:
        return Reply(
            f"No tier called **{m.group('tier')}**. Try `tiers` for the list.", ok=False
        )
    node, ancestors = found
    rows = [
        f"- **{c['name']}** — {scaffold.status_of(c, ancestors + [node])}"
        for c in scaffold.children(node)
    ]
    return Reply(
        f"**{node['name']}** ({len(rows)} components, owner "
        f"`{scaffold.owner_of(node, ancestors)}`):\n\n" + "\n".join(rows),
        summary=f"{node['name']}: {len(rows)} components",
    )


def cmd_mark(spec: dict, m: re.Match) -> Reply:
    status = m.group("status").lower()
    found = find(spec, m.group("name"))
    if not found:
        return Reply(
            f"No component called **{m.group('name')}**." + did_you_mean(spec, m.group("name")),
            ok=False,
        )
    node, ancestors = found
    if scaffold.children(node):
        return Reply(f"**{node['name']}** is a tier, not a component.", ok=False)
    before = scaffold.status_of(node, ancestors)
    if before == status:
        return Reply(f"**{node['name']}** is already `{status}`.")
    node["status"] = status
    return Reply(
        f"**{node['name']}**: `{before}` → `{status}`",
        changed=True,
        summary=f"{node['name']} -> {status}",
    )


def cmd_owner(spec: dict, m: re.Match) -> Reply:
    found = find_group(spec, m.group("tier"))
    if not found:
        return Reply(f"No tier called **{m.group('tier')}**.", ok=False)
    node, ancestors = found
    before = scaffold.owner_of(node, ancestors)
    node["owner"] = m.group("slug").lower()
    return Reply(
        f"**{node['name']}** owner: `{before}` → `{node['owner']}`",
        changed=True,
        summary=f"{node['name']} owner -> {node['owner']}",
    )


def cmd_add(spec: dict, m: re.Match) -> Reply:
    name = m.group("name").strip()
    found_group = find_group(spec, m.group("tier"), components_only=True)
    if not found_group:
        return Reply(
            f"No tier called **{m.group('tier')}**. Try `tiers` for the list.", ok=False
        )
    if find(spec, name):
        return Reply(f"**{name}** already exists.", ok=False)
    description = (m.group("description") or "").strip() or "Not yet described."
    node, _ = found_group
    node.setdefault("children", []).append({"name": name, "description": description})
    note = "" if m.group("description") else "\n\nNo description given, so I used a placeholder."
    return Reply(
        f"Added **{name}** to **{node['name']}**.{note}",
        changed=True,
        summary=f"add {name} to {node['name']}",
    )


def cmd_depend(spec: dict, m: re.Match) -> Reply:
    src = find(spec, m.group("name"))
    dst = find(spec, m.group("target"))
    if not src:
        return Reply(f"No component called **{m.group('name')}**.", ok=False)
    if not dst:
        return Reply(f"No component called **{m.group('target')}**.", ok=False)
    node, target = src[0], dst[0]
    if scaffold.children(node) or scaffold.children(target):
        return Reply("Dependencies go between components, not tiers.", ok=False)
    existing = node.setdefault("depends_on", [])
    if any(d.lower() == target["name"].lower() for d in existing):
        return Reply(f"**{node['name']}** already depends on **{target['name']}**.")
    existing.append(target["name"])
    return Reply(
        f"**{node['name']}** now depends on **{target['name']}**.",
        changed=True,
        summary=f"{node['name']} -> {target['name']}",
    )


NAME = r"(?P<name>.+?)"
TARGET = r"(?P<target>.+?)"

# Ordered: the first pattern that matches wins, so put the specific ones first.
ROUTES: list[tuple[str, callable]] = [
    (r"^\s*(help|commands|what can you do|\?)\s*$", cmd_help),
    (r"^\s*(status|stats|summary|how many|overview)\b", cmd_status),
    (r"^\s*(tiers|list tiers|what tiers)\b", cmd_tiers),
    (rf"^\s*(?:what|who)\s+(?:depends on|needs|uses)\s+{NAME}\s*$", cmd_dependents),
    (rf"^\s*dependents\s+(?:of\s+)?{NAME}\s*$", cmd_dependents),
    (rf"^\s*what\s+does\s+{NAME}\s+(?:need|depend on|use)\s*$", cmd_dependencies),
    (rf"^\s*(?:dependencies|upstreams)\s+(?:of\s+)?{NAME}\s*$", cmd_dependencies),
    (r"^\s*(?:show|list)\s+(?:me\s+)?(?:the\s+)?(?P<tier>.+?)\s*$", cmd_show),
    (r"^\s*(?:find|search|where is|look up)\s+(?P<term>.+?)\s*$", cmd_find),
    (
        rf"^\s*(?:mark|set)\s+{NAME}\s+(?:as|to|status)\s+"
        r"(?P<status>planned|building|running)\s*$",
        cmd_mark,
    ),
    (
        r"^\s*set\s+(?:the\s+)?owner\s+(?:of\s+)?(?P<tier>.+?)\s+to\s+"
        r"(?P<slug>[a-z0-9][a-z0-9-]*)\s*$",
        cmd_owner,
    ),
    (
        r"^\s*add\s+(?:component\s+)?(?P<name>.+?)\s+to\s+(?P<tier>[^:]+?)"
        r"(?:\s*[:—-]\s*(?P<description>.+))?\s*$",
        cmd_add,
    ),
    (rf"^\s*make\s+{NAME}\s+depend\s+on\s+{TARGET}\s*$", cmd_depend),
    (rf"^\s*{NAME}\s+depends\s+on\s+{TARGET}\s*$", cmd_depend),
]


def dispatch(spec: dict, message: str) -> Reply:
    text = " ".join(message.split())
    for pattern, handler in ROUTES:
        match = re.match(pattern, text, re.I)
        if match:
            return handler(spec, match)
    return Reply(
        f"I did not understand **{text}**.\n\nSend `help` for what I know.",
        ok=False,
        _handled=False,
    )


# ----------------------------------------------------------------------- main


def write_spec(spec: dict) -> None:
    """Write the spec back, keeping comments and formatting intact."""
    from ruamel.yaml import YAML

    yaml_rt = YAML()
    yaml_rt.preserve_quotes = True
    yaml_rt.indent(mapping=2, sequence=4, offset=2)
    yaml_rt.width = 78
    with open(scaffold.SPEC, "w", encoding="utf-8") as fh:
        yaml_rt.dump(spec, fh)


def load_roundtrip() -> dict:
    from ruamel.yaml import YAML

    yaml_rt = YAML()
    yaml_rt.preserve_quotes = True
    yaml_rt.indent(mapping=2, sequence=4, offset=2)
    yaml_rt.width = 78
    return yaml_rt.load(scaffold.SPEC.read_text(encoding="utf-8"))


def run(message: str, apply: bool = False) -> tuple[Reply, int]:
    # Read-only commands use the plain loader so ruamel is not needed to ask a
    # question; only an edit reaches for the comment-preserving one.
    spec = yaml.safe_load(scaffold.SPEC.read_text(encoding="utf-8"))
    probe = dispatch(spec, message)

    if not probe.changed:
        return probe, (0 if probe.ok else (2 if not probe._handled else 1))

    spec = load_roundtrip()
    reply = dispatch(spec, message)

    errors = scaffold.validate(spec)
    if errors:
        listed = "\n".join(f"- {e}" for e in errors[:5])
        return (
            Reply(
                f"{reply.text}\n\n**Not saved** — that would break the tree:\n\n{listed}",
                ok=False,
            ),
            1,
        )

    if apply:
        write_spec(spec)
    return reply, 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message", help="the message to interpret")
    parser.add_argument(
        "--apply", action="store_true", help="write spec changes (default is a dry run)"
    )
    parser.add_argument("--summary-to", help="append a one-line summary to this file")
    args = parser.parse_args()

    reply, code = run(args.message, apply=args.apply)
    print(reply.text)

    if args.summary_to and reply.summary:
        with open(args.summary_to, "a", encoding="utf-8") as fh:
            fh.write(reply.summary + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
