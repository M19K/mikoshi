#!/usr/bin/env python3
"""
evidence.py — gather citable LINES, not pages, each carrying who said it.

**This is the half that makes Mikoshi's synthesis different, and it is not the
model.** The ordinary unit of citation is a page. A page citation is
unfalsifiable in practice: the page is long, the claim is somewhere
in it, and checking costs more than trusting. Here a citation is a **file and a
line number**, so a wrong claim is caught by looking, not by believing.

Three tags ride along with every passage, and all three come from metadata the
vault already carries rather than anything new we have to write:

  authority   `[@owner · date]` marks a line as HIS decision; `[@claude-code/x]`
              marks it as an agent's. Measured 2026-08-21: 225 of the first, 57
              of the second. A page store has no equivalent axis, because a
              page has no author inside it. **An answer that
              cannot tell "he decided this" from "an agent inferred this" is the
              exact failure this vault keeps hitting** — including twice by me
              on 2026-08-21.

  supersession  `⟨stale … superseded by …⟩`, `**Supersedes**:` and RETIRED /
              WITHDRAWN markers. Thin today (8 occurrences), so it is a filter
              that rarely fires rather than a headline feature — stated plainly
              so nobody oversells it later.

  scope       the owning project, so an answer can stay inside one silo.
              A global ranking buries a small source: a 40-note project loses
              to a 40,000-note one, every time, and silently. Ours is per-folder
              by construction.

**Entity pages are used as a COVERAGE MAP, not as a source.** An entity page
holds no claims — it is an index: *"OpenRouter, 38 mentions across 20 notes,"*
then the list. Citing one would cite a table of contents. So they do two jobs
here instead, and both were previously done by nothing at all (measured
2026-08-21: 135 of 177 entity pages had no inbound links — the funnel wrote
them and nothing ever read them):

  recall     when a question names a known entity, the notes that entity page
             already ranks by mention count are pulled in as candidates. That
             is a hand-built index the funnel maintains for free, and BM25 has
             no way to know it exists.
  honesty    the answer can state how much of the available material it used —
             *"OpenRouter appears in 20 notes; this answer draws on 3."* That is
             a completeness signal, distinct from a gap signal, and most
             systems give neither.

Retrieval itself is NOT reimplemented — `funnel.retrieve.search` already fuses
BM25 and vectors and measures 85% right-answer-first. This narrows its winners
down to the lines that actually earned the hit.
"""
import pathlib
import re

from funnel import retrieve

VAULT = pathlib.Path(__file__).resolve().parent.parent.parent
PROJECTS = VAULT / "02-Projects"

ENTITIES = VAULT / "01-Knowledge Base" / "Entities"
ENT_NAME = re.compile(r'^entity:\s*"?(.+?)"?\s*$', re.M)
ENT_LINK = re.compile(r"^-\s*\[\[([^\]|]+)(?:\|[^\]]*)?\]\]\s*—\s*(\d+)×", re.M)

OWNER = re.compile(r"\[@owner[^\]]*\]", re.I)
AGENT = re.compile(r"\[@(claude-code|admin|hermes|unknown-agent)[^\]]*\]", re.I)
DATED = re.compile(r"(20\d{2}-\d{2}-\d{2})")
STALE = re.compile(r"⟨\s*stale[^⟩]*⟩|\*\*(RETIRED|WITHDRAWN|SUPERSEDED)\b", re.I)

# A line has to carry something before it is worth citing. Headings, list
# bullets and prose qualify; table pipes and code fences do not, because a
# citation pointing at `|---|---|` helps nobody.
NOISE = re.compile(r"^\s*(\|[\s\-:|]*\||```|---\s*$|<!--)")


def entity_index() -> dict:
    """{lowercased entity name: {name, path, notes:[(vault-relative path, count)]}}"""
    idx = {}
    if not ENTITIES.exists():
        return idx
    for p in sorted(ENTITIES.glob("*.md")):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        m = ENT_NAME.search(text)
        name = (m.group(1) if m else p.stem.replace("-", " ")).strip()
        notes = [(t.strip(), int(c)) for t, c in ENT_LINK.findall(text)]
        idx[name.lower()] = {"name": name, "path": str(p.relative_to(VAULT)),
                             "notes": notes}
    return idx


def entities_in(question: str, idx: dict | None = None) -> list[dict]:
    """Known entities the question names. Longest name wins, so `Claude Code`
    beats `Claude` rather than both firing."""
    idx = entity_index() if idx is None else idx
    low = f" {question.lower()} "
    found = []
    for key, ent in idx.items():
        if len(key) < 3:
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", low):
            found.append(ent)
    found.sort(key=lambda e: -len(e["name"]))
    out, seen = [], ""
    for e in found:
        if e["name"].lower() in seen:
            continue
        seen += " " + e["name"].lower()
        out.append(e)
    return out[:3]


def _authority(line: str, context: str) -> str:
    """Who is speaking. The line wins; its surrounding block is the fallback."""
    if OWNER.search(line):
        return "owner"
    if AGENT.search(line):
        return "agent"
    if OWNER.search(context):
        return "owner-block"
    return "unattributed"


def _project_of(path: pathlib.Path) -> str | None:
    try:
        rel = path.relative_to(PROJECTS)
    except ValueError:
        return None
    return rel.parts[0] if rel.parts else None


def _score_line(line: str, terms: set) -> int:
    low = line.lower()
    return sum(1 for t in terms if t in low)


def gather(question: str, scope: str | None = None, k: int = 6,
           per_file: int = 3, db=None) -> list[dict]:
    """Citable passages for a question, best first.

    `scope` is a project folder name. Passing one keeps the answer inside that
    silo, which is the default an operator usually wants.
    """
    terms = {t for t in retrieve.tokens(question, drop_stopwords=True) if len(t) > 2}
    hits, _docs = retrieve.search(question, k=k * 3, db=db)

    # The funnel already ranked, per entity, which notes talk about it most.
    # Nothing read that until now. Fold its top notes in as candidates so a
    # question naming an entity reaches them even when BM25 misses.
    ents = entities_in(question)
    known = {h[0] for h in hits}
    for e in ents:
        for rel, count in e["notes"][:4]:
            cand = str((VAULT / (rel + ".md")))
            if (VAULT / (rel + ".md")).exists() and cand not in known:
                hits.append((cand, 0.0))
                known.add(cand)

    out = []
    for path_str, score in hits:
        path = pathlib.Path(path_str)
        if not path.is_absolute():
            path = VAULT / path
        project = _project_of(path)
        if scope and project != scope:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue

        context = "\n".join(lines[:80])
        ranked = []
        for i, line in enumerate(lines, 1):
            s = line.strip()
            if len(s) < 25 or NOISE.match(line):
                continue
            hit = _score_line(s, terms)
            if not hit:
                continue
            # Density, not raw count. A 5,000-character Queue row mentions
            # everything and pinpoints nothing; citing it sends the reader to a
            # wall of text, which is the coarse-citation problem this module
            # exists to fix. Long lines have to earn their place.
            density = hit / max(1, len(s) / 160)
            ranked.append((density, i, s))
        ranked.sort(key=lambda r: (-r[0], r[1]))

        for density, i, text in ranked[:per_file]:
            out.append({
                "path": str(path.relative_to(VAULT)),
                "line": i,
                "text": text[:400],
                "authority": _authority(text, context),
                "date": (DATED.search(text) or DATED.search(context) or [None, None])[0]
                        if DATED.search(text) or DATED.search(context) else None,
                "project": project,
                "superseded": bool(STALE.search(text)),
                "retrieval_score": score,
                "line_hits": round(density, 3),
            })

    # A line his own hand signed outranks an agent's paraphrase of it.
    rank = {"owner": 0, "owner-block": 1, "agent": 2, "unattributed": 3}
    out.sort(key=lambda p: (p["superseded"], rank[p["authority"]], -p["line_hits"]))
    out = out[:k * per_file]
    for p in out:
        p["entities"] = [e["name"] for e in ents]
    return out


def coverage(question: str) -> list[dict]:
    """How much material exists per named entity — the denominator for honesty."""
    return [{"name": e["name"], "path": e["path"], "notes": len(e["notes"])}
            for e in entities_in(question)]


def format_for_prompt(passages: list[dict]) -> str:
    """Numbered evidence the model must cite by index."""
    rows = []
    for n, p in enumerate(passages, 1):
        who = {"owner": "OWNER DECIDED", "owner-block": "in a block the owner authored",
               "agent": "an agent wrote", "unattributed": "unattributed"}[p["authority"]]
        flag = " [SUPERSEDED]" if p["superseded"] else ""
        rows.append(f"[E{n}] ({who}{flag}) {p['path']}:{p['line']}\n{p['text']}")
    return "\n\n".join(rows)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("question")
    ap.add_argument("--scope", help="restrict to one project folder")
    ap.add_argument("-k", type=int, default=6)
    a = ap.parse_args()
    ps = gather(a.question, scope=a.scope, k=a.k)
    print(f"{len(ps)} passages\n")
    for n, p in enumerate(ps, 1):
        print(f"[E{n}] {p['authority']:<14} {p['path']}:{p['line']}")
        print(f"      {p['text'][:110]}")
