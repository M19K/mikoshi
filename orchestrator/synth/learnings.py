#!/usr/bin/env python3
"""
learnings.py — the "what did we find out the hard way?" index.

**Found by the eval, not by design.** Expanding the question set to 30 on
2026-08-21 included *"what did we learn about reasoning models eating the token
budget"* — a thing this vault knows well, recorded twice. The pre-flight said
**unreachable**, and the reason was structural: `funnel.retrieve` builds its
corpus from markdown, so **every `Learnings.jsonl` in the vault was invisible to
retrieval.** 291 records across 8 projects that nothing could ever cite.

That is the same shape of gap the entity pages had, and it is worth naming as a
pattern: *the vault's most structured records were the least reachable, because
retrieval was built for prose.* Decisions had already been rescued by
`decisions.py`; learnings had not.

**Why learnings are worth citing specifically.** A learning is a trap someone
already fell into, written at the moment they understood it, with a confidence
score and the files it touches. It is the single highest-value thing to surface
before an agent repeats the mistake — and, unlike prose, it carries its own
metadata about how much to trust it.

    python3 -m synth.learnings "token budget"
    python3 -m synth.learnings --all
"""
import json
import pathlib
import re

VAULT = pathlib.Path(__file__).resolve().parent.parent.parent
PROJECTS = VAULT / "02-Projects"


def load(project: str | None = None) -> list[dict]:
    out = []
    paths = ([PROJECTS / project / "Learnings.jsonl"] if project
             else sorted(PROJECTS.glob("*/Learnings.jsonl")))
    for p in paths:
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            row["project"] = p.parent.name
            row["source"] = str(p.relative_to(VAULT))
            out.append(row)
    out.sort(key=lambda r: str(r.get("ts", "")), reverse=True)
    return out


def _terms(q: str) -> set:
    return {t for t in re.findall(r"[a-z0-9][a-z0-9\-]+", q.lower()) if len(t) > 2}


# **A relevance floor was considered and measured out, 2026-08-23.** The
# three-tier-memory reference argues for one on any store that grows without
# bound, which learnings does — 292 and climbing. Measured on the 30-question
# set before adding it: of 270 records actually injected into prompts, **one**
# scored 2 or less, and the weakest learning to reach a prompt scored 5 against
# a typical top match of 12. The top-N cap is already doing the work, because
# the corpus is still small enough that good matches exist for every question.
#
# So no floor. A knob that fires once in 270 is the guessed rule rule 2 forbids.
# **Revisit when questions routinely have fewer than four matches above about
# 5** — that is the symptom, and it means the corpus has outgrown the cap.
def matching(query: str, project: str | None = None, top: int = 4) -> list[dict]:
    """Learnings bearing on the query, best first.

    The `key` is weighted hardest because it is a deliberate slug someone chose
    to name the finding — a match there is a much stronger signal than a word
    appearing somewhere in a long insight."""
    terms = _terms(query)
    if not terms:
        return []
    scored = []
    for l in load(project):
        key = str(l.get("key", "")).lower().replace("-", " ")
        insight = str(l.get("insight", "")).lower()
        score = (4 * sum(1 for t in terms if t in key)
                 + 1 * sum(1 for t in terms if t in insight))
        if score:
            # A high-confidence finding outranks a hedged one at equal match.
            scored.append((score + 0.1 * int(l.get("confidence", 5)), l))
    scored.sort(key=lambda s: -s[0])
    return [l for _, l in scored[:top]]


def format_for_prompt(rows: list[dict]) -> str:
    if not rows:
        return ""
    out = []
    for n, l in enumerate(rows, 1):
        out.append(
            f"[L{n}] {l['project']} · {str(l.get('ts',''))[:10]} · "
            f"{l.get('type','')} · confidence {l.get('confidence','?')}/10\n"
            f"  {l.get('key','')}\n"
            f"  {str(l.get('insight',''))[:700]}")
    return "\n\n".join(out)


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="?")
    ap.add_argument("--project")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()

    if a.all or not a.query:
        rows = load(a.project)
        print(f"{len(rows)} learnings across "
              f"{len({r['project'] for r in rows})} projects")
        by = {}
        for r in rows:
            by[r["project"]] = by.get(r["project"], 0) + 1
        for k, v in sorted(by.items(), key=lambda kv: -kv[1]):
            print(f"  {k:<28} {v}")
        return 0

    rows = matching(a.query, a.project)
    if not rows:
        print(f"Nothing recorded for {a.query!r}.")
        return 1
    print(f"{len(rows)} learning(s) bearing on {a.query!r}\n")
    print(format_for_prompt(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
