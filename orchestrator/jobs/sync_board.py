#!/usr/bin/env python3
"""
sync_board.py — regenerate the parts of the Open Board that are derivable, so
they cannot drift.

**Why.** The board is the page the owner keeps open. Every agent was expected to
update it by hand before finishing, which meant it was only right when someone
remembered — and on 2026-08-20 it showed a handoff that had been closed and was
missing two that were open. He caught it by asking. A dashboard that is wrong is
worse than none, because it is trusted.

**What this does and does not touch.** The "Waiting on project owners" section
and the tally are a pure function of `Queue.md`, so this writes them. Everything
else on the board — what needs the owner, per-project work, parked ideas — is
judgment, and judgment is not generated. Those stay hand-written.

    python3 05-Orchestrator/jobs/sync_board.py           # rewrite if it differs
    python3 05-Orchestrator/jobs/sync_board.py --check   # exit 1 if it differs

`--check` is what the Stop hook runs, so an agent is told before it finishes
rather than the next morning.
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from handoff_status import is_open

VAULT = Path(__file__).resolve().parents[2]
BOARD = VAULT / "05-Orchestrator" / "Open Board.html"
QUEUE = VAULT / "05-Orchestrator" / "Queue.md"

SECTION_START = "  <h2>Waiting on project owners"
SECTION_END = "</section>"


def open_handoffs(queue_text: str) -> list[dict]:
    """Every handoff whose status cell still reads Open. Order preserved."""
    out = []
    for line in queue_text.splitlines():
        m = re.match(r"\|\s*(H-\d+)\s*\|\s*`?([^`|]+)`?\s*\|\s*`?([^`|]+)`?\s*\|", line)
        if not m:
            continue
        cells = line.rstrip().rstrip("|").split(" | ")
        status = cells[-1].strip().lower()
        # One definition of "open", in handoff_status.py — this used to be a
        # private copy and drifted from vault_check.py within a day.
        if not is_open(status):
            continue
        body = cells[3] if len(cells) > 4 else ""
        # first bolded sentence is the headline the board should carry
        head = re.search(r"\*\*(.+?)\*\*", body)
        gist = re.sub(r"[*`]", "", head.group(1)) if head else re.sub(r"[*`]", "", body[:150])
        # The posting date comes from the STATUS cell — `Open · posted YYYY-MM-DD`
        # — never from the body. [2026-08-20] Reading the first date in the prose
        # took whatever evidence the handoff happened to cite first: H-021 was
        # rendered "posted 2026-09-14" off a deadline it mentioned, a future date
        # on the page the owner keeps open. Body dates are citations, not metadata.
        posted = re.search(r"(\d{4}-\d{2}-\d{2})", cells[-1])
        out.append({
            "id": m.group(1),
            "to": m.group(3).strip().replace("@claude-code/", ""),
            "gist": gist.strip()[:210],
            "posted": posted.group(1) if posted else "",
        })
    return out


def age_chip(posted: str) -> str:
    if not posted:
        return "open"
    try:
        y, mo, d = (int(x) for x in posted.split("-"))
        days = (date.today() - date(y, mo, d)).days
    except ValueError:
        return "open"
    return "today" if days <= 0 else "1 day" if days == 1 else f"{days} days"


def render(handoffs: list[dict]) -> str:
    rows = []
    for h in handoffs:
        rows.append(
            '    <li>\n'
            f'      <span class="chip c-stop">{age_chip(h["posted"])}</span>\n'
            '      <div class="body">\n'
            f'        <h3>{h["id"]} → {h["to"]}</h3>\n'
            f'        <p>{h["gist"]}</p>\n'
            f'        <p class="meta">posted {h["posted"] or "?"} · unanswered by its owner</p>\n'
            '      </div>\n'
            '    </li>'
        )
    body = "\n".join(rows) if rows else (
        '    <li><span class="chip c-go">clear</span><div class="body">'
        '<h3>Nothing waiting on another project</h3>'
        '<p>Every handoff in the queue has been answered.</p></div></li>')
    return (
        "  <h2>Waiting on project owners <em>posted to them, not yet acted on</em></h2>\n"
        '  <div class="rule"></div>\n'
        '  <p style="margin:10px 0 0;font-size:13px;color:var(--ink-2);max-width:70ch">'
        "One project found something another needs. Each was posted to the owning agent and "
        "is still open. <strong>Nothing here needs you</strong> — it is here so a handoff "
        "cannot sit unread without you being able to see that it is sitting. "
        "<em>This section is generated from the queue; do not hand-edit it.</em></p>\n"
        "  <ol>\n" + body + "\n  </ol>\n"
    )


SECTION_RE = re.compile(r"<h2>(.*?)</h2>(.*?)</section>", re.S)


def count_items(board_text: str) -> dict:
    """Count the <li> rows in every section, keyed by the section's heading.

    The tallies were typed by hand and were wrong twice on 2026-08-20 — once
    reading 9/5/15/8/5 against real counts of 7/5/26/-/6. Every one of them is
    a number of rows sitting in the markup, so none of them should ever have
    been a judgement.
    """
    counts = {}
    for m in SECTION_RE.finditer(board_text):
        head = re.sub(r"<.*?>", "", m.group(1)).strip().lower()
        counts[head] = m.group(2).count("<li>")
    return counts


def retally(board_text: str) -> str:
    """Set every countable tally from the rows actually present.

    'Stale / drifting' is deliberately NOT set: it counts `c-hold` chips, which
    are a judgement about whether something is drifting rather than a section.
    """
    c = count_items(board_text)
    def pick(*words):
        for head, n in c.items():
            if all(w in head for w in words):
                return n
        return None

    # ONLY "With owners". The page derives the rest on load — another session
    # added that script on 2026-08-20, and deriving in the page is strictly
    # better than writing into the file: it cannot go stale and needs no
    # republish. "With owners" is the one cell it deliberately leaves alone,
    # because it counts open handoffs in Queue.md rather than rows on the page.
    #
    # Setting the others here too would put TWO writers on one number, which is
    # the exact drift both mechanisms exist to end. So this touches one cell.
    owners = pick("waiting on project owners")
    if owners is not None:
        # Anchor on the cell's LABEL, not its class. [2026-08-20] This read
        # `<div class="t-stop"><b>` exactly; another session later added
        # `data-generated` to that div, so the substitution matched NOTHING and
        # said nothing about it. The cell froze at 5 while three handoffs were
        # open, and `--check` still passed, because a rewrite that changes
        # nothing is byte-identical to the file it came from. The label is what
        # gives the cell its meaning, so it is the stable thing to find it by.
        #
        # The first guard written here was defeated immediately: it asked
        # whether the number appeared anywhere in the page, and an unrelated
        # <b>3</b> satisfied it. Verified by breaking the cell on purpose and
        # watching it pass. This one re-reads the cell it just wrote.
        cell = re.compile(r'(<b>)\d+(</b><span>With owners)')
        if not cell.search(board_text):
            raise SystemExit(
                "sync_board: cannot find the 'With owners' tally cell — its "
                "markup changed. Fix the pattern in retally(); do not let this "
                "pass silently.")
        board_text = cell.sub(rf'\g<1>{owners}\g<2>', board_text, count=1)
        if not re.search(rf'<b>{owners}</b><span>With owners', board_text):
            raise SystemExit(
                "sync_board: wrote the 'With owners' tally and it did not take.")
    return board_text


def rebuild(board_text: str, handoffs: list[dict]) -> str:
    i = board_text.index(SECTION_START)
    j = board_text.index(SECTION_END, i)
    out = board_text[:i] + render(handoffs) + board_text[j:]
    return retally(out)


def main() -> None:
    check = "--check" in sys.argv
    if not BOARD.is_file() or not QUEUE.is_file():
        print("board or queue missing — nothing to sync")
        return
    board = BOARD.read_text(encoding="utf-8")
    if SECTION_START not in board:
        print("board has no 'Waiting on project owners' section — not touching it")
        return
    want = rebuild(board, open_handoffs(QUEUE.read_text(encoding="utf-8")))
    if want == board:
        print("Open Board is in sync with the queue.")
        return
    if check:
        print("OPEN BOARD IS OUT OF SYNC WITH THE QUEUE.\n"
              "  Run: python3 05-Orchestrator/jobs/sync_board.py\n"
              "  then republish it. The board is the page the owner reads.")
        sys.exit(1)
    BOARD.write_text(want, encoding="utf-8")
    print("Open Board resynced from the queue. Republish it.")


if __name__ == "__main__":
    main()
