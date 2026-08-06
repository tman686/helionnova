#!/usr/bin/env python3
"""Render universe.yaml as a self-contained browsable page.

    python3 architecture/page.py                      # artifact-ready fragment
    python3 architecture/page.py --standalone -o x.html   # openable in a browser

Output is NOT committed — regenerate it when you want a current view. The
default output is a fragment (a <title>, styles, markup, script) with no
<html>/<head>/<body> wrapper, because the artifact host supplies that skeleton
and a nested document would break it. Pass --standalone to get a complete
document you can open locally.

Every figure on the page is computed from the spec, so the page cannot drift
from the tree it claims to describe.

Design follows the palette already established in the repository's index.html:
plasma against a warm near-black, teal as the cool counterpoint, monospace for
instrument labels. Fonts are system stacks on purpose — the site's webfonts are
CDN-hosted and the artifact CSP blocks them, so linking them would silently
fall back to something arbitrary.
"""

from __future__ import annotations

import argparse
import html
from pathlib import Path

import yaml

import scaffold

STATUS_LABEL = {"planned": "Planned", "building": "Building", "running": "Running"}
SKELETON = (
    '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
    "{head}\n</head>\n<body>\n{body}\n</body>\n</html>\n"
)

e = html.escape


def mermaid_text(name: str) -> str:
    """Make a name safe to sit inside `<pre class="mermaid">`.

    That block is HTML-parsed before mermaid ever sees it, so a name containing
    `<` would inject real markup. Order matters: drop the ampersand first (a
    bare `&` confuses the mermaid parser, and an escaped one resolves back to a
    bare `&` inside the pre), swap double quotes so they cannot close mermaid's
    own label quoting, then escape the angle brackets.
    """
    safe = name.replace("&", "and").replace('"', "'")
    return html.escape(safe, quote=False)


def build(spec: dict) -> str:
    index = scaffold.build_index(spec)
    reverse = scaffold.dependents_map(spec)
    edges = scaffold.dependency_edges(spec)
    total = scaffold.count_leaves(spec)
    counts = scaffold.status_rollup(spec, [])
    foundations = total - sum(1 for n in scaffold.leaves(spec) if scaffold.depends_on(n))

    # (domain, group) pairs that actually hold components.
    groups = []
    for domain in scaffold.children(spec):
        tiers = [c for c in scaffold.children(domain) if scaffold.children(c)]
        groups += [(domain, t) for t in tiers] or [(domain, domain)]

    # ---- tier map. "and" rather than "&" so no HTML entity round-trip can
    # reach the mermaid parser in an unexpected form.
    lines = ["flowchart TD", '    U(["Universe"])']
    for domain in scaffold.children(spec):
        did = scaffold.mermaid_id(domain["name"])
        lines.append(
            f'    U --> {did}["{mermaid_text(domain["name"])}'
            f'<br/>{scaffold.count_leaves(domain)} components"]'
        )
        for tier in [c for c in scaffold.children(domain) if scaffold.children(c)]:
            tid = scaffold.mermaid_id(tier["name"])
            lines.append(
                f'    {did} --> {tid}["{mermaid_text(tier["name"])}'
                f'<br/>{scaffold.count_leaves(tier)}"]'
            )
    diagram = "\n".join(lines)

    # ---- cross-tier coupling
    pair: dict[tuple[str, str], int] = {}
    for src, tgt in edges:
        s = scaffold.tier_of(index[src.lower()][1])
        t = scaffold.tier_of(index[tgt.lower()][1])
        if s != t:
            pair[(s, t)] = pair.get((s, t), 0) + 1
    tier_names = sorted({t for p in pair for t in p})

    ranked = sorted(reverse.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:14]
    widest = len(ranked[0][1]) if ranked else 1

    def chip(node: dict, ancestors: list[dict]) -> str:
        name = node["name"]
        status = scaffold.status_of(node, ancestors)
        dependents = len(reverse.get(name, []))
        upstreams = len(scaffold.depends_on(node))
        aka = node.get("aliases") or []
        tip = f"{name}. {node.get('description', '')}"
        if aka:
            tip += f" Also known as: {', '.join(aka)}."
        return (
            f'<li class="chip s-{status}" data-name="{e(name.lower())}" '
            f'data-aka="{e(" ".join(a.lower() for a in aka))}" tabindex="0" title="{e(tip)}">'
            f'<span class="dot" aria-hidden="true"></span>'
            f'<span class="chip-name">{e(name)}</span>'
            f'<span class="deg" aria-label="{upstreams} dependencies, {dependents} dependents">'
            f'<span class="out">&darr;{upstreams}</span>'
            f'<span class="in">&uarr;{dependents}</span></span>'
            f'<span class="chip-desc">{e(scaffold.clean(node.get("description", "")))}</span>'
            "</li>"
        )

    nav, sections = [], []
    for domain, group in groups:
        slug = scaffold.slugify(group["name"])
        ancestors = [spec, domain] if group is not domain else [spec]
        kids = scaffold.children(group)
        roll = scaffold.status_rollup(group, ancestors)
        nav.append(
            f'<a href="#{slug}"><span>{e(group["name"])}</span>'
            f'<span class="n">{len(kids)}</span></a>'
        )
        sections.append(
            f'<section id="{slug}" class="tier"><header class="tier-head">'
            f'<p class="eyebrow">{e(domain["name"]) if group is not domain else "Domain"}</p>'
            f'<h2>{e(group["name"])}</h2>'
            f'<p class="tier-desc">{e(scaffold.clean(group.get("description", "")))}</p>'
            f'<p class="meta"><span class="owner">'
            f'{e(scaffold.owner_of(group, ancestors))}</span>'
            f'<span class="sep">&middot;</span>{len(kids)} components'
            f'<span class="sep">&middot;</span>{scaffold.rollup_text(roll)}</p></header>'
            f'<ul class="grid">'
            + "".join(chip(k, ancestors + [group]) for k in kids)
            + "</ul></section>"
        )

    bars = "".join(
        f'<li><span class="bl">{e(n)}</span><span class="bt">'
        f'<span class="bf" style="width:{100 * len(d) / widest:.1f}%"></span></span>'
        f'<span class="bv">{len(d)}</span></li>'
        for n, d in ranked
    )
    head_cells = "".join(f"<th>{e(scaffold.abbreviate(t))}</th>" for t in tier_names)
    rows = "".join(
        f"<tr><th>{e(t)}</th>"
        + "".join(
            f'<td class="v">{pair[(t, o)]}</td>' if (t, o) in pair else '<td class="z">&middot;</td>'
            for o in tier_names
        )
        + "</tr>"
        for t in tier_names
    )
    legend = "".join(
        f'<span class="lg s-{k}"><span class="dot"></span>{STATUS_LABEL[k]} {v}</span>'
        for k, v in counts.items()
    )

    return TEMPLATE.format(
        total=total,
        tiers=len(groups),
        edges=len(edges),
        foundations=foundations,
        legend=legend,
        nav="".join(nav),
        diagram=diagram,
        bars=bars,
        head_cells=head_cells,
        rows=rows,
        sections="".join(sections),
    )


TEMPLATE = """<title>Universe — Helion Nova platform architecture</title>
<style>
:root {{
  --plasma:#ff3a1a; --plasma-2:#ff6b35; --amber:#ffaa44; --teal:#00b8a4;
  --ground:#faf6f4; --surface:#fff; --raised:#fdfaf9;
  --ink:#1a0d10; --muted:#6f5659; --faint:#a89194; --line:#e8dcda;
  --shadow:0 1px 2px rgba(26,13,16,.06),0 8px 24px -12px rgba(26,13,16,.14);
  --sans:system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
}}
@media (prefers-color-scheme:dark){{
  :root{{ --ground:#0a0507; --surface:#150a0e; --raised:#1c0e13;
    --ink:#fff5f0; --muted:#a8888a; --faint:#6b4f52; --line:#2b171d;
    --shadow:0 1px 2px rgba(0,0,0,.5),0 8px 28px -14px rgba(255,58,26,.22); }}
}}
:root[data-theme="dark"]{{ --ground:#0a0507; --surface:#150a0e; --raised:#1c0e13;
  --ink:#fff5f0; --muted:#a8888a; --faint:#6b4f52; --line:#2b171d;
  --shadow:0 1px 2px rgba(0,0,0,.5),0 8px 28px -14px rgba(255,58,26,.22); }}
:root[data-theme="light"]{{ --ground:#faf6f4; --surface:#fff; --raised:#fdfaf9;
  --ink:#1a0d10; --muted:#6f5659; --faint:#a89194; --line:#e8dcda;
  --shadow:0 1px 2px rgba(26,13,16,.06),0 8px 24px -12px rgba(26,13,16,.14); }}

*{{box-sizing:border-box}}
body{{margin:0;background:var(--ground);color:var(--ink);font-family:var(--sans);
  line-height:1.6;-webkit-font-smoothing:antialiased}}
.wrap{{display:grid;grid-template-columns:210px minmax(0,1fr);gap:48px;
  max-width:1180px;margin:0 auto;padding:0 28px 96px}}
@media (max-width:900px){{ .wrap{{grid-template-columns:1fr;gap:0}} .rail{{display:none}} }}

.eyebrow{{font-family:var(--mono);font-size:.62rem;letter-spacing:.18em;
  text-transform:uppercase;color:var(--plasma);margin:0 0 10px}}
h1,h2,h3{{text-wrap:balance;letter-spacing:-.022em;font-weight:800;margin:0}}

.hero{{grid-column:1/-1;padding:76px 0 40px;border-bottom:1px solid var(--line);
  margin-bottom:44px;position:relative;overflow:hidden}}
.hero::after{{content:"";position:absolute;right:-140px;top:-90px;width:420px;height:420px;
  border-radius:50%;pointer-events:none;
  background:radial-gradient(circle,rgba(255,58,26,.16),rgba(255,58,26,.03) 55%,transparent 72%)}}
h1{{font-size:clamp(2.3rem,5.2vw,3.7rem);line-height:1.02}}
h1 em{{font-style:normal;color:var(--plasma)}}
.lede{{color:var(--muted);max-width:60ch;margin:18px 0 0;font-size:1.03rem}}

.readout{{display:flex;flex-wrap:wrap;gap:12px;margin-top:30px}}
.stat{{background:var(--surface);border:1px solid var(--line);border-radius:3px;
  padding:13px 20px;min-width:104px;box-shadow:var(--shadow)}}
.stat b{{display:block;font-size:1.72rem;line-height:1;letter-spacing:-.03em;
  font-variant-numeric:tabular-nums}}
.stat span{{font-family:var(--mono);font-size:.6rem;letter-spacing:.15em;
  text-transform:uppercase;color:var(--muted)}}
.stat.hot b{{color:var(--plasma)}}

.legend{{display:flex;flex-wrap:wrap;gap:18px;margin-top:22px;font-family:var(--mono);
  font-size:.64rem;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}}
.lg{{display:flex;align-items:center;gap:7px}}
.dot{{width:7px;height:7px;border-radius:50%;background:var(--faint);flex:none}}
.s-building .dot{{background:var(--amber)}}
.s-running .dot{{background:var(--teal)}}

.rail{{position:sticky;top:0;align-self:start;max-height:100vh;overflow-y:auto;
  padding:28px 0;font-size:.83rem}}
.rail a{{display:flex;justify-content:space-between;gap:8px;padding:5px 10px;
  color:var(--muted);text-decoration:none;border-left:2px solid transparent;
  border-radius:0 2px 2px 0}}
.rail a:hover,.rail a:focus-visible{{color:var(--ink);background:var(--raised);
  border-left-color:var(--plasma)}}
.rail .n{{font-family:var(--mono);font-size:.68rem;color:var(--faint);
  font-variant-numeric:tabular-nums}}

.panel{{background:var(--surface);border:1px solid var(--line);border-radius:4px;
  padding:26px;margin-bottom:44px;box-shadow:var(--shadow)}}
.panel h3{{font-size:1.12rem;margin-bottom:6px}}
.panel p.note{{color:var(--muted);font-size:.88rem;margin:0 0 20px;max-width:64ch}}
.scroll{{overflow-x:auto}}
pre.mermaid{{background:none;border:0;text-align:center;margin:0}}

.bars{{list-style:none;margin:0;padding:0;display:grid;gap:7px}}
.bars li{{display:grid;grid-template-columns:minmax(120px,1.1fr) minmax(0,3fr) 34px;
  gap:12px;align-items:center;font-size:.85rem}}
.bl{{color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.bt{{background:var(--raised);border:1px solid var(--line);height:15px;border-radius:2px;
  overflow:hidden}}
.bf{{display:block;height:100%;background:linear-gradient(90deg,var(--plasma),var(--plasma-2))}}
.bv{{font-family:var(--mono);font-size:.74rem;text-align:right;
  font-variant-numeric:tabular-nums;color:var(--ink)}}

table{{border-collapse:collapse;font-size:.8rem;width:100%}}
th,td{{border:1px solid var(--line);padding:6px 9px;text-align:right;
  font-variant-numeric:tabular-nums}}
thead th{{font-family:var(--mono);font-size:.62rem;letter-spacing:.09em;
  color:var(--muted);font-weight:500}}
tbody th{{text-align:left;font-weight:500;color:var(--muted);white-space:nowrap}}
td.v{{color:var(--plasma);font-weight:700}}
td.z{{color:var(--faint)}}

.tier{{margin-bottom:60px;scroll-margin-top:20px}}
.tier-head h2{{font-size:1.6rem}}
.tier-desc{{color:var(--muted);max-width:66ch;margin:9px 0 0;font-size:.94rem}}
.meta{{font-family:var(--mono);font-size:.63rem;letter-spacing:.11em;
  text-transform:uppercase;color:var(--faint);margin:12px 0 0}}
.owner{{color:var(--teal)}}
.sep{{margin:0 9px;opacity:.5}}

.grid{{list-style:none;margin:22px 0 0;padding:0;display:grid;gap:9px;
  grid-template-columns:repeat(auto-fill,minmax(238px,1fr))}}
.chip{{background:var(--surface);border:1px solid var(--line);
  border-left:2px solid var(--faint);border-radius:3px;padding:11px 13px;display:grid;
  grid-template-columns:auto 1fr auto;grid-template-areas:"d n g" ". s s";
  gap:2px 8px;align-items:center;transition:border-color .16s,transform .16s}}
@media (prefers-reduced-motion:reduce){{ .chip{{transition:none}} }}
.chip:hover,.chip:focus-visible{{border-color:var(--plasma);transform:translateY(-1px);
  outline:none}}
.chip:focus-visible{{box-shadow:0 0 0 2px var(--plasma)}}
.chip.s-building{{border-left-color:var(--amber)}}
.chip.s-running{{border-left-color:var(--teal)}}
.chip .dot{{grid-area:d}}
.chip-name{{grid-area:n;font-weight:650;font-size:.9rem;letter-spacing:-.01em}}
.deg{{grid-area:g;font-family:var(--mono);font-size:.62rem;color:var(--faint);
  display:flex;gap:6px;font-variant-numeric:tabular-nums}}
.deg .in{{color:var(--plasma)}}
.chip-desc{{grid-area:s;color:var(--muted);font-size:.76rem;line-height:1.45;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}}
.chip[hidden]{{display:none}}

.filter{{position:sticky;top:0;z-index:5;background:var(--ground);padding:14px 0;
  margin-bottom:8px;border-bottom:1px solid var(--line)}}
.filter input{{width:100%;max-width:340px;font-family:var(--mono);font-size:.78rem;
  padding:9px 12px;border:1px solid var(--line);border-radius:3px;
  background:var(--surface);color:var(--ink)}}
.filter input:focus{{outline:2px solid var(--plasma);outline-offset:1px;
  border-color:transparent}}
.filter .count{{font-family:var(--mono);font-size:.64rem;letter-spacing:.12em;
  text-transform:uppercase;color:var(--muted);margin-left:14px}}
footer{{grid-column:1/-1;border-top:1px solid var(--line);padding-top:22px;
  color:var(--faint);font-size:.78rem;max-width:70ch}}
</style>

<div class="wrap">
  <header class="hero">
    <p class="eyebrow">Helion Nova &middot; Platform architecture</p>
    <h1>The <em>Universe</em> tree</h1>
    <p class="lede">Every server, service, and tier the platform is planned to run —
      {total} components across {tiers} tiers, wired into a {edges}-edge dependency
      graph. This is a map, not an inventory: a component appearing here does not
      mean it is running.</p>
    <div class="readout">
      <div class="stat"><b>{total}</b><span>Components</span></div>
      <div class="stat"><b>{tiers}</b><span>Tiers</span></div>
      <div class="stat hot"><b>{edges}</b><span>Dependencies</span></div>
      <div class="stat"><b>{foundations}</b><span>Foundations</span></div>
      <div class="stat"><b>0</b><span>Cycles</span></div>
    </div>
    <div class="legend">{legend}</div>
  </header>

  <nav class="rail" aria-label="Tiers">{nav}</nav>

  <main>
    <div class="panel">
      <h3>Shape of the tree</h3>
      <p class="note">Tier level only. The full depth lives in the repository — all
        {total} components on one canvas is unreadable.</p>
      <div class="scroll"><pre class="mermaid">{diagram}</pre></div>
    </div>

    <div class="panel">
      <h3>Load-bearing components</h3>
      <p class="note">Counted by how many other components declare a dependency on
        them. An outage in these fans out furthest.</p>
      <ul class="bars">{bars}</ul>
    </div>

    <div class="panel">
      <h3>Coupling between tiers</h3>
      <p class="note">Cross-tier edges only; rows depend on columns. Columns run in the
        same order as the rows, so <code>DT</code> is Data Tier. Edges inside a tier
        are omitted.</p>
      <div class="scroll"><table><thead><tr><th></th>{head_cells}</tr></thead>
        <tbody>{rows}</tbody></table></div>
    </div>

    <div class="filter">
      <input id="q" type="search" placeholder="Filter components…"
        aria-label="Filter components">
      <span class="count" id="count"></span>
    </div>
    {sections}
  </main>

  <footer>Each component shows its dependency degree: &darr; what it needs,
    &uarr; what needs it. Owners are placeholder team slugs pending real ones.
    Generated from <code>architecture/universe.yaml</code>.</footer>
</div>

<script>
const q=document.getElementById('q'),cnt=document.getElementById('count'),
      chips=[...document.querySelectorAll('.chip')],
      secs=[...document.querySelectorAll('.tier')];
function apply(){{
  const t=q.value.trim().toLowerCase();
  let n=0;
  chips.forEach(c=>{{
    const hit=!t||c.dataset.name.includes(t)||c.dataset.aka.includes(t);
    c.hidden=!hit; if(hit)n++;
  }});
  secs.forEach(s=>{{s.hidden=![...s.querySelectorAll('.chip')].some(c=>!c.hidden);}});
  cnt.textContent=t?n+' of {total} shown':'{total} components';
}}
q.addEventListener('input',apply);apply();
</script>
"""


def standalone(fragment: str) -> str:
    """Split the fragment's head-level tags out into a real document."""
    marker = "</style>"
    head, _, body = fragment.partition(marker)
    return SKELETON.format(head=head + marker, body=body.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--out", default="architecture.html", help="output path")
    parser.add_argument(
        "--standalone",
        action="store_true",
        help="emit a complete HTML document instead of an artifact-ready fragment",
    )
    args = parser.parse_args()

    spec = yaml.safe_load((scaffold.SPEC).read_text(encoding="utf-8"))
    errors = scaffold.validate(spec)
    if errors:
        print(f"{scaffold.SPEC.name} is invalid; fix it first:", *errors, sep="\n  - ")
        return 1

    page = build(spec)
    if args.standalone:
        page = standalone(page)

    out = Path(args.out)
    out.write_text(page, encoding="utf-8")
    print(f"wrote {out} ({len(page) / 1024:.0f} KB, {scaffold.count_leaves(spec)} components)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
