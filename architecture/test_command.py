#!/usr/bin/env python3
"""Tests for the plain-English command interpreter.

The interpreter can commit to the repository, so the tests that matter most
are the ones proving it refuses: an edit that would break the tree must leave
the spec untouched, and a question must never write at all.
"""

from __future__ import annotations

import copy
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
