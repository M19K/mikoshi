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

    python3 05-Orchestrator/jobs/sync_board.py                 # rewrite if it differs
    python3 05-Orchestrator/jobs/sync_board.py --check         # exit 1 if it differs
    python3 05-Orchestrator/jobs/sync_board.py --stamps        # who touched what, when
    python3 05-Orchestrator/jobs/sync_board.py --merge <file>  # resolve a republish conflict
    python3 05-Orchestrator/jobs/sync_board.py --inbox         # what other agents have queued

**One agent publishes this board.** [@owner · 2026-08-27] `@claude-code/mikoshi`
holds it; every other agent appends to `Board Inbox.md` and carries on. Per
section timestamps fixed *how* a conflict resolves; a single writer means there
is not one. Applied on `keyword=board update`, never automatically — an entry is
a request, and the publisher still edits it for length and duplication.

**Every section carries the time it was last changed and who changed it.**
[@owner · 2026-08-27] Two sessions can both republish the board, and until now a
conflict was settled by whichever agent was looking at it, guessing. One did
that on 2026-08-27, decided another session's version superseded its own, and
was wrong — with nothing on the page able to say otherwise.

**So the rule is his and it is mechanical: the newest change wins.** What makes
it safe is the *grain*. Whole-file "newest wins" would throw away a section the
other session edited and this one did not, which is the very loss it is meant
to prevent — so the comparison is per section, by `data-updated`. Two sessions
editing different sections both keep their work; two editing the same section
resolve to the later stamp, with the earlier one printed rather than dropped
silently.

**The generated section never needs adjudication** — it is a pure function of
the queue, so a merge regenerates it instead of choosing a side.

`--check` is what the Stop hook runs, so an agent is told before it finishes
rather than the next morning.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from handoff_status import cells as row_cells, is_open

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
        cells = row_cells(line)
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


SECTION_RE = re.compile(
    r'<section(?P<attrs>[^>]*)>(?P<body>.*?)</section>', re.S)
STAMP_RE = re.compile(r'data-updated="([^"]*)"')
ID_RE = re.compile(r'id="([^"]*)"')
BY_RE = re.compile(r'data-by="([^"]*)"')


def sections(text: str) -> dict:
    """{id: (stamp, by, whole_block)} for every stamped section."""
    out = {}
    for m in SECTION_RE.finditer(text):
        sid = ID_RE.search(m.group("attrs"))
        if not sid:
            continue
        stamp = STAMP_RE.search(m.group("attrs"))
        by = BY_RE.search(m.group("attrs"))
        out[sid.group(1)] = (stamp.group(1) if stamp else "",
                             by.group(1) if by else "?",
                             m.group(0))
    return out


def touch(text: str, section_id: str, by: str, when: str = None) -> str:
    """Move one section's stamp to now. Call it when you edit that section —
    an edit whose stamp did not move is invisible to the merge, which is the
    same as not having made it."""
    when = when or datetime.now().strftime("%Y-%m-%dT%H:%M")
    def sub(m):
        found = ID_RE.search(m.group("attrs"))
        if not found or found.group(1) != section_id:
            return m.group(0)
        attrs = STAMP_RE.sub(f'data-updated="{when}"', m.group("attrs"))
        attrs = BY_RE.sub(f'data-by="{by}"', attrs)
        return f"<section{attrs}>{m.group('body')}</section>"
    return SECTION_RE.sub(sub, text)


def merge(ours: str, theirs: str, handoffs: list[dict]) -> tuple[str, list[str]]:
    """Resolve a republish conflict. Newest `data-updated` wins, per section.

    Returns the merged page and a line per decision, because a merge nobody
    can read is the same guess it replaces."""
    mine, yours = sections(ours), sections(theirs)
    notes, out = [], ours
    for sid, (t_stamp, t_by, t_block) in yours.items():
        if sid not in mine:
            notes.append(f"  + {sid}: only in theirs ({t_by}) — kept")
            out = out.replace("</main>", t_block + "\n</main>", 1)
            continue
        o_stamp, o_by, o_block = mine[sid]
        if t_block == o_block:
            continue
        if t_stamp > o_stamp:
            notes.append(f"  ~ {sid}: theirs is newer ({t_stamp} {t_by} "
                         f"beats {o_stamp} {o_by}) — taking theirs")
            out = out.replace(o_block, t_block, 1)
        elif t_stamp < o_stamp:
            notes.append(f"  = {sid}: ours is newer ({o_stamp} {o_by} "
                         f"beats {t_stamp} {t_by}) — keeping ours")
        else:
            notes.append(f"  ! {sid}: SAME stamp {o_stamp}, different content. "
                         f"Keeping ours; read theirs before republishing.")
    for sid in mine:
        if sid not in yours:
            notes.append(f"  + {sid}: only in ours — kept")
    return rebuild(out, handoffs), notes


# A section edited without moving its stamp is invisible to `merge` — the same
# as not having edited it, and the exact way this whole mechanism would quietly
# stop working. So the last synced content of each section is recorded, and
# `--check` says which ones changed without their stamp moving. The snapshot is
# a cache, not a source: delete it and the next sync rewrites it.
SNAPSHOT = VAULT / "05-Orchestrator" / "state" / "board-sections.json"

# **The board is an index of what is outstanding, not a report on it.**
# [@owner · 2026-08-27] Measured that day: 69 items, 5,274 words, one of them
# 354 — a page nobody can scan, which defeats the only reason it exists. An
# item is a headline plus two or three lines. **The detail is not lost, it is
# in the project log, which is where a reader who wants it should be sent.**
#
# 45 words is the cap, and it is not a guess: at the board's column width a
# line runs about 15 words, so three lines is 45. The headline is not counted —
# it is doing the work of the first line.
ITEM_WORDS = 45
ITEM_RE = re.compile(r"<h3>(?P<head>.*?)</h3>(?P<body>.*?)(?=</div>\s*</li>)", re.S)

# **Finished work does not belong on this page.** [@owner · 2026-08-27] The
# board has said so in its own first line since it was written, and eight
# finished items were on it anyway — because "done" feels like something worth
# showing, and every one of them pushed an open item further down.
#
# The trap is the item that is *mostly* done: two of those eight carried an
# open half inside them, and deleting them wholesale would have deleted the
# open half too. So the rule is not "delete anything green" — it is that a
# finished chip may not exist, and clearing one means asking what is still
# open inside it and giving that its own row.
DONE_CHIPS = {"done", "answered", "fixed", "ready", "closed", "shipped", "complete"}
CHIP_RE = re.compile(r'<span class="chip[^"]*">([^<]*)</span>')


def finished(text: str) -> list[str]:
    """Items marked as finished. The board is what is outstanding."""
    out = []
    for m in re.finditer(r"<li>.*?</li>", text, re.S):
        chip = CHIP_RE.search(m.group(0))
        head = re.search(r"<h3>(.*?)</h3>", m.group(0), re.S)
        if chip and head and chip.group(1).strip().lower() in DONE_CHIPS:
            out.append(f"{chip.group(1).strip():<10} {_plain(head.group(1))[:62]}")
    return out


def _plain(fragment: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html.unescape(fragment))).strip()


def overlong(text: str) -> list[tuple[int, str]]:
    """Items whose body runs past the cap, longest first."""
    out = []
    for m in ITEM_RE.finditer(text):
        body = m.group("body")
        # The meta line is a citation and the `who` line is a routing label.
        # Neither is prose the reader has to wade through, so neither counts
        # against the cap — otherwise adding a useful label makes an item
        # "too long" and the fix is to delete the label.
        body = re.sub(r'<p class="(?:meta|who)">.*?</p>', "", body, flags=re.S)
        n = len(_plain(body).split())
        if n > ITEM_WORDS:
            out.append((n, _plain(m.group("head"))[:64]))
    return sorted(out, reverse=True)


def _digest(block: str) -> str:
    return hashlib.sha256(block.encode("utf-8")).hexdigest()[:12]


def snapshot(text: str) -> None:
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(json.dumps(
        {sid: {"stamp": st, "by": by, "digest": _digest(block)}
         for sid, (st, by, block) in sections(text).items()},
        indent=2), encoding="utf-8")


def unstamped_edits(text: str) -> list[str]:
    """Sections whose content moved while their stamp stood still."""
    if not SNAPSHOT.is_file():
        return []
    try:
        was = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    out = []
    for sid, (stamp, by, block) in sections(text).items():
        old = was.get(sid)
        if not old:
            continue
        if old["digest"] != _digest(block) and old["stamp"] == stamp:
            out.append(f"  {sid}: content changed, stamp still {stamp} "
                       f"(last set by {old['by']})")
    return out


def rebuild(board_text: str, handoffs: list[dict]) -> str:
    i = board_text.index(SECTION_START)
    j = board_text.index(SECTION_END, i)
    out = board_text[:i] + render(handoffs) + board_text[j:]
    return retally(out)


def main() -> None:
    check = "--check" in sys.argv

    if "--stamps" in sys.argv:
        for sid, (stamp, by, _) in sorted(
                sections(BOARD.read_text(encoding="utf-8")).items(),
                key=lambda kv: kv[1][0], reverse=True):
            print(f"  {stamp:<17} {by:<28} {sid}")
        print("\n  Newest change wins, per section. An edit whose stamp did not")
        print("  move is invisible to a merge — which is the same as not making it.")
        return

    if "--lint" in sys.argv:
        board = BOARD.read_text(encoding="utf-8")
        fin = finished(board)
        if fin:
            print(f"{len(fin)} FINISHED item(s) still on the board:\n")
            for f in fin:
                print(f"  {f}")
            print("\n  The board is what is outstanding. Finished work lives in the")
            print("  project log. Before removing one, ask what is still open inside")
            print("  it — a mostly-done item hides the half that is not.")
            sys.exit(1)
        long = overlong(board)
        if not long:
            print(f"Every item is within {ITEM_WORDS} words. The board is scannable.")
            return
        print(f"{len(long)} item(s) over {ITEM_WORDS} words — a headline plus two "
              f"or three lines:\n")
        for n, head in long:
            print(f"  {n:>4}w  {head}")
        print("\n  The board indexes what is outstanding; it does not report on it.")
        print("  Move the detail to the project log and link it in the meta line.")
        sys.exit(1)

    if "--inbox" in sys.argv:
        inbox = VAULT / "05-Orchestrator" / "Board Inbox.md"
        if not inbox.is_file():
            print(f"no inbox at {inbox}"); return
        text = inbox.read_text(encoding="utf-8")
        # Split on the HEADINGS, anchored to the start of a line — not on the
        # bare strings. An entry whose body quotes the applied-entries heading
        # (as one did on 2026-08-31, explaining that trap) truncated `pending`
        # at its own body and hid every entry below it. The listing still
        # printed a plausible number, so nothing looked wrong.
        parts = re.split(r"^## Pending\s*$", text, flags=re.M)
        if len(parts) < 2:
            print("Board Inbox.md has no ## Pending section"); sys.exit(2)
        tail = re.split(r"^## Applied\s*$", parts[1], flags=re.M)
        if len(tail) < 2:
            print("Board Inbox.md has no ## Applied section"); sys.exit(2)
        pending = tail[0]
        entries = [b for b in re.split(r"^### ", pending, flags=re.M)[1:]]
        if not entries:
            print("Nothing queued. The board is current with what agents have asked for.")
            return
        print(f"{len(entries)} request(s) waiting for `keyword=board update`:\n")
        for e in entries:
            head, *rest = e.strip().splitlines()
            action = (rest[0].strip() if rest else "?")
            title = next((l for l in rest[1:] if l.strip()), "").strip()[:70]
            print(f"  {head.strip()}")
            print(f"      {action:<9} {title}")
        print("\n  Applied by @claude-code/mikoshi only. Everyone else appends and moves on.")
        return

    if "--merge" in sys.argv:
        other = Path(sys.argv[sys.argv.index("--merge") + 1])
        if not other.is_file():
            print(f"no such file: {other}"); sys.exit(2)
        merged, notes = merge(BOARD.read_text(encoding="utf-8"),
                              other.read_text(encoding="utf-8"),
                              open_handoffs(QUEUE.read_text(encoding="utf-8")))
        print("merge — newest stamp wins, per section\n")
        for n in notes or ["  nothing differed"]:
            print(n)
        BOARD.write_text(merged, encoding="utf-8")
        snapshot(merged)
        print("\nOpen Board merged. Read the lines above, then republish.")
        return

    if not BOARD.is_file() or not QUEUE.is_file():
        print("board or queue missing — nothing to sync")
        return
    board = BOARD.read_text(encoding="utf-8")
    if SECTION_START not in board:
        print("board has no 'Waiting on project owners' section — not touching it")
        return
    drifted = unstamped_edits(board)
    want = rebuild(board, open_handoffs(QUEUE.read_text(encoding="utf-8")))
    if want == board and not drifted:
        print("Open Board is in sync with the queue.")
        snapshot(board)
        return
    if drifted:
        print("SECTIONS EDITED WITHOUT MOVING THEIR STAMP:\n"
              + "\n".join(drifted)
              + "\n\n  The newest stamp decides a republish conflict, so an edit\n"
                "  that did not move one cannot win and will be silently lost.\n"
                "  Set data-updated to now and data-by to your tag.")
    # [@claude-code/maintenance · 2026-08-30] Both conditions are reported, then
    # one exit. This used to `sys.exit(1)` inside the `drifted` branch, so an
    # unstamped edit MASKED the out-of-sync report — the Stop hook runs `--check`,
    # and on 2026-08-30 it printed only the stamp warning while H-068 and H-070
    # were open in the queue and missing from the board. A checker that hides the
    # very drift it exists to catch is worse than no checker.
    if want != board:
        print(("\n" if drifted else "")
              + "OPEN BOARD IS OUT OF SYNC WITH THE QUEUE.\n"
                "  Run: python3 05-Orchestrator/jobs/sync_board.py\n"
                "  then republish it. The board is the page the owner reads.")
    if check:
        sys.exit(1)
    BOARD.write_text(want, encoding="utf-8")
    snapshot(want)
    print("Open Board resynced from the queue. Republish it.")


if __name__ == "__main__":
    main()
