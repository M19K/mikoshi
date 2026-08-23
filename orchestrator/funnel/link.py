#!/usr/bin/env python3
"""
link.py — Stage 7. Nothing is filed without at least one link.

The orphan ring is ~800 unlinked notes. Automated ingestion makes that worse
unless linking is a *precondition of filing*, so this stage can block a file.

Links are typed, not bare (Information Lifecycle → Stage 5). The vocabulary is
fixed; `relates_to` is the fallback when the model is unavailable, and it is
marked as such rather than dressed up as a judgment.

An entry that can link to nothing is not silently orphaned — it is flagged
`novel: true`, which is itself the signal that it may be genuinely new.
"""
import re

from . import llm, store

TYPES = ("implements", "supersedes", "depends_on", "alternative_to",
         "used_by", "built_with", "contradicts")
FALLBACK_TYPE = "relates_to"

# Cosine distance ceiling, matched to `link_notes.py` so the orphan-repairer and
# the file-time linker agree. A tighter ceiling means more `novel` flags, which
# is the right trade: a flagged entry is reviewable, a wrong link is quietly
# misleading.
#
# Tightening is NOT sufficient on its own. An agent framework still links to the
# Queue at 0.341 — well inside any sane ceiling — because at 64 notes the corpus
# has no closer neighbour, and a whole-note embedding is an averaged topic
# vector. Most edges therefore type as `relates_to`, honestly: the seven-type
# vocabulary describes entity-to-entity relations, and the vault's notes are
# documents. **Stage 5 entity extraction is the fix**, not a smaller number here.
MAX_DISTANCE = 0.45
TOP_K = 4
KEEP = 3

SYSTEM = "You type edges in a knowledge graph. Answer only with JSON."

TEMPLATE = """Type each candidate edge from the NEW ENTRY to the EXISTING NOTE.

Allowed types: {types}
If none genuinely applies, use "{fallback}".

Return ONLY: {{"links":[{{"i":<index>,"type":"<type>"}}]}}

NEW ENTRY: {name} — {what}

CANDIDATES:
{cands}"""


# Root pointer files are never link targets. `README.md`, `AGENTS.md` and
# `GEMINI.md` are pointers to `CLAUDE.md` that, in its own words, "deliberately
# restate nothing" — so an edge to one carries no information about the note
# that drew it. They are also the one class this linker CANNOT reference
# correctly: `qualify()` builds a vault-relative path, a root file has no
# directory to prefix, and six files in the vault answer to `README`. On
# 2026-08-21 `stop-making-tuis.md` shipped `[[README|Mikoshi]]`, which
# `vault_check` reported as ambiguous — it resolves to one of the six silently.
# Excluding them fixes the recurrence rather than the one note.
POINTER_FILES = {"README.md", "AGENTS.md", "GEMINI.md"}


def candidates(db, env: dict):
    vec = store.embed(f"{env['entry']['name']}\n\n{env['entry']['what']} {env['entry']['why']}",
                      kind="query")
    env["entry_vec"] = vec
    near = store.nearest_notes(db, vec, k=TOP_K)
    return [(p, t, d) for p, t, d in near
            if d <= MAX_DISTANCE and p not in POINTER_FILES]


def qualify(path: str, title: str) -> str:
    """Vault-relative link with an alias — 16 notes are named `_index`."""
    stem = path[:-3] if path.endswith(".md") else path
    return f"{stem}|{title}"


def run(db, envelopes, progress=None):
    for n, env in enumerate(envelopes, 1):
        cands = candidates(db, env)
        if not cands:
            env["links"], env["novel"] = [], True
            if progress:
                progress(n, len(envelopes))
            continue

        block = "\n".join(f"[{i}] {t} — {p}" for i, (p, t, _) in enumerate(cands))
        got = llm.chat(TEMPLATE.format(types=", ".join(TYPES), fallback=FALLBACK_TYPE,
                                       name=env["entry"]["name"], what=env["entry"]["what"],
                                       cands=block), SYSTEM, max_tokens=400)
        typed = {}
        if isinstance(got, dict):
            for l in got.get("links", []) or []:
                try:
                    typed[int(l.get("i"))] = str(l.get("type"))
                except (TypeError, ValueError):
                    continue

        links = []
        for i, (path, title, dist) in enumerate(cands[:KEEP]):
            t = typed.get(i, FALLBACK_TYPE)
            if t not in TYPES + (FALLBACK_TYPE,):
                t = FALLBACK_TYPE
            links.append({"target": qualify(path, title), "type": t,
                          "distance": round(dist, 3)})
        env["links"], env["novel"] = links, False
        if progress:
            progress(n, len(envelopes))
    return envelopes


def render(links) -> str:
    return "\n".join(f"- `{l['type']}` → [[{l['target']}]]" for l in links)
