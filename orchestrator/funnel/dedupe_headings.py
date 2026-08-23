#!/usr/bin/env python3
"""
dedupe_headings.py — find and merge Knowledge Base entries filed twice under two
spellings.

`promote.py` stopped *new* ones on 2026-08-20 by matching on `entry_key()` rather
than on the lowercased heading (H-022). This is the other half: the duplicates
already on disk when that landed, and a re-runnable check so drift is visible
rather than accumulating silently.

**Merging is lossless.** The variant blocks are not deleted — the surviving *body*
is the one with the highest funnel score, and every other variant's own
description, source and date are folded underneath it as `also reported as`
lines. The alternate spelling is kept too, because a variant name is the only
record that a tool is also called that.

**The surviving body and the surviving heading are chosen separately**, because
they are different questions. The body is the best-sourced one; the heading is
the most readable one — ASCII over look-alikes, a display name over a slug. A
heading is then swept of the characters that are invisible on screen and
different to a computer: measured 2026-08-20, 11 headings in 736 carried a
narrow no-break space, a no-break space, or a non-breaking hyphen, and that class
alone produced a character-for-character duplicate of `Qwen 3.8 27B` that no
amount of case-folding would ever have caught.

**Within-file only, version parentheticals exempt.** `Figma` in SaaS and
`Figma (MCP)` in MCP Servers are separate entries by design, and `Claude Opus
(4.5)` is not a misspelling of `(4.6)`. Both rules live in `entry_key()`.

    python3 -m funnel.dedupe_headings              # report, change nothing
    python3 -m funnel.dedupe_headings --apply
"""
import argparse
import collections
import datetime as dt
import pathlib
import re
import shutil

from . import store
from .promote import HEADING_RE, entry_key

KB = store.VAULT / "01-Knowledge Base"
TOOLING = KB / "Tooling Sources"
BACKUP = store.ORCH / "state" / "kb-backups"
SCORE_RE = re.compile(r"score\s+([0-9.]+)")
SOURCE_RE = re.compile(r"^- \*\*Source\*\*:\s*(.+?)\s*$", re.M)
WHAT_RE = re.compile(r"^- \*\*What it is\*\*:\s*(.+?)\s*$", re.M)
RETRIEVED_RE = re.compile(r"retrieved\s+(\d{4}-\d{2}-\d{2})")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# Invisible on screen, different to a computer. Visible punctuation — em dash,
# middle dot, angle brackets — is left alone; it is a choice, not a hazard.
LOOKALIKES = {"\u00a0": " ", "\u2009": " ", "\u202f": " ",
              "\u2010": "-", "\u2011": "-"}


def sweep(head: str) -> str:
    """Fold look-alikes only. A heading with none is returned untouched — the
    double space before a `⟨stale …⟩` tombstone is a convention, not a defect,
    and a sweep that tidies it is editing entries it was not asked to edit."""
    out = head
    for bad, good in LOOKALIKES.items():
        out = out.replace(bad, good)
    if out == head:
        return head
    return re.sub(r"  +", " ", out).strip()


def readable(head: str):
    """Sort key for choosing which spelling stays as the heading. Lower is better."""
    return (any(ord(c) > 127 for c in head),   # look-alikes lose to plain ASCII
            bool(SLUG_RE.match(head)),          # `ui-ux-pro-max` loses to `UI UX Pro Max`
            len(head))                          # then the plainer of the two


def blocks(text: str):
    """Every `#### ` block in a file, as (heading, start, end, body)."""
    marks = [(m.start(), m.group(1).strip()) for m in HEADING_RE.finditer(text)]
    out = []
    for i, (pos, head) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        out.append((head, pos, end, text[pos:end]))
    return out


def score(body: str) -> float:
    m = SCORE_RE.search(body)
    return float(m.group(1)) if m else -1.0


def fold(head: str, body: str) -> str:
    """One variant, compressed to the line that would survive a citation."""
    what = WHAT_RE.search(body)
    src = SOURCE_RE.search(body)
    when = RETRIEVED_RE.search(body)
    bits = [f"- *(also reported as **{head}**"]
    if src:
        bits.append(f", {src.group(1)}")
    if when:
        bits.append(f", retrieved {when.group(1)}")
    bits.append(")*")
    line = "".join(bits)
    if what:
        line += f"\n  - {what.group(1)}"
    return line


def groups(path: pathlib.Path):
    """Duplicate groups in one file, keyed by entry_key, in file order."""
    text = path.read_text(encoding="utf-8")
    by_key = collections.defaultdict(list)
    for head, start, end, body in blocks(text):
        by_key[entry_key(head)].append((head, start, end, body))
    return text, {k: v for k, v in by_key.items() if len(v) > 1}


def merge_file(path: pathlib.Path, apply: bool) -> int:
    text, dupes = groups(path)
    if not dupes:
        return 0

    print(f"\n{path.name}")
    # Rewrite back-to-front so earlier offsets stay valid.
    edits = []
    for key, members in dupes.items():
        keep = max(members, key=lambda m: score(m[3]))
        losers = [m for m in members if m is not keep]
        head = sweep(min((m[0] for m in members), key=readable))
        names = " · ".join(m[0] for m in members)
        print(f"  {key:<22} {len(members)} entries — heading {head!r}, "
              f"body from {keep[0]!r} (score {score(keep[3]):.4f})")
        print(f"      {names}")
        folded = "\n".join(fold(h, b) for h, _, _, b in losers if sweep(h) != head)
        edits.append((keep, (head, folded)))
        for m in losers:
            edits.append((m, None))

    # Headings outside any duplicate group still get swept, so the next entry
    # under that name matches instead of quietly becoming a fifteenth duplicate.
    for head, start, end, body in blocks(text):
        if entry_key(head) in dupes or sweep(head) == head:
            continue
        print(f"  {'(sweep)':<22} {head!r} → {sweep(head)!r}")
        edits.append(((head, start, end, body), (sweep(head), "")))

    if not apply:
        return len(dupes)

    for (head, start, end, body), payload in sorted(edits, key=lambda e: -e[0][1]):
        if payload is None:
            text = text[:start] + text[end:]
            continue
        new_head, folded = payload
        body = body.replace(f"#### {head}", f"#### {new_head}", 1).rstrip()
        tail = ("\n" + folded) if folded else ""
        text = text[:start] + body + tail + "\n\n" + text[end:]
    path.write_text(re.sub(r"\n{4,}", "\n\n\n", text), encoding="utf-8")
    return len(dupes)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    files = sorted(TOOLING.glob("*.md"))
    if args.apply:
        stamp = dt.datetime.now().strftime("%Y-%m-%d-%H%M%S")
        dest = BACKUP / f"dedupe-{stamp}"
        dest.mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copy2(f, dest / f.name)
        print(f"backup → {dest}")

    total = sum(merge_file(f, args.apply) for f in files)
    print(f"\n{total} duplicate groups"
          + ("" if args.apply else " (report only — re-run with --apply)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
