#!/usr/bin/env python3
"""
age.py — Stage 7. How knowledge leaves.

`Information Lifecycle.md` has specified this since 2026-08-10 and nothing
implemented it: `half_life` was written into frontmatter and read by nobody,
`supersedes` was a line of text nobody acted on, and there was no tombstone code
anywhere. An entry could be three versions out of date and nothing would say so.

Three mechanics, in the order they fire:

  **Time.**  Past its half-life an entry is FLAGGED, never deleted. Review asks
             one question — is this still true? Yes refreshes the date, no
             disposes of it.
  **Supersession.**  The one that matters. A newer entry about the same subject
             retires the older one by its own content, without waiting for a
             clock. A Sonnet 5 entry supersedes the Sonnet 4 entry.
  **Disposal.**  Tombstone by default: one line saying what it was, when it was
             current, and what replaced it. Delete outright only for things that
             never mattered or are actively misleading.

Tombstoning rather than deleting is what stops the same tool being re-added six
months later as if it were new, and it costs about twenty tokens.

    python3 -m funnel.age review          # what is stale, what is superseded
    python3 -m funnel.age review --apply  # write the flags
    python3 -m funnel.age tombstone "Old Tool" --replaced-by "New Tool"
"""
import argparse
import datetime as dt
import pathlib
import re

from . import store
from .embed_corpus import title_of

KB = store.VAULT / "01-Knowledge Base"
TOOLING = KB / "Tooling Sources"
INGESTED = KB / "Ingested"

HALF_LIFE = {"tooling": 180, "workflow": 365, "concept": 1095,
             "industry": 365, "news": 7}
DEFAULT_HALF_LIFE = 180

HEADING_RE = re.compile(r"^####\s+(.+?)\s*$", re.M)
RETRIEVED_RE = re.compile(r"retrieved (\d{4}-\d{2}-\d{2})")
CARD_RE = re.compile(r"pulled (\d{4}-\d{2}-\d{2})")
FM_RE = re.compile(r"^---\n(.*?)\n---", re.S)
STALE_MARK = "⟨stale"
TOMB_MARK = "⟨retired"

# A newer name that supersedes an older one differs only by a version number.
VERSIONED = re.compile(r"^(?P<base>.+?)[\s\-]*v?(?P<ver>\d+(?:\.\d+)*)\s*$", re.I)

# A flag is appended to the heading, so the heading is no longer the entry's
# name. Strip it before comparing anything: without this, flagging an entry
# renames it, `tombstone` can no longer find it, supersession stops matching it
# against its own newer version, and the entity vocabulary picks up a name with
# a timestamp glued on. [measured 2026-08-16]
MARKER_RE = re.compile(r"\s*⟨(?:stale|retired)[^⟩]*⟩\s*$")


def clean_name(raw: str) -> str:
    return MARKER_RE.sub("", raw).strip()


def today():
    return dt.date.today()


def parse_date(s):
    try:
        return dt.date.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def entries():
    """Every filed entry, from both shapes the vault uses.

    Tooling Sources hold `#### Name` blocks with a dated citation line; Ingested
    holds one atomic note per entry with real frontmatter. Both age the same way.
    """
    out = []
    for f in sorted(TOOLING.glob("*.md")):
        text = f.read_text(encoding="utf-8")
        heads = [(m.start(), m.group(1).strip()) for m in HEADING_RE.finditer(text)]
        raw_names = {clean_name(n): n for _, n in heads}
        for i, (pos, name) in enumerate(heads):
            end = heads[i + 1][0] if i + 1 < len(heads) else len(text)
            block = text[pos:end]
            d = RETRIEVED_RE.search(block) or CARD_RE.search(block)
            out.append({"kind": "block", "file": f, "name": clean_name(name),
                        "raw_name": name, "block": block,
                        "filed": parse_date(d.group(1)) if d else None,
                        "category": "tooling",
                        "retired": TOMB_MARK in block, "flagged": STALE_MARK in block})

    for f in sorted(INGESTED.glob("*.md")) if INGESTED.exists() else []:
        text = f.read_text(encoding="utf-8")
        fm = FM_RE.match(text)
        meta = {}
        if fm:
            for line in fm.group(1).splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    meta[k.strip()] = v.strip().strip('"')
        title = title_of(f, text)   # one definition; a `#` in a code fence is not an H1
        out.append({"kind": "note", "file": f, "name": clean_name(title),
                    "raw_name": title, "block": text,
                    "filed": parse_date(meta.get("filed") or meta.get("created")),
                    "category": meta.get("category", "tooling"),
                    "half_life": int(meta["half_life"]) if meta.get("half_life", "").isdigit() else None,
                    "supersedes": meta.get("supersedes", ""),
                    "retired": TOMB_MARK in text, "flagged": STALE_MARK in text})
    return out


def stale(e):
    """Past its half-life. Returns days overdue, or None."""
    if e["retired"] or not e["filed"]:
        return None
    hl = e.get("half_life") or HALF_LIFE.get(e["category"], DEFAULT_HALF_LIFE)
    overdue = (today() - e["filed"]).days - hl
    return overdue if overdue > 0 else None


def superseded(all_entries):
    """Pairs where a newer entry retires an older one, by content not by clock.

    Two signals, both explicit — never a guess:
      · an entry naming another in its `supersedes:` frontmatter
      · two entries whose names differ only by a version number
    """
    pairs = []
    by_name = {e["name"].lower(): e for e in all_entries}

    for e in all_entries:
        target = (e.get("supersedes") or "").strip().lower()
        if target and target in by_name and by_name[target] is not e:
            pairs.append((by_name[target], e, "declared"))

    versioned = {}
    for e in all_entries:
        m = VERSIONED.match(e["name"])
        if not m:
            continue
        base = m.group("base").strip().lower()
        try:
            ver = tuple(int(x) for x in m.group("ver").split("."))
        except ValueError:
            continue
        versioned.setdefault(base, []).append((ver, e))

    for base, items in versioned.items():
        if len(items) < 2:
            continue
        items.sort(key=lambda t: t[0])
        newest = items[-1][1]
        for _ver, old in items[:-1]:
            if old is not newest and not old["retired"]:
                pairs.append((old, newest, "version"))
    return pairs


def mark(e, note):
    """Append a flag to an entry in place. Never rewrites what is there."""
    text = e["file"].read_text(encoding="utf-8")
    if e["kind"] == "block":
        head = f"#### {e.get('raw_name') or e['name']}"
        if head not in text or note in text:
            return False
        text = text.replace(head, f"{head}  {note}", 1)
    else:
        if note in text:
            return False
        text = re.sub(r"^(# .+)$", lambda m: m.group(1) + "  " + note, text,
                      count=1, flags=re.M)
    e["file"].write_text(text, encoding="utf-8")
    return True


def tombstone(name, replaced_by="", reason=""):
    """Collapse an entry to one line. What it was, when, what replaced it."""
    for e in entries():
        if e["name"].lower() != name.lower():
            continue
        text = e["file"].read_text(encoding="utf-8")
        when = e["filed"].isoformat() if e["filed"] else "unknown"
        line = (f"#### {e['name']}  {TOMB_MARK} {today()}⟩\n"
                f"Was current {when}."
                + (f" Superseded by **{replaced_by}**." if replaced_by else "")
                + (f" {reason}" if reason else "") + "\n")
        if e["kind"] == "block":
            text = text.replace(e["block"].rstrip() + "\n", line)
            e["file"].write_text(text, encoding="utf-8")
        else:
            e["file"].write_text(f"---\ntags: [retired]\nretired: {today()}\n---\n\n" + line,
                                 encoding="utf-8")
        return True
    return False


def review(apply=False):
    all_e = entries()
    live = [e for e in all_e if not e["retired"]]
    stales = [(e, stale(e)) for e in live]
    stales = [(e, d) for e, d in stales if d is not None and not e["flagged"]]
    pairs = [(o, n, why) for o, n, why in superseded(live) if not o["flagged"]]

    if apply:
        for e, days in stales:
            mark(e, f"{STALE_MARK} {today()} · {days}d past half-life — still true?⟩")
        for old, new, _why in pairs:
            mark(old, f"{STALE_MARK} {today()} · superseded by {new['name']}⟩")

    return {"entries": len(all_e), "live": len(live), "retired": len(all_e) - len(live),
            "stale": len(stales), "superseded": len(pairs), "written": apply}, stales, pairs


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("review")
    r.add_argument("--apply", action="store_true")
    t = sub.add_parser("tombstone")
    t.add_argument("name")
    t.add_argument("--replaced-by", default="")
    t.add_argument("--reason", default="")
    a = ap.parse_args()

    if a.cmd == "tombstone":
        print("tombstoned" if tombstone(a.name, a.replaced_by, a.reason)
              else f"no entry named {a.name!r}")
        return

    stats, stales, pairs = review(apply=a.apply)
    if pairs:
        print("superseded — a newer entry retires an older one:")
        for old, new, why in pairs[:20]:
            print(f"  {old['name'][:40]:42} ← {new['name'][:34]:36} ({why})")
    if stales:
        print(f"\npast half-life — flag for review, never delete:")
        for e, days in sorted(stales, key=lambda t: -t[1])[:20]:
            print(f"  {days:5}d over  {e['name'][:44]:46} [{e['category']}]")
    print(f"\n{stats}" + ("" if a.apply else "\n(review only — re-run with --apply to flag)"))


if __name__ == "__main__":
    main()
