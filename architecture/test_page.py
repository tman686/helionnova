#!/usr/bin/env python3
"""Tests for the browsable page generator.

The page is published as an artifact, where a strict CSP blocks every external
host and the host supplies its own document skeleton. Both of those fail
silently — a blocked font just renders in a fallback, a nested <html> just
renders wrong — so they are asserted here rather than eyeballed.
"""

from __future__ import annotations

import re
import unittest

import yaml

import page
import scaffold

REAL_SPEC = yaml.safe_load(scaffold.SPEC.read_text(encoding="utf-8"))
FRAGMENT = page.build(REAL_SPEC)
STANDALONE = page.standalone(FRAGMENT)

EXTERNAL = re.compile(r'(?:src|href)\s*=\s*["\'](?:https?:)?//')


class SelfContained(unittest.TestCase):
    def test_no_external_resources(self):
        """The artifact CSP blocks every external host; a linked font would
        fall back silently rather than error."""
        for name, doc in (("fragment", FRAGMENT), ("standalone", STANDALONE)):
            self.assertIsNone(EXTERNAL.search(doc), name)

    def test_no_url_fetch_at_runtime(self):
        for call in ("fetch(", "XMLHttpRequest", "WebSocket(", "importScripts"):
            self.assertNotIn(call, FRAGMENT)


class DocumentShape(unittest.TestCase):
    def test_fragment_has_no_document_wrapper(self):
        """The artifact host wraps the file in its own skeleton; emitting one
        here would nest a document inside a body."""
        self.assertIsNone(re.search(r"<(html|head|body)\b", FRAGMENT))

    def test_fragment_carries_a_title(self):
        self.assertIn("<title>", FRAGMENT)

    def test_standalone_is_a_complete_document(self):
        self.assertTrue(STANDALONE.lstrip().lower().startswith("<!doctype html>"))
        self.assertLess(STANDALONE.index("</style>"), STANDALONE.index("<body>"))
        self.assertIn('<div class="wrap">', STANDALONE.split("<body>", 1)[1])

    def test_standalone_round_trip_keeps_every_component(self):
        self.assertEqual(
            len(re.findall(r'class="chip s-', STANDALONE)),
            scaffold.count_leaves(REAL_SPEC),
        )


class ContentMatchesTheSpec(unittest.TestCase):
    """The page states figures as fact, so they have to come from the spec."""

    def test_every_component_appears(self):
        for node in scaffold.leaves(REAL_SPEC):
            self.assertIn(f">{page.e(node['name'])}<", FRAGMENT, node["name"])

    def test_one_chip_per_component(self):
        self.assertEqual(
            len(re.findall(r'class="chip s-', FRAGMENT)),
            scaffold.count_leaves(REAL_SPEC),
        )

    def test_headline_figures_are_computed_not_typed(self):
        total = scaffold.count_leaves(REAL_SPEC)
        edges = len(scaffold.dependency_edges(REAL_SPEC))
        self.assertIn(f"<b>{total}</b><span>Components</span>", FRAGMENT)
        self.assertIn(f"<b>{edges}</b><span>Dependencies</span>", FRAGMENT)

    def test_status_legend_matches_the_rollup(self):
        counts = scaffold.status_rollup(REAL_SPEC, [])
        for status, n in counts.items():
            self.assertIn(f"{page.STATUS_LABEL[status]} {n}", FRAGMENT)

    def test_every_tier_gets_a_section_and_a_nav_entry(self):
        for domain in scaffold.children(REAL_SPEC):
            tiers = [c for c in scaffold.children(domain) if scaffold.children(c)] or [domain]
            for tier in tiers:
                slug = scaffold.slugify(tier["name"])
                self.assertIn(f'id="{slug}"', FRAGMENT, tier["name"])
                self.assertIn(f'href="#{slug}"', FRAGMENT, tier["name"])

    def test_aliases_are_searchable(self):
        node = next(n for n in scaffold.leaves(REAL_SPEC) if n.get("aliases"))
        self.assertIn(node["aliases"][0].lower(), FRAGMENT)


def hostile(name: str, where: str = "component") -> str:
    """Render a spec whose names carry characters that break HTML or mermaid.

    The real spec has no component name containing `&` or `<`, so asserting
    against it alone cannot prove the escaping works — it would pass even with
    escaping removed. These build the dangerous case on purpose.
    """
    component = {"name": name if where == "component" else "Alpha", "description": "d"}
    tier = {
        "name": name if where == "tier" else "Tier",
        "description": "d",
        "children": [component],
    }
    domain = {
        "name": name if where == "domain" else "Domain",
        "description": "d",
        "children": [tier],
    }
    return page.build({"name": "Universe", "description": "d", "children": [domain]})


class Escaping(unittest.TestCase):
    def test_ampersand_names_are_escaped_in_markup(self):
        self.assertIn("Identity &amp; Security", FRAGMENT)
        self.assertNotIn("<h2>Identity & Security</h2>", FRAGMENT)

    def test_component_names_are_escaped(self):
        out = hostile('Rate & Limit <script>alert("x")</script>')
        self.assertNotIn("<script>alert", out)
        self.assertIn("Rate &amp; Limit", out)

    def test_component_descriptions_are_escaped(self):
        spec = {
            "name": "Universe", "description": "d",
            "children": [{
                "name": "Domain", "description": "d",
                "children": [{
                    "name": "Tier", "description": "d",
                    "children": [{"name": "Alpha", "description": "a <b>bold</b> & risky claim"}],
                }],
            }],
        }
        out = page.build(spec)
        self.assertNotIn("<b>bold</b>", out)
        self.assertIn("&amp; risky", out)

    def test_tier_and_domain_names_are_escaped(self):
        for where in ("tier", "domain"):
            out = hostile("Risk & <em>Reward</em>", where)
            self.assertNotIn("<em>Reward</em>", out)

    def test_mermaid_source_avoids_the_bare_ampersand(self):
        """Inside <pre> the browser resolves entities before mermaid parses,
        so the diagram spells the word instead of relying on that."""
        diagram = re.search(r'<pre class="mermaid">(.*?)</pre>', FRAGMENT, re.S).group(1)
        self.assertNotIn("&", diagram)
        self.assertIn("Identity and Security", diagram)

    def test_mermaid_stays_ampersand_free_wherever_the_name_sits(self):
        for where in ("domain", "tier"):
            out = hostile("Risk & Reward", where)
            diagram = re.search(r'<pre class="mermaid">(.*?)</pre>', out, re.S).group(1)
            self.assertNotIn("&", diagram, where)
            self.assertIn("Risk and Reward", diagram, where)


class Accessibility(unittest.TestCase):
    def test_filter_input_is_labelled(self):
        self.assertIn('aria-label="Filter components"', FRAGMENT)

    def test_nav_is_labelled(self):
        self.assertIn('aria-label="Tiers"', FRAGMENT)

    def test_dependency_degree_is_not_symbols_only(self):
        self.assertIn("dependencies,", FRAGMENT)

    def test_focus_is_visible_and_motion_is_optional(self):
        self.assertIn(":focus-visible", FRAGMENT)
        self.assertIn("prefers-reduced-motion", FRAGMENT)

    def test_both_themes_are_defined(self):
        self.assertIn("prefers-color-scheme:dark", FRAGMENT)
        self.assertIn(':root[data-theme="dark"]', FRAGMENT)
        self.assertIn(':root[data-theme="light"]', FRAGMENT)

    def test_wide_content_scrolls_in_its_own_container(self):
        """A table this wide must not make the page body scroll sideways."""
        self.assertIn('<div class="scroll"><table>', FRAGMENT)
        self.assertIn("overflow-x:auto", FRAGMENT)


class ScriptWiring(unittest.TestCase):
    def test_script_targets_elements_that_exist(self):
        for element_id in re.findall(r"getElementById\('([^']+)'\)", FRAGMENT):
            self.assertIn(f'id="{element_id}"', FRAGMENT, element_id)

    def test_filter_reads_attributes_the_chips_carry(self):
        for attr in re.findall(r"dataset\.(\w+)", FRAGMENT):
            kebab = re.sub(r"(?<!^)(?=[A-Z])", "-", attr).lower()
            self.assertIn(f"data-{kebab}=", FRAGMENT, attr)


if __name__ == "__main__":
    unittest.main()
