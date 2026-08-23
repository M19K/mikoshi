#!/usr/bin/env python3
"""
entities.py — entity pages, built by pattern matching. Zero LLM calls.

*(2026-08-16. The common approach extracts typed edges from wikilink
syntax on every write with no model calls, and gives people, companies and tools
their own accumulating page. That is the prerequisite that makes supersession
work: you cannot retire "the old version of X" unless X is a thing the vault
knows about.)*

**Mikoshi's version uses an asset most such systems lack: a curated vocabulary.**
`01-Knowledge Base/Tooling Sources/` already holds 656 `#### Tool Name` headings,
each hand- or funnel-verified with a category and a source. That is a canonical
name list, so entity detection here is exact-match against known names rather
than guessing what looks like an entity. No model, no heuristic drift.

An entity page accumulates every mention: which note, which date, which source.
That is the record supersession will read from at Stage 7.

    python3 -m funnel.entities            # report, write nothing
    python3 -m funnel.entities --apply
"""
import argparse
import datetime as dt
import pathlib
import re
from collections import defaultdict

from . import store

KB = store.VAULT / "01-Knowledge Base"
TOOLING = KB / "Tooling Sources"
ENTITIES = KB / "Entities"

HEADING_RE = re.compile(r"^####\s+(.+?)\s*$", re.M)
FM_RE = re.compile(r"^---\n(.*?)\n---", re.S)

SCAN_DIRS = ("01-Knowledge Base", "02-Projects", "05-Orchestrator")
# `Tooling Sources/` is the vocabulary itself. Scanning it makes every entity
# match its own catalogue entry, which is not evidence of anything — a mention
# only means something when it appears somewhere other than its definition.
SKIP_PARTS = {"code", "03-Archive", ".git", ".obsidian", "state", "staged",
              "funnel", "jobs", "Entities", "kb-backups", "__pycache__",
              "Tooling Sources"}

# Names that are ordinary English and would match constantly. A curated
# vocabulary still contains words like "Every" (the publication) and "Vals".
# Requiring a capital letter is not enough — these need an exact-case,
# word-boundary hit AND still produce noise, so they are excluded outright.
AMBIGUOUS = {
    "every", "vals", "kog", "flue", "opal", "arc", "bolt", "cursor", "notion",
    "linear", "loop", "chain", "stack", "brain", "agent", "agents", "claude",
    "gpt", "ai", "llm", "api", "cli", "sdk", "mcp", "rag", "next", "swift",
    "go", "rust", "python", "node", "react", "docker", "git", "github",
}
MIN_LEN = 4


def vocabulary():
    """Canonical entity names, from the Tooling Sources headings."""
    vocab = {}
    if not TOOLING.exists():
        return vocab
    for f in sorted(TOOLING.glob("*.md")):
        text = f.read_text(encoding="utf-8")
        for m in HEADING_RE.finditer(text):
            name = m.group(1).strip().strip('"')
            # an aged entry carries a ⟨stale …⟩ or ⟨retired …⟩ marker in its
            # heading; without stripping it the same tool becomes two entities
            name = re.sub(r"\s*⟨(?:stale|retired)[^⟩]*⟩\s*$", "", name).strip()
            # strip a trailing parenthetical qualifier: `Cavalry (AI research)`
            base = re.sub(r"\s*\(.*\)\s*$", "", name).strip()
            if len(base) < MIN_LEN or base.lower() in AMBIGUOUS:
                continue
            # A single all-lowercase word is a common noun, not a name. The
            # catalogue contains skill headings like `video` and `social`, which
            # otherwise match hundreds of times and mean nothing. A real product
            # name carries a capital, a digit, or punctuation.
            if base.islower() and base.isalpha() and " " not in base:
                continue
            # keep the longest form when two headings normalise to one name
            if base.lower() not in vocab or len(name) > len(vocab[base.lower()][0]):
                vocab[base.lower()] = (base, f.stem)
    return vocab


def notes():
    for d in SCAN_DIRS:
        root = store.VAULT / d
        if not root.exists():
            continue
        for p in sorted(root.rglob("*.md")):
            if any(x in SKIP_PARTS for x in p.parts):
                continue
            yield p


def scan(vocab, progress=None):
    """Exact-name, word-boundary matches. One pass per note, no model calls."""
    # A one-word name matches case-SENSITIVELY. `Make` and `Clear` are real
    # products whose names are also ordinary English, and case-insensitive
    # matching turns every "make sure" and "to be clear" into a mention.
    # Multi-word names are distinctive enough to match either way.
    patterns = {}
    for k, (name, _cat) in vocab.items():
        rx = rf"(?<![\w-]){re.escape(name)}(?![\w-])"
        patterns[k] = re.compile(rx) if " " not in name else re.compile(rx, re.I)
    mentions = defaultdict(list)
    paths = list(notes())
    for i, p in enumerate(paths, 1):
        text = p.read_text(encoding="utf-8", errors="ignore")
        rel = str(p.relative_to(store.VAULT))
        fm = FM_RE.match(text)
        date = ""
        if fm:
            d = re.search(r"^(?:filed|created):\s*(\S+)", fm.group(1), re.M)
            date = d.group(1) if d else ""
        for key, rx in patterns.items():
            n = len(rx.findall(text))
            if n:
                mentions[key].append((rel, n, date))
        if progress:
            progress(i, len(paths))
    return mentions


def slug(text):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")[:60]


def page(name, category, hits):
    total = sum(n for _, n, _ in hits)
    where = sorted(hits, key=lambda h: -h[1])
    body = [
        "---",
        "tags: [entity]",
        f"created: {dt.date.today().isoformat()}",
        f"entity: \"{name}\"",
        f"category: {category}",
        f"mentions: {total}",
        f"notes: {len(hits)}",
        "---",
        "",
        f"# {name}",
        "",
        f"Entity record. **{total} mentions across {len(hits)} notes.** Built by exact-name "
        f"matching against the canonical vocabulary in `Tooling Sources/{category}.md` — "
        "no model call, so this page cannot hallucinate a mention.",
        "",
        "## Where it appears",
        "",
    ]
    for rel, n, date in where:
        stem = rel[:-3] if rel.endswith(".md") else rel
        title = pathlib.Path(rel).stem
        body.append(f"- [[{stem}|{title}]] — {n}×" + (f" · {date}" if date else ""))
    body += ["", "## Related", "",
             f"- `documented_in` → [[01-Knowledge Base/Tooling Sources/{category}|"
             f"Tooling Sources — {category}]]", ""]
    return "\n".join(l for l in body if l is not None)


def run(apply=False, min_notes=2, progress=None):
    vocab = vocabulary()
    mentions = scan(vocab, progress=progress)
    # An entity seen in one note is a mention, not yet a thing worth its own page.
    keep = {k: v for k, v in mentions.items() if len(v) >= min_notes}
    if apply:
        ENTITIES.mkdir(parents=True, exist_ok=True)
        for key, hits in keep.items():
            name, category = vocab[key]
            (ENTITIES / f"{slug(name)}.md").write_text(
                page(name, category, hits), encoding="utf-8")
    return {"vocabulary": len(vocab), "matched": len(mentions),
            "pages": len(keep), "written": len(keep) if apply else 0}, keep, vocab


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--min-notes", type=int, default=2)
    a = ap.parse_args()
    stats, keep, vocab = run(apply=a.apply, min_notes=a.min_notes)
    for key, hits in sorted(keep.items(), key=lambda kv: -sum(n for _, n, _ in kv[1]))[:25]:
        total = sum(n for _, n, _ in hits)
        print(f"  {total:4}×  {len(hits):2} notes  {vocab[key][0][:44]:46} [{vocab[key][1]}]")
    print(f"\n{stats}" + ("" if a.apply else "\n(report only — re-run with --apply)"))


if __name__ == "__main__":
    main()
