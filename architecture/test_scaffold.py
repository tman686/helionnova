#!/usr/bin/env python3
"""Tests for the architecture scaffold generator.

    python3 -m unittest discover -s architecture -v

Stdlib only — CI already installs pyyaml for the generator itself, and the
tests should not add another dependency to a repo that has none.

The validator is the load-bearing part: it is the only thing standing between
a typo in universe.yaml and a silently corrupt tree, so every rule it claims
to enforce has a test that proves it fires.
"""

from __future__ import annotations

import copy
import re
import unittest
from pathlib import Path

import yaml

import scaffold

ROOT = Path(__file__).resolve().parent
REAL_SPEC = yaml.safe_load((ROOT / "universe.yaml").read_text(encoding="utf-8"))


def tiny(**overrides) -> dict:
    """A minimal valid spec: root -> domain -> tier -> two components."""
    spec = {
        "name": "Universe",
        "description": "root",
        "children": [
            {
                "name": "Domain",
                "description": "a domain",
                "owner": "core",
                "children": [
                    {
                        "name": "Tier",
                        "description": "a tier",
                        "children": [
                            {"name": "Alpha", "description": "first"},
                            {"name": "Beta", "description": "second"},
                        ],
                    }
                ],
            }
        ],
    }
    spec.update(overrides)
    return spec


def component(spec: dict, name: str) -> dict:
    for node, _ in scaffold.walk(spec):
        if node.get("name") == name:
            return node
    raise AssertionError(f"no component named {name}")


class Slugify(unittest.TestCase):
    def test_handles_the_awkward_names_in_the_real_spec(self):
        cases = {
            "HTTP/3 Gateway": "http-3-gateway",
            "Identity & Security": "identity-and-security",
            "Blue/Green Deployments": "blue-green-deployments",
            "Pub/Sub": "pub-sub",
            "SIEM": "siem",
            "Time-Series Database": "time-series-database",
            "CI": "ci",
        }
        for name, expected in cases.items():
            self.assertEqual(scaffold.slugify(name), expected, name)

    def test_never_produces_leading_or_trailing_separators(self):
        for node, _ in scaffold.walk(REAL_SPEC):
            slug = scaffold.slugify(node["name"])
            self.assertTrue(slug, node["name"])
            self.assertFalse(slug.startswith("-") or slug.endswith("-"), node["name"])


class ValidatorAcceptsGoodSpecs(unittest.TestCase):
    def test_tiny_spec_is_clean(self):
        self.assertEqual(scaffold.validate(tiny()), [])

    def test_the_real_spec_is_clean(self):
        self.assertEqual(scaffold.validate(REAL_SPEC), [])

    def test_alias_resolves_as_a_dependency_target(self):
        spec = tiny()
        component(spec, "Beta")["aliases"] = ["Old Beta"]
        component(spec, "Alpha")["depends_on"] = ["Old Beta"]
        self.assertEqual(scaffold.validate(spec), [])

    def test_a_diamond_is_not_mistaken_for_a_cycle(self):
        spec = tiny()
        component(spec, "Tier")["children"].append({"name": "Gamma", "description": "third"})
        component(spec, "Tier")["children"].append({"name": "Delta", "description": "fourth"})
        component(spec, "Alpha")["depends_on"] = ["Beta", "Gamma"]
        component(spec, "Beta")["depends_on"] = ["Delta"]
        component(spec, "Gamma")["depends_on"] = ["Delta"]
        self.assertEqual(scaffold.validate(spec), [])


class ValidatorRejectsBadSpecs(unittest.TestCase):
    """Each test asserts a specific rule fires. A rule with no test is a rule
    that can silently stop working."""

    def assert_rejects(self, spec: dict, fragment: str):
        errors = scaffold.validate(spec)
        self.assertTrue(errors, f"expected an error mentioning {fragment!r}, got none")
        self.assertTrue(
            any(fragment in e for e in errors),
            f"expected an error mentioning {fragment!r}, got: {errors}",
        )

    def test_missing_description(self):
        spec = tiny()
        del component(spec, "Alpha")["description"]
        self.assert_rejects(spec, "missing description")

    def test_blank_description_counts_as_missing(self):
        spec = tiny()
        component(spec, "Alpha")["description"] = "   \n  "
        self.assert_rejects(spec, "missing description")

    def test_unknown_status(self):
        spec = tiny()
        component(spec, "Alpha")["status"] = "shipped"
        self.assert_rejects(spec, "not one of")

    def test_owner_must_be_a_lowercase_slug(self):
        for bad in ["Platform-Edge", "platform edge", "platform_edge", "-leading"]:
            spec = tiny()
            component(spec, "Alpha")["owner"] = bad
            self.assert_rejects(spec, "not a lowercase team slug")

    def test_sibling_slug_collision(self):
        spec = tiny()
        component(spec, "Tier")["children"].append({"name": "alpha", "description": "clash"})
        self.assert_rejects(spec, "slug collides")

    def test_duplicate_component_name_anywhere(self):
        spec = tiny()
        spec["children"][0]["children"].append(
            {
                "name": "Other Tier",
                "description": "another",
                "children": [{"name": "Alpha", "description": "dupe"}],
            }
        )
        self.assert_rejects(spec, "duplicate component name")

    def test_alias_claimed_by_two_components(self):
        spec = tiny()
        component(spec, "Alpha")["aliases"] = ["Shared"]
        component(spec, "Beta")["aliases"] = ["Shared"]
        self.assert_rejects(spec, "claimed by both")

    def test_alias_shadowing_a_real_component(self):
        spec = tiny()
        component(spec, "Alpha")["aliases"] = ["Beta"]
        self.assert_rejects(spec, "shadows a real component")

    def test_dependency_on_unknown_component(self):
        spec = tiny()
        component(spec, "Alpha")["depends_on"] = ["Nonexistent"]
        self.assert_rejects(spec, "unknown component")

    def test_self_dependency(self):
        spec = tiny()
        component(spec, "Alpha")["depends_on"] = ["Alpha"]
        self.assert_rejects(spec, "depends on itself")

    def test_dependency_on_a_group(self):
        spec = tiny()
        component(spec, "Alpha")["depends_on"] = ["Tier"]
        self.assert_rejects(spec, "is a group not a component")

    def test_duplicate_dependency_entries(self):
        spec = tiny()
        component(spec, "Alpha")["depends_on"] = ["Beta", "beta"]
        self.assert_rejects(spec, "duplicate entries")

    def test_depends_on_declared_on_a_group(self):
        spec = tiny()
        component(spec, "Tier")["depends_on"] = ["Alpha"]
        self.assert_rejects(spec, "belongs on components, not groups")

    def test_two_component_cycle(self):
        spec = tiny()
        component(spec, "Alpha")["depends_on"] = ["Beta"]
        component(spec, "Beta")["depends_on"] = ["Alpha"]
        self.assert_rejects(spec, "dependency cycle")

    def test_longer_cycle(self):
        spec = tiny()
        component(spec, "Tier")["children"].append({"name": "Gamma", "description": "third"})
        component(spec, "Alpha")["depends_on"] = ["Beta"]
        component(spec, "Beta")["depends_on"] = ["Gamma"]
        component(spec, "Gamma")["depends_on"] = ["Alpha"]
        self.assert_rejects(spec, "dependency cycle")

    def test_a_cycle_is_reported_once_not_once_per_member(self):
        spec = tiny()
        component(spec, "Alpha")["depends_on"] = ["Beta"]
        component(spec, "Beta")["depends_on"] = ["Alpha"]
        cycles = scaffold.find_cycles(spec)
        self.assertEqual(len(cycles), 1, cycles)

    def test_a_cycle_reached_through_an_alias_is_still_caught(self):
        spec = tiny()
        component(spec, "Beta")["aliases"] = ["Old Beta"]
        component(spec, "Alpha")["depends_on"] = ["Old Beta"]
        component(spec, "Beta")["depends_on"] = ["Alpha"]
        self.assert_rejects(spec, "dependency cycle")


class Inheritance(unittest.TestCase):
    def test_owner_is_inherited_from_the_nearest_ancestor(self):
        spec = tiny()
        node, ancestors = scaffold.build_index(spec)["alpha"]
        self.assertEqual(scaffold.owner_of(node, ancestors), "core")

    def test_a_component_can_override_its_inherited_owner(self):
        spec = tiny()
        component(spec, "Alpha")["owner"] = "special"
        node, ancestors = scaffold.build_index(spec)["alpha"]
        self.assertEqual(scaffold.owner_of(node, ancestors), "special")

    def test_status_defaults_to_planned(self):
        spec = tiny()
        node, ancestors = scaffold.build_index(spec)["alpha"]
        self.assertEqual(scaffold.status_of(node, ancestors), "planned")

    def test_status_is_inherited_then_overridden(self):
        spec = tiny()
        component(spec, "Tier")["status"] = "building"
        component(spec, "Beta")["status"] = "running"
        index = scaffold.build_index(spec)
        self.assertEqual(scaffold.status_of(*index["alpha"]), "building")
        self.assertEqual(scaffold.status_of(*index["beta"]), "running")

    def test_rollup_counts_every_component_by_status(self):
        spec = tiny()
        component(spec, "Beta")["status"] = "running"
        counts = scaffold.status_rollup(spec, [])
        self.assertEqual(counts, {"planned": 1, "building": 0, "running": 1})

    def test_owner_falls_back_to_unassigned(self):
        spec = tiny()
        del spec["children"][0]["owner"]
        node, ancestors = scaffold.build_index(spec)["alpha"]
        self.assertEqual(scaffold.owner_of(node, ancestors), scaffold.DEFAULT_OWNER)


class Graph(unittest.TestCase):
    def test_index_resolves_names_and_aliases(self):
        spec = tiny()
        component(spec, "Beta")["aliases"] = ["Old Beta"]
        index = scaffold.build_index(spec)
        self.assertIs(index["beta"][0], index["old beta"][0])

    def test_edges_are_recorded_against_resolved_names(self):
        spec = tiny()
        component(spec, "Beta")["aliases"] = ["Old Beta"]
        component(spec, "Alpha")["depends_on"] = ["Old Beta"]
        self.assertEqual(scaffold.dependency_edges(spec), [("Alpha", "Beta")])

    def test_reverse_edges(self):
        spec = tiny()
        component(spec, "Alpha")["depends_on"] = ["Beta"]
        self.assertEqual(scaffold.dependents_map(spec), {"Beta": ["Alpha"]})

    def test_real_spec_has_no_cycles(self):
        self.assertEqual(scaffold.find_cycles(REAL_SPEC), [])

    def test_every_real_dependency_resolves(self):
        index = scaffold.build_index(REAL_SPEC)
        for node, _ in scaffold.walk(REAL_SPEC):
            for target in scaffold.depends_on(node):
                self.assertIn(target.lower(), index, f"{node['name']} -> {target}")


class Rendering(unittest.TestCase):
    def test_tree_starts_at_the_root_and_includes_every_node(self):
        lines = scaffold.render_tree(REAL_SPEC)
        self.assertEqual(lines[0], "Universe/")
        rendered = "\n".join(lines)
        for node, _ in scaffold.walk(REAL_SPEC):
            self.assertIn(node["name"], rendered)

    def test_mermaid_labels_escape_characters_that_break_the_parser(self):
        self.assertEqual(scaffold.mermaid_label("Identity & Security"), "Identity &amp; Security")
        self.assertEqual(scaffold.mermaid_label('a "quoted" one'), "a &quot;quoted&quot; one")
        self.assertEqual(scaffold.mermaid_label("issue #3"), "issue &#35;3")

    def test_mermaid_ids_are_valid_identifiers(self):
        for node, _ in scaffold.walk(REAL_SPEC):
            self.assertRegex(scaffold.mermaid_id(node["name"]), r"^n_[a-z0-9_]+$")

    def test_tier_abbreviations_do_not_collide(self):
        tiers = [
            tier["name"]
            for domain in REAL_SPEC["children"]
            for tier in scaffold.children(domain)
            if scaffold.children(tier)
        ]
        abbrs = [scaffold.abbreviate(t) for t in tiers]
        self.assertEqual(len(abbrs), len(set(abbrs)), dict(zip(tiers, abbrs)))

    def test_leaf_page_lists_both_directions_of_its_dependencies(self):
        spec = tiny()
        component(spec, "Alpha")["depends_on"] = ["Beta"]
        index = scaffold.build_index(spec)
        alpha = scaffold.readme_for(*index["alpha"], spec)
        beta = scaffold.readme_for(*index["beta"], spec)
        self.assertIn("**Depends on:** [Beta]", alpha)
        self.assertIn("**Depended on by:** [Alpha]", beta)
        self.assertIn("foundation", beta)


class GeneratedOutput(unittest.TestCase):
    """Checks against the tree already on disk. `scaffold.py --check` proves it
    is current; these prove it is not internally broken."""

    def test_every_component_has_a_directory_with_a_readme(self):
        for node, ancestors in scaffold.walk(REAL_SPEC):
            if not ancestors:
                continue
            path = scaffold.TREE_DIR.joinpath(
                *[scaffold.slugify(a["name"]) for a in ancestors[1:]],
                scaffold.slugify(node["name"]),
                "README.md",
            )
            self.assertTrue(path.exists(), path)

    def test_no_generated_link_dangles(self):
        broken = []
        for md in list(scaffold.ROOT.rglob("*.md")):
            for target in re.findall(r"\]\((\.{1,2}/[^)]+)\)", md.read_text(encoding="utf-8")):
                if not (md.parent / target).resolve().exists():
                    broken.append(f"{md}: {target}")
        self.assertEqual(broken, [])

    def test_generated_files_carry_the_do_not_edit_marker(self):
        for md in list(scaffold.TREE_DIR.rglob("*.md")) + [scaffold.DOC]:
            self.assertTrue(
                md.read_text(encoding="utf-8").startswith("<!-- Generated by"), md
            )


if __name__ == "__main__":
    unittest.main()
