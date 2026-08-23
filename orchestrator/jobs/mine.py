#!/usr/bin/env python3
"""
mine.py — "what is open for me?", answered deterministically in a few lines.

**Why this exists.** On 2026-08-20 the `project-three` thread was refreshed,
reported *"no open handoffs for my tag, board in sync, and the site's chat key
already comes from its own labelled entry"*, and every one of those claims was
false: two handoffs were open and addressed to it, and `serve.py` still read the
retired key. It wrote nothing and produced a confident all-clear.

**The cause is measurable and is not the agent's judgement.** `Queue.md` is
**109 KB across 181 lines** — 19 single lines exceed 2,000 characters and the
longest is 5,211. The Handoffs section begins 13 KB in, after an Active table
where every row is a paragraph. An agent asked to "read the queue" is being
asked to pull ~27k tokens and then find itself in the middle of it; a truncated
read returns the top of the file, which is Active, not Handoffs. So the failure
looks like an agent ignoring its work and is actually an agent that never saw it.

A false all-clear is worse than no refresh at all, because it closes the
question. So the refresh no longer asks anyone to read the queue and judge —
it asks this, which greps and cannot be truncated into a wrong answer.

    python3 05-Orchestrator/jobs/mine.py @claude-code/project-three
    python3 05-Orchestrator/jobs/mine.py project-three     # tag optional
    python3 05-Orchestrator/jobs/mine.py --all                 # every open item

Exit 0 = nothing owed. Exit 1 = you owe something. **Trust the exit code, not
your reading of the file.**
"""
import argparse
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from handoff_status import is_open

VAULT = pathlib.Path(__file__).resolve().parent.parent.parent
QUEUE = VAULT / "05-Orchestrator" / "Queue.md"

ROW = re.compile(r"^\|\s*(H-\d+)\s*\|([^|]*)\|([^|]*)\|(.*)\|([^|]*)\|\s*$")


def clean(s: str) -> str:
    return s.replace("`", "").strip()


def tag_matches(cell: str, who: str) -> bool:
    """A tag matches if the project name matches. Deliberately loose on the
    prefix: a thread may know itself as `project-three` or as
    `@claude-code/project-three`, and being wrong about which is not a
    reason to miss work addressed to you."""
    cell, who = clean(cell).lower(), clean(who).lower().lstrip("@")
    who = who.split("/")[-1]
    return cell.split("/")[-1] == who or cell.endswith("/" + who)


def rows():
    if not QUEUE.exists():
        return []
    out = []
    for line in QUEUE.read_text(encoding="utf-8").splitlines():
        m = ROW.match(line)
        if not m:
            continue
        num, frm, to, body, status = m.groups()
        out.append({"id": num, "from": clean(frm), "to": clean(to),
                    "body": clean(body), "status": clean(status)})
    return out


def malformed_rows(text: str) -> list[str]:
    """Handoff rows that will be silently ignored by every tool that reads them.

    A markdown table row must end in `|`. On 2026-08-22 a row was rewritten by a
    script that forgot the trailing pipe, and the handoff simply vanished — not
    from one tool, from all of them at once, which is the worst possible failure
    mode for a queue whose entire job is that nothing goes unread. Loud is
    cheap here; silence is not.
    """
    bad = []
    for line in text.splitlines():
        if re.match(r"^\|\s*H-\d+\s*\|", line) and not line.rstrip().endswith("|"):
            bad.append(line.split("|")[1].strip())
    return bad


def duplicate_ids(text: str) -> list[str]:
    """Handoff numbers claimed by more than one row.

    Nothing allocates these atomically — every session picks the next free
    number by reading the file, so two sessions working the same evening pick
    the same one. Measured 2026-08-22: four IDs collided in a single hour, and
    H-024 and H-025 had each been claimed twice days earlier without anyone
    noticing. **A shared ID is worse than a missing one:** a status written
    against "H-037" lands on whichever row a reader finds first, so one agent
    can close another's work by accident and both believe they are done.

    Not auto-fixed. Renumbering a live row breaks every reference to it in the
    logs and on the board, and that judgement belongs to whoever owns the row.
    """
    ids = [m.group(1) for m in
           (re.match(r"\|\s*(H-\d+)\s*\|", l) for l in text.splitlines()) if m]
    return sorted({i for i in ids if ids.count(i) > 1})


# is_open lives in handoff_status.py — one definition, three callers.


def first_sentence(body: str, n: int = 220) -> str:
    b = re.sub(r"\*\*|\*|`", "", body).strip()
    cut = b[:n]
    return cut + ("…" if len(b) > n else "")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tag", nargs="?", help="your tag, or just the project name")
    ap.add_argument("--all", action="store_true", help="every open handoff")
    a = ap.parse_args()

    if not a.tag and not a.all:
        ap.error("give a tag, or --all")

    qtext = QUEUE.read_text(encoding="utf-8")
    dupes = duplicate_ids(qtext)
    if dupes:
        print(f"!! {len(dupes)} handoff number(s) are claimed by more than one row: "
              f"{', '.join(dupes)}\n"
              f"   A status written against one of these lands on whichever row is "
              f"found first.\n"
              f"   Renumber the newer one — it is not safe to leave.\n")

    bad = malformed_rows(qtext)
    if bad:
        # Printed before anything else and never suppressed: these rows are
        # invisible to this tool, so a clean "nothing owed" below would be a
        # lie of exactly the kind this script exists to prevent.
        print(f"!! {len(bad)} handoff row(s) are missing their closing `|` and "
              f"are being IGNORED by every tool: {', '.join(bad)}\n"
              f"   Fix the row in Queue.md before trusting anything below.\n")

    everything = rows()
    open_rows = [r for r in everything if is_open(r["status"])]

    if a.all:
        print(f"{len(open_rows)} open handoff(s) across the vault\n")
        for r in open_rows:
            print(f"  {r['id']}  -> {r['to']}")
            print(f"       {first_sentence(r['body'], 150)}")
        return 1 if open_rows else 0

    mine = [r for r in open_rows if tag_matches(r["to"], a.tag)]
    who = clean(a.tag).split("/")[-1]

    if not mine:
        print(f"Nothing open for {who}. "
              f"({len(open_rows)} open in the vault, none addressed to you.)")
        return 0

    print(f"{len(mine)} OPEN handoff(s) addressed to {who} — "
          f"answer each, then write the outcome into its status cell.\n")
    for r in mine:
        print(f"  {r['id']}  from {r['from']}")
        print(f"       {first_sentence(r['body'])}")
        print()
    print("Reading these and moving on is not refreshing. Act, then set the")
    print("status cell so it stops being Open, then run jobs/sync_board.py.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
