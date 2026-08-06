#!/usr/bin/env python3
"""Tests for the graph queries and the Ollama fallback.

The graph answers are the reason the tool is worth using — a wrong blast
radius is worse than none, because someone might believe it.

The Ollama tests never contact a server. What they pin down is the property
that makes the fallback safe: the model rewrites a message into an existing
command, and that rewrite is re-dispatched through the ordinary router. It
cannot execute anything a typed command could not.
"""

from __future__ import annotations

import copy
import unittest

import yaml

import brain
import command
import scaffold

REAL_SPEC = yaml.safe_load(scaffold.SPEC.read_text(encoding="utf-8"))


def fresh() -> dict:
    return copy.deepcopy(REAL_SPEC)


def chain_spec() -> dict:
    """A -> B -> C, plus D depending on B. Hand-checkable."""
    return {
        "name": "Universe",
        "description": "r",
        "children": [
            {
                "name": "Domain",
                "description": "d",
                "children": [
                    {
                        "name": "Tier",
                        "description": "t",
                        "children": [
                            {"name": "A", "description": "a", "depends_on": ["B"]},
                            {"name": "B", "description": "b", "depends_on": ["C"]},
                            {"name": "C", "description": "c"},
                            {"name": "D", "description": "d", "depends_on": ["B"]},
                        ],
                    }
                ],
            }
        ],
    }


class GraphMaths(unittest.TestCase):
    def setUp(self):
        self.spec = chain_spec()
        self.down = scaffold.adjacency(self.spec)
        self.up = scaffold.adjacency(self.spec, reverse=True)

    def test_adjacency_points_the_way_it_says(self):
        self.assertEqual(self.down["A"], ["B"])
        self.assertEqual(sorted(self.up["B"]), ["A", "D"])

    def test_reachable_is_transitive_and_excludes_itself(self):
        self.assertEqual(scaffold.reachable(self.down, "A"), {"B", "C"})
        self.assertEqual(scaffold.reachable(self.up, "C"), {"A", "B", "D"})

    def test_reachable_on_a_leaf_is_empty(self):
        self.assertEqual(scaffold.reachable(self.down, "C"), set())

    def test_shortest_path(self):
        self.assertEqual(scaffold.shortest_path(self.down, "A", "C"), ["A", "B", "C"])
        self.assertEqual(scaffold.shortest_path(self.down, "A", "A"), ["A"])
        self.assertIsNone(scaffold.shortest_path(self.down, "C", "A"))

    def test_build_layers_puts_dependencies_first(self):
        layers = scaffold.build_layers(self.spec)
        self.assertEqual(layers[0], ["C"])
        self.assertEqual(layers[1], ["B"])
        self.assertEqual(layers[2], ["A", "D"])

    def test_every_real_component_lands_in_exactly_one_layer(self):
        layers = scaffold.build_layers(REAL_SPEC)
        placed = [n for layer in layers for n in layer]
        self.assertEqual(len(placed), len(set(placed)))
        self.assertEqual(len(placed), scaffold.count_leaves(REAL_SPEC))

    def test_no_component_precedes_something_it_depends_on(self):
        """The property that makes a build order a build order."""
        layers = scaffold.build_layers(REAL_SPEC)
        level = {n: i for i, layer in enumerate(layers) for n in layer}
        for source, target in scaffold.dependency_edges(REAL_SPEC):
            self.assertLess(level[target], level[source], f"{source} before {target}")

    def test_layer_zero_is_exactly_the_foundations(self):
        layers = scaffold.build_layers(REAL_SPEC)
        foundations = {n["name"] for n in scaffold.leaves(REAL_SPEC) if not scaffold.depends_on(n)}
        self.assertEqual(set(layers[0]), foundations)


class GraphCommands(unittest.TestCase):
    def ask(self, message, spec=None):
        return command.dispatch(spec if spec is not None else fresh(), message)

    def test_blast_radius_counts_the_whole_chain(self):
        spec = chain_spec()
        reply = command.dispatch(spec, "what breaks if C fails")
        self.assertIn("3 of 4", reply.text)

    def test_blast_phrasings(self):
        for message in (
            "what breaks if metrics fails",
            "what breaks if metrics",
            "blast radius of metrics",
            "impact of metrics",
        ):
            self.assertIn("fail if **Metrics** does", self.ask(message).text, message)

    def test_everything_needed_is_transitive(self):
        spec = chain_spec()
        self.assertIn("needs **2**", command.dispatch(spec, "everything A needs").text)

    def test_why_shows_the_chain(self):
        spec = chain_spec()
        reply = command.dispatch(spec, "why does A need C")
        self.assertIn("A** → B → **C", reply.text)

    def test_why_explains_when_the_arrow_points_the_other_way(self):
        spec = chain_spec()
        reply = command.dispatch(spec, "why does C need A")
        self.assertFalse(reply.ok)
        self.assertIn("other way round", reply.text)

    def test_about_gathers_everything(self):
        text = self.ask("about inference").text
        for expected in ("tier", "status", "owner", "needs", "needed by"):
            self.assertIn(expected, text)

    def test_check_reports_a_clean_spec(self):
        self.assertTrue(self.ask("check").ok)

    def test_foundations_and_orphans_are_disjoint_facts(self):
        self.assertIn("foundations", self.ask("foundations").text)
        self.assertIn("nothing depending on them", self.ask("orphans").text)

    def test_unlink_removes_an_edge(self):
        spec = fresh()
        reply = command.dispatch(spec, "make web search no longer depend on fetch cache")
        self.assertTrue(reply.changed)
        self.assertNotIn("Fetch Cache", scaffold.depends_on(command.find(spec, "web search")[0]))

    def test_unlink_an_edge_that_is_not_there(self):
        self.assertFalse(self.ask("make metrics no longer depend on inference").ok)

    def test_remove_refuses_while_anything_depends_on_it(self):
        reply = self.ask("remove object storage")
        self.assertFalse(reply.ok)
        self.assertFalse(reply.changed)
        self.assertIn("depend on it", reply.text)

    def test_remove_works_once_nothing_depends_on_it(self):
        spec = fresh()
        reply = command.dispatch(spec, "remove api explorer")
        self.assertTrue(reply.changed, reply.text)
        self.assertIsNone(command.find(spec, "api explorer"))
        self.assertEqual(scaffold.validate(spec), [])


class OllamaFallback(unittest.TestCase):
    """No server is contacted. These pin the safety property, not the model."""

    def test_a_dead_host_reports_how_to_fix_it(self):
        original = brain.HOST
        brain.HOST = "http://127.0.0.1:9"  # discard port, always refuses
        try:
            self.assertFalse(brain.available())
            with self.assertRaises(brain.BrainUnavailable) as caught:
                brain.translate(REAL_SPEC, "anything")
            self.assertIn("ollama serve", str(caught.exception))
        finally:
            brain.HOST = original

    def test_wrapping_is_stripped_from_the_reply(self):
        for raw, expected in [
            ("```\nstatus\n```", "status"),
            ("Command: status", "status"),
            ('"status"', "status"),
            ("status.", "status"),
            ("status\nSome chatter afterwards", "status"),
            ("  `status`  ", "status"),
        ]:
            self.assertEqual(brain.clean_command(raw), expected, raw)

    def test_the_vocabulary_is_real_names(self):
        vocab = brain.vocabulary(REAL_SPEC)
        self.assertIn("Object Storage", vocab)
        self.assertIn("Data Tier", vocab)
        self.assertNotIn("Server Infrastructure", vocab.split("COMPONENTS")[0])

    def test_a_translated_command_is_re_dispatched(self):
        original = brain.translate
        brain.translate = lambda spec, message: "what depends on object storage"
        try:
            reply = command.ask_brain(fresh(), "which things lean on the object store")
            self.assertIn("depend on **Object Storage**", reply.text)
            self.assertIn("read as:", reply.text)
        finally:
            brain.translate = original

    def test_a_suggestion_the_router_does_not_know_is_refused(self):
        """The model cannot invent capability. This is the whole guarantee."""
        original = brain.translate
        brain.translate = lambda spec, message: "rm -rf / && drop all tables"
        try:
            reply = command.ask_brain(fresh(), "destroy everything")
            self.assertFalse(reply.ok)
            self.assertFalse(reply.changed)
            self.assertIn("not a command I know", reply.text)
        finally:
            brain.translate = original

    def test_a_hallucinated_component_is_refused_by_the_router(self):
        original = brain.translate
        brain.translate = lambda spec, message: "what depends on Quantum Flux Capacitor"
        try:
            reply = command.ask_brain(fresh(), "what about the flux capacitor")
            self.assertFalse(reply.ok)
            self.assertFalse(reply.changed)
        finally:
            brain.translate = original

    def test_an_edit_suggested_by_the_model_still_faces_validation(self):
        original = brain.translate
        brain.translate = lambda spec, message: "make key management depend on object storage"
        try:
            reply, code = command.run("tangle the storage keys", brain_enabled=True)
            self.assertEqual(code, 1)
            self.assertIn("Not saved", reply.text)
        finally:
            brain.translate = original

    def test_the_router_is_tried_before_the_model(self):
        """Latency and determinism: a known phrasing must never reach Ollama."""
        original = brain.translate

        def explode(spec, message):
            raise AssertionError("the model was consulted for a known phrasing")

        brain.translate = explode
        try:
            reply, code = command.run("status", brain_enabled=True)
            self.assertEqual(code, 0)
        finally:
            brain.translate = original


if __name__ == "__main__":
    unittest.main()
