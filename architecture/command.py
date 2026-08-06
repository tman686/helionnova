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
        "Ask in ordinary words. Put your own component names where these use "
        "examples — do not type any brackets.\n\n"
        "**Following the graph**\n"
        "- `what breaks if object storage fails` — everything downstream, not just\n"
        "  the direct dependents\n"
        "- `everything rag needs` — the full set that must exist first\n"
        "- `why does inference need key management` — the chain that connects them\n"
        "- `build order` — layers you could build in, foundations first\n"
        "- `foundations` — depend on nothing\n"
        "- `orphans` — nothing depends on them\n\n"
        "**Looking things up**\n"
        "- `about inference` — everything known about one component\n"
        "- `what depends on metrics` / `what does rag need` — one hop\n"
        "- `find kafka` — search names, aliases, descriptions\n"
        "- `what's in messaging` — a tier's components\n"
        "- `tiers`, `status`, `check`\n\n"
        "**Changing**\n"
        "- `add Redis Cache to Data Tier: Caches hot queries.`\n"
        "- `mark inference as building`\n"
        "- `set owner of data tier to storage-crew`\n"
        "- `make web search depend on metrics`\n"
        "- `make web search no longer depend on metrics`\n"
        "- `remove redis cache`\n\n"
        "Every change is validated against the whole tree before it is written. "
        "A duplicate name, a dangling dependency, or a cycle is refused with a "
        "reason. Removing something other components depend on is refused too."
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


def _listing(names, limit: int = 25) -> str:
    names = list(names)
    shown = names[:limit]
    tail = f"\n\n…and {len(names) - len(shown)} more." if len(names) > limit else ""
    return "\n".join(f"- {n}" for n in shown) + tail


def cmd_blast(spec: dict, m: re.Match) -> Reply:
    """Everything that would break, not just the direct dependents."""
    found = find(spec, m.group("name"))
    if not found:
        return Reply(
            f"No component called **{m.group('name')}**." + did_you_mean(spec, m.group("name")),
            ok=False,
        )
    node = found[0]
    direct = scaffold.dependents_map(spec).get(node["name"], [])
    everything = scaffold.reachable(scaffold.adjacency(spec, reverse=True), node["name"])
    total = scaffold.count_leaves(spec)
    indirect = sorted(everything - set(direct))
    body = (
        f"**{len(everything)} of {total} components** fail if **{node['name']}** does "
        f"— {100 * len(everything) / total:.0f}% of the platform.\n\n"
        f"**{len(direct)} directly:**\n{_listing(direct)}"
    )
    if indirect:
        body += f"\n\n**{len(indirect)} further down the chain:**\n{_listing(indirect)}"
    return Reply(body, summary=f"{node['name']}: blast radius {len(everything)}")


def cmd_needs_all(spec: dict, m: re.Match) -> Reply:
    """The full set that must exist before this can start."""
    found = find(spec, m.group("name"))
    if not found:
        return Reply(f"No component called **{m.group('name')}**.", ok=False)
    node = found[0]
    direct = scaffold.depends_on(node)
    everything = scaffold.reachable(scaffold.adjacency(spec), node["name"])
    if not everything:
        return Reply(f"**{node['name']}** needs nothing — it is a foundation.")
    indirect = sorted(everything - set(direct))
    body = (
        f"**{node['name']}** needs **{len(everything)}** components before it can run.\n\n"
        f"**{len(direct)} directly:**\n{_listing(direct)}"
    )
    if indirect:
        body += f"\n\n**{len(indirect)} transitively:**\n{_listing(indirect)}"
    return Reply(body, summary=f"{node['name']} needs {len(everything)}")


def cmd_why(spec: dict, m: re.Match) -> Reply:
    """Show the chain that connects two components."""
    src, dst = find(spec, m.group("name")), find(spec, m.group("target"))
    if not src:
        return Reply(f"No component called **{m.group('name')}**.", ok=False)
    if not dst:
        return Reply(f"No component called **{m.group('target')}**.", ok=False)
    a, b = src[0]["name"], dst[0]["name"]
    path = scaffold.shortest_path(scaffold.adjacency(spec), a, b)
    if not path:
        back = scaffold.shortest_path(scaffold.adjacency(spec), b, a)
        if back:
            return Reply(
                f"**{a}** does not depend on **{b}** — it is the other way round:\n\n"
                + " → ".join(back),
                ok=False,
            )
        return Reply(f"**{a}** does not depend on **{b}**, directly or otherwise.", ok=False)
    hops = len(path) - 1
    return Reply(
        f"**{a}** needs **{b}** in {hops} hop{'s' if hops != 1 else ''}:\n\n"
        + " → ".join(f"**{p}**" if p in (a, b) else p for p in path),
        summary=f"{a} -> {b} in {hops}",
    )


def cmd_build_order(spec: dict, m: re.Match) -> Reply:
    layers = scaffold.build_layers(spec)
    if not layers:
        return Reply("Nothing to order.", ok=False)
    lines = [
        f"**{len(layers)} build layers.** Everything in a layer can be built at once, "
        "once the layer above it exists.\n"
    ]
    for i, layer in enumerate(layers):
        head = "foundations" if i == 0 else f"layer {i}"
        lines.append(f"**{head}** ({len(layer)}): " + ", ".join(layer))
    return Reply("\n\n".join(lines), summary=f"{len(layers)} build layers")


def cmd_about(spec: dict, m: re.Match) -> Reply:
    """Everything known about one component, on one card."""
    found = find(spec, m.group("name"))
    if not found:
        return Reply(
            f"No component called **{m.group('name')}**." + did_you_mean(spec, m.group("name")),
            ok=False,
        )
    node, ancestors = found
    if scaffold.children(node):
        return cmd_show(spec, m)
    dependents = scaffold.dependents_map(spec).get(node["name"], [])
    blast = scaffold.reachable(scaffold.adjacency(spec, reverse=True), node["name"])
    needs = scaffold.reachable(scaffold.adjacency(spec), node["name"])
    lines = [
        f"### {node['name']}",
        "",
        scaffold.clean(node.get("description", "")),
        "",
        f"- **tier** {ancestors[-1]['name']}",
        f"- **status** {scaffold.status_of(node, ancestors)}",
        f"- **owner** `{scaffold.owner_of(node, ancestors)}`",
    ]
    if node.get("aliases"):
        lines.append(f"- **also known as** {', '.join(node['aliases'])}")
    lines += [
        f"- **needs** {len(scaffold.depends_on(node))} directly, {len(needs)} in total",
        f"- **needed by** {len(dependents)} directly, {len(blast)} in total",
    ]
    if scaffold.depends_on(node):
        lines += ["", "**Depends on:** " + ", ".join(scaffold.depends_on(node))]
    if dependents:
        lines += ["**Depended on by:** " + ", ".join(dependents)]
    return Reply("\n".join(lines), summary=f"about {node['name']}")


def cmd_foundations(spec: dict, m: re.Match) -> Reply:
    names = sorted(n["name"] for n in scaffold.leaves(spec) if not scaffold.depends_on(n))
    return Reply(
        f"**{len(names)} foundations** — they depend on nothing, so they get built "
        f"first:\n\n{_listing(names, 60)}",
        summary=f"{len(names)} foundations",
    )


def cmd_orphans(spec: dict, m: re.Match) -> Reply:
    reverse = scaffold.dependents_map(spec)
    names = sorted(n["name"] for n in scaffold.leaves(spec) if not reverse.get(n["name"]))
    return Reply(
        f"**{len(names)} components** have nothing depending on them. Leaves of the "
        f"graph — safe to build last, or to cut:\n\n{_listing(names, 60)}",
        summary=f"{len(names)} orphans",
    )


def cmd_check(spec: dict, m: re.Match) -> Reply:
    errors = scaffold.validate(spec)
    if errors:
        return Reply(
            f"**{len(errors)} problems:**\n\n" + _listing(errors, 20),
            ok=False,
            summary=f"{len(errors)} problems",
        )
    return Reply(
        f"Clean. {scaffold.count_leaves(spec)} components, "
        f"{len(scaffold.dependency_edges(spec))} edges, no cycles, every dependency "
        "resolves.",
        summary="spec clean",
    )


def cmd_unlink(spec: dict, m: re.Match) -> Reply:
    src, dst = find(spec, m.group("name")), find(spec, m.group("target"))
    if not src or not dst:
        return Reply("I do not know one of those components.", ok=False)
    node, target = src[0], dst[0]
    existing = scaffold.depends_on(node)
    match = [d for d in existing if d.lower() == target["name"].lower()]
    if not match:
        return Reply(f"**{node['name']}** does not depend on **{target['name']}**.", ok=False)
    node["depends_on"] = [d for d in existing if d not in match]
    if not node["depends_on"]:
        del node["depends_on"]
    return Reply(
        f"**{node['name']}** no longer depends on **{target['name']}**.",
        changed=True,
        summary=f"unlink {node['name']} -> {target['name']}",
    )


def cmd_remove(spec: dict, m: re.Match) -> Reply:
    found = find(spec, m.group("name"))
    if not found:
        return Reply(f"No component called **{m.group('name')}**.", ok=False)
    node, ancestors = found
    if scaffold.children(node):
        return Reply(f"**{node['name']}** is a tier. I only remove components.", ok=False)
    dependents = scaffold.dependents_map(spec).get(node["name"], [])
    if dependents:
        return Reply(
            f"**{node['name']}** cannot go — {len(dependents)} depend on it:\n\n"
            f"{_listing(dependents)}\n\nDetach them first.",
            ok=False,
        )
    parent = ancestors[-1]
    parent["children"] = [c for c in parent["children"] if c is not node]
    return Reply(
        f"Removed **{node['name']}** from **{parent['name']}**.",
        changed=True,
        summary=f"remove {node['name']}",
    )


NAME = r"(?P<name>.+?)"
TARGET = r"(?P<target>.+?)"

# Ordered: the first pattern that matches wins, so put the specific ones first.
ROUTES: list[tuple[str, callable]] = [
    (r"^\s*(help|commands|what can you do|\?)\s*$", cmd_help),
    (r"^\s*(status|stats|summary|how many|overview)\b", cmd_status),
    (r"^\s*(tiers|list tiers|what tiers)\b", cmd_tiers),
    (r"^\s*(foundations|what are the foundations|roots)\s*$", cmd_foundations),
    (r"^\s*(orphans|leaves|unused|what is unused)\s*$", cmd_orphans),
    (r"^\s*(build order|build layers|order|topo|topological)\s*$", cmd_build_order),
    (r"^\s*(check|validate|is it valid|lint|health)\s*$", cmd_check),
    # Graph-reach questions — before the one-hop versions they generalise.
    (rf"^\s*(?:blast radius|impact|impact of|blast)\s+(?:of\s+|i\s+lose\s+)?{NAME}\s*$", cmd_blast),
    # "…if X fails" — the trailing verb is part of the phrasing, not the name.
    (
        rf"^\s*what\s+(?:all\s+)?(?:breaks|fails|happens)\s+"
        rf"(?:if\s+(?:i\s+lose\s+)?|when\s+|without\s+)?{NAME}"
        r"(?:\s+(?:fails|dies|breaks|goes\s+down|is\s+down|goes\s+away))?\s*$",
        cmd_blast,
    ),
    (rf"^\s*(?:everything|all)\s+(?:that\s+)?{NAME}\s+needs\s*$", cmd_needs_all),
    (rf"^\s*what\s+does\s+{NAME}\s+need\s+in\s+total\s*$", cmd_needs_all),
    (rf"^\s*why\s+does\s+{NAME}\s+(?:need|depend on)\s+{TARGET}\s*$", cmd_why),
    (rf"^\s*(?:path|chain)\s+from\s+{NAME}\s+to\s+{TARGET}\s*$", cmd_why),
    (rf"^\s*(?:about|describe|tell me about|info(?:\s+on)?|explain)\s+{NAME}\s*$", cmd_about),
    (rf"^\s*(?:what|who)\s+(?:depends on|needs|uses)\s+{NAME}\s*$", cmd_dependents),
    (rf"^\s*dependents\s+(?:of\s+)?{NAME}\s*$", cmd_dependents),
    (rf"^\s*what\s+does\s+{NAME}\s+(?:need|depend on|use)\s*$", cmd_dependencies),
    (rf"^\s*(?:dependencies|upstreams)\s+(?:of\s+)?{NAME}\s*$", cmd_dependencies),
    (rf"^\s*(?:remove|delete|drop)\s+{NAME}\s*$", cmd_remove),
    (rf"^\s*(?:unlink|detach)\s+{NAME}\s+from\s+{TARGET}\s*$", cmd_unlink),
    (rf"^\s*make\s+{NAME}\s+(?:no longer|not)\s+depend\s+on\s+{TARGET}\s*$", cmd_unlink),
    (r"^\s*what(?:'s|s| is)\s+in\s+(?:the\s+)?(?P<tier>.+?)\s*$", cmd_show),
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


def tidy(text: str) -> str:
    """Undo ruamel's cosmetic churn so a one-line edit is a one-line diff.

    Re-wrapping a folded scalar leaves a trailing space before each break, and
    the round-trip sprinkles blank lines inside mappings. Left alone, changing
    one component rewrites 140 lines and buries the actual edit.

    Only whitespace is touched. A blank line is dropped only when it sits
    between two indented lines — the structural blank lines at the top of the
    file are between column-0 lines and survive.
    """
    lines = text.split("\n")
    out: list[str] = []
    for i, line in enumerate(lines):
        stripped = line.rstrip()
        if stripped == "":
            previous = next((l for l in reversed(out) if l.strip()), "")
            following = next((l for l in lines[i + 1:] if l.strip()), "")
            indented = lambda s: len(s) - len(s.lstrip()) >= 4  # noqa: E731
            if indented(previous) and indented(following):
                continue
        out.append(stripped)
    return "\n".join(out)


def write_spec(spec: dict) -> None:
    """Write the spec back, keeping comments and formatting intact."""
    import io

    from ruamel.yaml import YAML

    yaml_rt = YAML()
    yaml_rt.preserve_quotes = True
    yaml_rt.indent(mapping=2, sequence=4, offset=2)
    yaml_rt.width = 78
    buffer = io.StringIO()
    yaml_rt.dump(spec, buffer)
    scaffold.SPEC.write_text(tidy(buffer.getvalue()), encoding="utf-8")


def load_roundtrip() -> dict:
    from ruamel.yaml import YAML

    yaml_rt = YAML()
    yaml_rt.preserve_quotes = True
    yaml_rt.indent(mapping=2, sequence=4, offset=2)
    yaml_rt.width = 78
    return yaml_rt.load(scaffold.SPEC.read_text(encoding="utf-8"))


def run(message: str, apply: bool = False) -> tuple[Reply, int]:
    """Interpret the message; write only when `apply` and the result validates.

    ruamel is a *writing* dependency — it exists to keep comments and
    formatting intact when the spec is rewritten. Nothing on the read or
    dry-run path may import it, so asking a question needs only pyyaml.
    """
    spec = yaml.safe_load(scaffold.SPEC.read_text(encoding="utf-8"))
    reply = dispatch(spec, message)

    if not reply.changed:
        return reply, (0 if reply.ok else (2 if not reply._handled else 1))

    # `spec` now carries the edit. Validate it before going near the file.
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
        # Re-apply to a comment-preserving tree, since the plain loader
        # discarded every comment on the way in.
        preserved = load_roundtrip()
        dispatch(preserved, message)
        write_spec(preserved)
    return reply, 0


BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"

QUIT = {"quit", "exit", "q", ":q", "bye"}

DRY_RUN_NOTE = (
    "_Preview only — nothing was saved. Add --apply to write it here, "
    "or --remote to commit it on GitHub._"
)


def for_terminal(text: str, colour: bool) -> str:
    """Markdown is for GitHub; a terminal wants plain text.

    Only applied when writing to a terminal, so the workflow still posts real
    Markdown into issue threads.
    """
    text = re.sub(r"^#+\s*", "", text, flags=re.M)
    if colour:
        text = re.sub(r"\*\*(.+?)\*\*", f"{BOLD}\\1{RESET}", text)
        text = re.sub(r"_(.+?)_", f"{DIM}\\1{RESET}", text)
    else:
        text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
        text = re.sub(r"_(.+?)_", r"\1", text)
    return text.replace("`", "")


def emit(reply: Reply, apply: bool, colour: bool) -> None:
    text = reply.text
    if reply.changed and not apply:
        text += "\n\n" + DRY_RUN_NOTE
    print(for_terminal(text, colour) if sys.stdout.isatty() else text)


def interactive(apply: bool) -> int:
    """Type messages one after another instead of re-invoking the command."""
    colour = sys.stdout.isatty()
    total = scaffold.count_leaves(yaml.safe_load(scaffold.SPEC.read_text(encoding="utf-8")))
    bold = BOLD if colour else ""
    reset = RESET if colour else ""
    print(f"{bold}helionnova{reset} — {total} components. Ask in plain words.")
    print(f"'help' for what I know, 'quit' to leave."
          f"{'' if apply else '  Nothing is saved in this mode.'}")

    while True:
        try:
            line = input(f"\n{bold}hn>{reset} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line.lower() in QUIT:
            return 0
        try:
            reply, _ = run(line, apply=apply)
            emit(reply, apply, colour)
        except Exception as exc:  # a bad message must not end the session
            print(f"Something went wrong: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message", nargs="?", help="the message to interpret")
    parser.add_argument(
        "-i", "--interactive", action="store_true", help="keep asking in a loop"
    )
    parser.add_argument(
        "--apply", action="store_true", help="write spec changes (default is a dry run)"
    )
    parser.add_argument("--summary-to", help="append a one-line summary to this file")
    args = parser.parse_args()

    if args.interactive or not args.message:
        return interactive(args.apply)

    reply, code = run(args.message, apply=args.apply)
    emit(reply, args.apply, sys.stdout.isatty())

    if args.summary_to and reply.summary:
        with open(args.summary_to, "a", encoding="utf-8") as fh:
            fh.write(reply.summary + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
