#!/usr/bin/env python3
"""Tests for the plain-English command interpreter.

The interpreter can commit to the repository, so the tests that matter most
are the ones proving it refuses: an edit that would break the tree must leave
the spec untouched, and a question must never write at all.
"""

from __future__ import annotations

import copy
import sys
import unittest

import yaml

import command
import scaffold

REAL_SPEC = yaml.safe_load(scaffold.SPEC.read_text(encoding="utf-8"))


def fresh() -> dict:
    return copy.deepcopy(REAL_SPEC)


def ask(message: str, spec: dict | None = None) -> command.Reply:
    return command.dispatch(spec if spec is not None else fresh(), message)


class Routing(unittest.TestCase):
    def test_phrasings_that_should_reach_the_same_command(self):
        for message in (
            "what depends on object storage",
            "who depends on Object Storage",
            "who needs object storage",
            "dependents of object storage",
            "dependents object storage",
        ):
            self.assertIn("depend on **Object Storage**", ask(message).text, message)

    def test_upstream_phrasings(self):
        for message in (
            "what does rag need",
            "what does RAG depend on",
            "dependencies of rag",
            "upstreams of rag",
        ):
            self.assertIn("**RAG** needs", ask(message).text, message)

    def test_asking_what_is_in_a_tier(self):
        for message in ("what's in messaging", "whats in messaging",
                        "what is in the messaging", "show messaging"):
            self.assertIn("**Messaging** (12 components", ask(message).text, message)

    def test_every_example_in_the_client_help_is_understood(self):
        """The help text is the first thing anyone copies, so nothing in it
        may be a phrasing the interpreter does not actually know."""
        import pathlib

        usage = (pathlib.Path(__file__).resolve().parent.parent / "bin" / "hn").read_text()
        block = usage.split("USAGE", 1)[1].split("USAGE", 1)[0]
        # Examples are the indented "  hn ..." lines; the title starts at column 0.
        examples = [
            line.strip()[3:].strip().strip('"')
            for line in block.splitlines()
            if line.startswith("  hn ") and not line.strip().startswith("hn --")
        ]
        self.assertGreater(len(examples), 5, "help text stopped listing examples")
        for message in examples:
            self.assertTrue(ask(message)._handled, f"help offers {message!r} but it is unknown")

    def test_case_and_punctuation_are_forgiven(self):
        self.assertIn("Object Storage", ask("WHAT DEPENDS ON object storage?").text)

    def test_aliases_resolve(self):
        """An old name still finds the component that superseded it."""
        self.assertIn("**Metrics**", ask("what does metrics server need").text)

    def test_unknown_message_is_reported_as_unhandled(self):
        reply = ask("please make me a sandwich")
        self.assertFalse(reply.ok)
        self.assertFalse(reply._handled)
        self.assertIn("did not understand", reply.text)

    def test_help_lists_something(self):
        self.assertIn("status", ask("help").text)


class Questions(unittest.TestCase):
    """Reads must never report a change."""

    def test_questions_do_not_mutate(self):
        for message in ("status", "tiers", "help", "find kafka", "show messaging",
                        "what depends on metrics", "what does rag need"):
            spec = fresh()
            reply = command.dispatch(spec, message)
            self.assertFalse(reply.changed, message)
            self.assertEqual(spec, REAL_SPEC, message)

    def test_status_counts_match_the_spec(self):
        text = ask("status").text
        self.assertIn(f"**{scaffold.count_leaves(REAL_SPEC)} components**", text)
        self.assertIn(f"{len(scaffold.dependency_edges(REAL_SPEC))} dependency edges", text)

    def test_tiers_excludes_domains_that_only_hold_tiers(self):
        text = ask("tiers").text
        self.assertIn("Gateway Tier", text)
        self.assertNotIn("Server Infrastructure", text)

    def test_missing_component_suggests_alternatives(self):
        reply = ask("what depends on object stroage")
        self.assertFalse(reply.ok)
        self.assertIn("Object Storage", reply.text)

    def test_find_searches_descriptions_too(self):
        self.assertTrue(ask("find robots").ok)


class GraphQuestions(unittest.TestCase):
    """The point of a dependency graph is the questions one hop cannot answer."""

    def test_blast_radius_exceeds_direct_dependents(self):
        text = ask("what breaks if object storage fails").text
        direct = len(scaffold.dependents_map(REAL_SPEC)["Object Storage"])
        total = len(scaffold.reachable(
            scaffold.adjacency(REAL_SPEC, reverse=True), "Object Storage"))
        self.assertGreater(total, direct, "transitive reach should exceed one hop")
        self.assertIn(f"**{total} of", text)

    def test_failure_verbs_are_not_swallowed_into_the_name(self):
        for message in (
            "what breaks if object storage fails",
            "what happens if object storage goes down",
            "what breaks without object storage",
            "blast radius of object storage",
        ):
            self.assertIn("**Object Storage**", ask(message).text, message)

    def test_transitive_needs_exceed_direct(self):
        text = ask("everything rag needs").text
        direct = len(scaffold.depends_on(command.find(REAL_SPEC, "rag")[0]))
        total = len(scaffold.reachable(scaffold.adjacency(REAL_SPEC), "RAG"))
        self.assertGreater(total, direct)
        self.assertIn(f"**{total}** components", text)

    def test_why_shows_a_real_path(self):
        text = ask("why does rag need object storage").text
        self.assertIn("→", text)
        self.assertIn("**RAG**", text)
        self.assertIn("**Object Storage**", text)

    def test_why_explains_when_the_arrow_points_the_other_way(self):
        reply = ask("why does object storage need rag")
        self.assertFalse(reply.ok)
        self.assertIn("other way round", reply.text)

    def test_why_says_so_when_unrelated(self):
        reply = ask("why does zone manager need kafka cluster")
        self.assertFalse(reply.ok)

    def test_build_order_is_a_valid_topological_layering(self):
        layers = scaffold.build_layers(REAL_SPEC)
        placed = {name: i for i, layer in enumerate(layers) for name in layer}
        self.assertEqual(len(placed), scaffold.count_leaves(REAL_SPEC))
        graph = scaffold.adjacency(REAL_SPEC)
        for name, level in placed.items():
            for dep in graph[name]:
                self.assertLess(placed[dep], level, f"{name} before its dependency {dep}")

    def test_layer_zero_is_exactly_the_foundations(self):
        layers = scaffold.build_layers(REAL_SPEC)
        foundations = {n["name"] for n in scaffold.leaves(REAL_SPEC)
                       if not scaffold.depends_on(n)}
        self.assertEqual(set(layers[0]), foundations)

    def test_about_card_carries_the_essentials(self):
        text = ask("about inference").text
        for fragment in ("### Inference", "**tier**", "**status**", "**owner**",
                         "**needs**", "**needed by**"):
            self.assertIn(fragment, text)

    def test_check_reports_a_clean_spec(self):
        self.assertTrue(ask("check").ok)

    def test_shortest_path_is_actually_shortest(self):
        graph = scaffold.adjacency(REAL_SPEC)
        path = scaffold.shortest_path(graph, "RAG", "Object Storage")
        self.assertIsNotNone(path)
        for a, b in zip(path, path[1:]):
            self.assertIn(b, graph[a], f"{a} -> {b} is not a real edge")

    def test_reachable_excludes_the_starting_node(self):
        self.assertNotIn(
            "Metrics", scaffold.reachable(scaffold.adjacency(REAL_SPEC), "Metrics"))


class Removal(unittest.TestCase):
    def test_removing_something_depended_on_is_refused(self):
        reply = ask("remove object storage")
        self.assertFalse(reply.ok)
        self.assertFalse(reply.changed)
        self.assertIn("depend on it", reply.text)

    def test_an_orphan_can_be_removed(self):
        spec = fresh()
        reply = command.dispatch(spec, "remove api explorer")
        self.assertTrue(reply.changed, reply.text)
        self.assertIsNone(command.find(spec, "api explorer"))
        self.assertEqual(scaffold.validate(spec), [])

    def test_removing_a_tier_is_refused(self):
        self.assertFalse(ask("remove data tier").ok)

    def test_unlink_removes_one_edge(self):
        spec = fresh()
        reply = command.dispatch(spec, "make rag no longer depend on web search")
        self.assertTrue(reply.changed)
        self.assertNotIn("Web Search", scaffold.depends_on(command.find(spec, "rag")[0]))
        self.assertEqual(scaffold.validate(spec), [])

    def test_unlinking_an_edge_that_is_not_there_is_refused(self):
        self.assertFalse(ask("unlink rag from zone manager").ok)


class Edits(unittest.TestCase):
    def test_mark_changes_status(self):
        spec = fresh()
        reply = command.dispatch(spec, "mark inference as building")
        self.assertTrue(reply.changed)
        self.assertEqual(command.find(spec, "inference")[0]["status"], "building")

    def test_marking_to_the_same_status_is_not_a_change(self):
        reply = ask("mark inference as planned")
        self.assertFalse(reply.changed)

    def test_add_component(self):
        spec = fresh()
        reply = command.dispatch(spec, "add Redis Sentinel to Data Tier: Promotes replicas.")
        self.assertTrue(reply.changed)
        node = command.find(spec, "redis sentinel")[0]
        self.assertEqual(node["description"], "Promotes replicas.")

    def test_add_without_a_description_gets_a_placeholder_and_says_so(self):
        spec = fresh()
        reply = command.dispatch(spec, "add Redis Sentinel to Data Tier")
        self.assertTrue(reply.changed)
        self.assertIn("placeholder", reply.text)
        self.assertEqual(scaffold.validate(spec), [])

    def test_add_to_a_tier_that_does_not_hold_components_is_refused(self):
        reply = ask("add Thing to Server Infrastructure")
        self.assertFalse(reply.ok)
        self.assertFalse(reply.changed)

    def test_dependency_can_be_added_either_phrasing(self):
        for message in ("make web search depend on metrics", "web search depends on metrics"):
            spec = fresh()
            reply = command.dispatch(spec, message)
            self.assertTrue(reply.changed, message)
            self.assertIn("Metrics", scaffold.depends_on(command.find(spec, "web search")[0]))

    def test_owner_can_be_set(self):
        spec = fresh()
        reply = command.dispatch(spec, "set owner of Data Tier to storage-crew")
        self.assertTrue(reply.changed)
        self.assertEqual(command.find_group(spec, "data tier")[0]["owner"], "storage-crew")


class RefusesToBreakTheTree(unittest.TestCase):
    """The interpreter validates the whole spec before writing. These prove it."""

    def assert_invalid_after(self, message: str):
        spec = fresh()
        reply = command.dispatch(spec, message)
        self.assertTrue(reply.changed, f"{message} should have attempted a change")
        self.assertTrue(scaffold.validate(spec), f"{message} should have broken validation")

    def test_a_dependency_cycle_is_caught_by_validation(self):
        # Object Storage -> Key Management already exists, so the reverse closes a loop.
        self.assert_invalid_after("make key management depend on object storage")

    def test_a_duplicate_name_is_caught_by_validation(self):
        spec = fresh()
        # `add` refuses outright before validation ever runs.
        reply = command.dispatch(spec, "add Metrics to Data Tier: dupe")
        self.assertFalse(reply.ok)
        self.assertFalse(reply.changed)

    def test_self_dependency_is_caught_by_validation(self):
        self.assert_invalid_after("make metrics depend on metrics")

    def test_depending_on_a_tier_is_refused(self):
        reply = ask("make metrics depend on Data Tier")
        self.assertFalse(reply.ok)
        self.assertFalse(reply.changed)

    def test_marking_a_tier_is_refused(self):
        reply = ask("mark Data Tier as running")
        self.assertFalse(reply.ok)
        self.assertFalse(reply.changed)


class ReadPathNeedsOnlyPyyaml(unittest.TestCase):
    """ruamel is a writing dependency and CI does not install it.

    A dry run that imports it fails only in CI, never on a machine where it
    happens to be present — which is exactly how it got through the first time.
    These tests make the absence explicit instead of relying on the runner.
    """

    def setUp(self):
        import builtins

        self._real_import = builtins.__import__

        def blocked(name, *args, **kwargs):
            if name.split(".")[0] == "ruamel":
                raise ModuleNotFoundError("No module named 'ruamel'")
            return self._real_import(name, *args, **kwargs)

        builtins.__import__ = blocked
        for mod in [m for m in list(sys.modules) if m.split(".")[0] == "ruamel"]:
            del sys.modules[mod]

    def tearDown(self):
        import builtins

        builtins.__import__ = self._real_import

    def test_questions_work_without_ruamel(self):
        for message in ("status", "tiers", "find kafka", "what depends on metrics"):
            self.assertEqual(command.run(message)[1], 0, message)

    def test_dry_run_edits_work_without_ruamel(self):
        reply, code = command.run("mark inference as building", apply=False)
        self.assertEqual(code, 0)
        self.assertTrue(reply.changed)

    def test_rejected_edits_work_without_ruamel(self):
        _, code = command.run("make key management depend on object storage", apply=False)
        self.assertEqual(code, 1)

    def test_unknown_messages_work_without_ruamel(self):
        self.assertEqual(command.run("please make me a sandwich")[1], 2)


def _has_ruamel() -> bool:
    try:
        import ruamel.yaml  # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


class MinimalDiffs(unittest.TestCase):
    """A one-line command must produce a one-line diff.

    Re-wrapping a folded scalar leaves a trailing space before the break, so an
    untidied round-trip rewrites ~140 lines and buries the real edit in
    whitespace. Commits from the command workflow would be unreviewable.
    """

    def test_tidy_strips_trailing_whitespace(self):
        self.assertEqual(command.tidy("a: 1   \nb: 2\t\n"), "a: 1\nb: 2\n")

    def test_tidy_drops_blank_lines_inside_a_mapping(self):
        text = "  - name: X\n    description: d\n\n    status: planned\n"
        self.assertNotIn("\n\n", command.tidy(text))

    def test_tidy_keeps_structural_blank_lines(self):
        text = "name: Universe\ndescription: d\n\nchildren:\n  - name: X\n"
        self.assertIn("\n\nchildren:", command.tidy(text))

    def test_tidy_changes_nothing_semantically(self):
        raw = scaffold.SPEC.read_text(encoding="utf-8")
        self.assertEqual(yaml.safe_load(command.tidy(raw)), yaml.safe_load(raw))

    @unittest.skipUnless(_has_ruamel(), "ruamel is a writing dependency")
    def test_an_unchanged_round_trip_is_byte_identical(self):
        """The property that makes every edit a minimal diff."""
        import io

        from ruamel.yaml import YAML

        rt = YAML()
        rt.preserve_quotes = True
        rt.indent(mapping=2, sequence=4, offset=2)
        rt.width = 78
        original = scaffold.SPEC.read_text(encoding="utf-8")
        buffer = io.StringIO()
        rt.dump(rt.load(original), buffer)
        self.assertEqual(command.tidy(buffer.getvalue()), original)


class ExitCodes(unittest.TestCase):
    """The workflow keys off these, so they are part of the contract."""

    def test_question_succeeds(self):
        self.assertEqual(command.run("status")[1], 0)

    def test_unknown_message_is_two(self):
        self.assertEqual(command.run("please make me a sandwich")[1], 2)

    def test_understood_but_impossible_is_one(self):
        self.assertEqual(command.run("what depends on a nonexistent widget")[1], 1)

    def test_a_dry_run_leaves_the_file_alone(self):
        before = scaffold.SPEC.read_text(encoding="utf-8")
        command.run("mark inference as building", apply=False)
        self.assertEqual(scaffold.SPEC.read_text(encoding="utf-8"), before)

    def test_an_edit_that_would_break_the_tree_reports_failure(self):
        reply, code = command.run("make key management depend on object storage", apply=False)
        self.assertEqual(code, 1)
        self.assertIn("Not saved", reply.text)


if __name__ == "__main__":
    unittest.main()
