#!/usr/bin/env python3
"""Hand free-form English to a local Ollama model, then run what it says.

    ./bin/hn "which bits of storage matter most"   # used automatically if running

The pattern matcher in command.py is fast, free, and deterministic, but it
only knows the phrasings it was taught. This is the fallback: when a message
matches nothing, ask a model to rewrite it as a command the router does know.

The model translates. It does not execute.

Its reply is fed back through `command.dispatch`, so it can only produce
commands that already exist, on components that already exist, and every edit
still passes the same validation. A model that hallucinates a component name
gets the usual "no component called X" — it cannot invent one, cannot write
YAML, and cannot reach anything the router does not already expose. Worth
being deliberate about: a local model's output is untrusted input.

Everything runs on your machine. Ollama is a local server; no message leaves
the device. Which model it uses is your choice — set OLLAMA_MODEL to whatever
you have pulled.

Configuration:
    OLLAMA_HOST     default http://localhost:11434
    OLLAMA_MODEL    default llama3.2:1b
    OLLAMA_TIMEOUT  seconds, default 60
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

import scaffold

HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
# A 1B model is what actually runs on a phone. Anything bigger is a choice.
MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:1b")
TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "60"))

# How many component names to show a small model. All 180 buries the request
# and a 1B model loses the thread; the shortlist is picked per message.
VOCAB_LIMIT = int(os.environ.get("OLLAMA_VOCAB", "30"))

GRAMMAR = """\
status
tiers
foundations
orphans
build order
check
show <tier>
find <text>
about <component>
what depends on <component>
what does <component> need
what breaks if <component> fails
everything <component> needs
why does <component> need <other component>
mark <component> as planned|building|running
set owner of <tier> to <team-slug>
add <name> to <tier>: <description>
make <component> depend on <other component>
<component> no longer depends on <other component>
remove <component>"""


class BrainUnavailable(RuntimeError):
    """Ollama is not running, or the model is not pulled."""


def _post(path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{HOST}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:200]
        if exc.code == 404:
            raise BrainUnavailable(
                f"Ollama has no model called '{MODEL}'. Pull it first:\n"
                f"    ollama pull {MODEL}"
            ) from exc
        raise BrainUnavailable(f"Ollama returned {exc.code}: {detail}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise BrainUnavailable(
            f"No Ollama at {HOST} ({exc}).\n"
            "Start it with `ollama serve`, or point OLLAMA_HOST at the machine "
            "running it — e.g. OLLAMA_HOST=http://192.168.1.10:11434"
        ) from exc


def available() -> bool:
    """Is there an Ollama server listening? Short timeout — this gates a prompt."""
    try:
        with urllib.request.urlopen(f"{HOST}/api/tags", timeout=3):
            return True
    except Exception:
        return False


def vocabulary(spec: dict, message: str = "", limit: int = 0) -> str:
    """The names the model may use, shortlisted to the ones plausibly meant.

    Handing a 1B model all 180 names costs most of its attention and it starts
    answering about whatever it read last. Scoring against the message keeps
    the prompt short and the answer on topic. Names it does not see, it cannot
    pick — but a wrong pick is caught downstream anyway, so a tight list costs
    little and buys a lot.
    """
    limit = limit or VOCAB_LIMIT
    tiers = [
        node["name"]
        for node, _ in scaffold.walk(spec)
        if scaffold.children(node) and any(scaffold.is_leaf(c) for c in scaffold.children(node))
    ]
    components = sorted(n["name"] for n in scaffold.leaves(spec))

    words = {w for w in re.split(r"\W+", message.lower()) if len(w) > 2}
    if words and len(components) > limit:
        reverse = scaffold.dependents_map(spec)

        def score(name: str) -> tuple[int, int]:
            tokens = {w for w in re.split(r"\W+", name.lower()) if w}
            overlap = len(words & tokens)
            # Break ties toward the components most things depend on: if the
            # message is vague, those are the likeliest subject.
            return (overlap, len(reverse.get(name, [])))

        ranked = sorted(components, key=score, reverse=True)
        components = sorted(ranked[:limit])

    return f"TIERS: {', '.join(tiers)}\n\nCOMPONENTS: {', '.join(components)}"


def prompt_for(spec: dict, message: str) -> str:
    return f"""\
You translate a person's request into exactly one command for an architecture \
tool. Reply with the command and nothing else — no explanation, no quotes, no \
code fences.

The only valid commands are:
{GRAMMAR}

Use only these names, spelled exactly as shown:
{vocabulary(spec, message)}

If the request does not correspond to any command above, reply with exactly:
UNKNOWN

Request: {message}
Command:"""


def clean_command(raw: str) -> str:
    """Strip the wrapping small models add even when told not to."""
    text = raw.strip()
    text = re.sub(r"^```[a-z]*\n?|```$", "", text, flags=re.M).strip()
    text = text.split("\n")[0].strip()
    text = re.sub(r'^(command|answer)\s*[:=]\s*', "", text, flags=re.I).strip()
    return text.strip("`\"' ").rstrip(".")


def translate(spec: dict, message: str) -> str:
    """Free-form English -> one command the router understands."""
    result = _post(
        "/api/generate",
        {
            "model": MODEL,
            "prompt": prompt_for(spec, message),
            "stream": False,
            # Deterministic: the same question should map to the same command.
            "options": {"temperature": 0, "num_predict": 60},
        },
    )
    return clean_command(result.get("response", ""))


def explain(spec: dict, message: str, context: str) -> str:
    """Answer in prose, given facts pulled from the graph. Never edits."""
    result = _post(
        "/api/generate",
        {
            "model": MODEL,
            "prompt": (
                "Answer the question using only the facts below. If they do not "
                "contain the answer, say so plainly rather than guessing.\n\n"
                f"FACTS:\n{context}\n\nQUESTION: {message}\n\nANSWER:"
            ),
            "stream": False,
            "options": {"temperature": 0.2, "num_predict": 400},
        },
    )
    return result.get("response", "").strip()


def context_for(spec: dict, message: str, limit: int = 40) -> str:
    """Pull the parts of the graph the question seems to be about."""
    words = {w for w in re.split(r"\W+", message.lower()) if len(w) > 3}
    lines: list[str] = []
    reverse = scaffold.dependents_map(spec)
    for node, ancestors in scaffold.walk(spec):
        if not scaffold.is_leaf(node):
            continue
        haystack = f"{node['name']} {node.get('description', '')}".lower()
        if words and not any(w in haystack for w in words):
            continue
        needs = scaffold.depends_on(node)
        used_by = reverse.get(node["name"], [])
        lines.append(
            f"- {node['name']} (tier: {ancestors[-1]['name']}, "
            f"status: {scaffold.status_of(node, ancestors)}): "
            f"{scaffold.clean(node.get('description', ''))} "
            f"Needs: {', '.join(needs) or 'nothing'}. "
            f"Needed by: {len(used_by)} components."
        )
        if len(lines) >= limit:
            break
    if not lines:
        counts = scaffold.status_rollup(spec, [])
        return (
            f"The platform has {scaffold.count_leaves(spec)} components across "
            f"{len(scaffold.dependency_edges(spec))} dependencies. "
            f"{counts['planned']} planned, {counts['building']} building, "
            f"{counts['running']} running. Nothing matched the question."
        )
    return "\n".join(lines)
