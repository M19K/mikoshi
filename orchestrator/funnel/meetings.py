#!/usr/bin/env python3
"""
meetings.py — your own meetings, as a source.

    python3 -m funnel.meetings park
    python3 -m funnel.meetings status

**Why this is worth having and the others are not obviously so.** A meeting
transcript is the only source in the funnel where *you were in the room*. It
carries decisions made out loud that never reached a file, and it is the single
richest input to `Decisions.jsonl`, which is the vault's most-cited store.

**What the routine sends.** A session holding the Granola connector calls
`get_meeting_transcript` for meetings since the last drop and writes them to
`state/meetings/`, shaped:

    {"meetings": [{"id": ..., "title": ..., "date": ...,
                   "transcript": "...", "participants": [...] }]}

**Other people are in these.** A transcript is not only yours — every other
participant is quoted verbatim, and they did not agree to be filed. Two rules
follow, and both are enforced here rather than trusted to the routine:
participants are reduced to a count, never named in the item, and a meeting
whose title marks it personal is dropped rather than parked.
"""
import argparse
import re

from . import connectors

name = "meetings"
min_body = 400          # a transcript under 400 chars is a calendar artefact

PERSONAL = re.compile(r"\b(therapy|doctor|medical|personal|family|1:1 with my|"
                      r"visa|immigration|lawyer|attorney)\b", re.I)


def envelope(r):
    title = (r.get("title") or "").strip() or "(untitled meeting)"
    if PERSONAL.search(title):
        # Professional-facing by default. A meeting the owner marked personal
        # does not enter a corpus that agents read as context.
        return None
    body = (r.get("transcript") or r.get("notes") or r.get("summary") or "")
    people = r.get("participants") or []
    return {
        "id": connectors.item_id("mtg", r.get("id"), title, r.get("date")),
        "url": r.get("url") or f"granola:{r.get('id','')}",
        "title": title,
        # A count, not a roster. The other people in the room are not indexed.
        "author": f"{len(people)} participant(s)" if people else "meeting",
        "source": "Meetings",
        "tier": 5,
        "domain": "meeting",
        "form": "transcript",
        "published": r.get("date") or connectors.now_iso(),
        "body": body,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("park"); p.add_argument("--dry-run", action="store_true")
    sub.add_parser("status")
    a = ap.parse_args()
    if a.cmd == "status":
        h = connectors.health(name)
        print(f"{name}: {h['state']} — {h['why']}")
        return
    s = connectors.park(__import__("sys").modules[__name__], dry_run=a.dry_run)
    print(f"found {s['found']} · parked {s['parked']} · already seen "
          f"{s['already']} · skipped {s['skipped']}")


if __name__ == "__main__":
    main()
