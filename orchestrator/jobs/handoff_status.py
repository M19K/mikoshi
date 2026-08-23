#!/usr/bin/env python3
"""
handoff_status.py — one definition of "is this handoff still open?".

**Why this file exists.** The test lived in three places — `mine.py`,
`sync_board.py` and `vault_check.py` — and on 2026-08-22 two of them had been
taught that `Reopened` counts as open while the third had not. The result was a
live contradiction: `sync_board.py` correctly kept H-031 on the board, and
`vault_check.py` reported that same row as stale and demanded its removal. An
agent trusting the checker would have deleted a genuinely open handoff.

That is the failure `CLAUDE.md` names under *the closing duties*: a rule written
in two places drifts the first time one copy is edited. So the rule is written
once, here, and the three callers import it.

**The default is OPEN, deliberately.** A status nobody can parse is surfaced,
never swallowed — a handoff that vanishes because of unrecognised wording is
indistinguishable from one that was done, which is precisely how work gets
dropped. `mine.py` exists because a false all-clear is worse than no check.
"""
from __future__ import annotations

# Words agents actually write when a handoff is finished. Matched on the first
# token of the status cell, after stripping markdown emphasis.
CLOSED_WORDS = (
    "answered", "closed", "resolved", "done", "withdrawn",
    "superseded", "dropped", "complete", "completed", "fixed",
)

# Words that mean it is still someone's move. Listed for the reader; the
# function does not depend on the list being exhaustive, because anything
# unrecognised is treated as open anyway.
OPEN_WORDS = (
    "open", "reopened", "part-answered", "partially", "blocked",
    "in progress", "awaiting", "pending",
)


def first_token(status: str) -> str:
    """The status cell's leading word, with markdown emphasis stripped."""
    s = (status or "").strip().lstrip("*_ ").lower()
    for sep in ("·", "—", "--", ".", ",", ":", "**"):
        s = s.split(sep)[0]
    return s.strip()


def is_open(status: str) -> bool:
    """True unless the status clearly says the handoff is finished.

    Open by default. A cell reading `Reopened · 2026-08-22` or
    `**Blocked on a permission**` is open; only the closed vocabulary closes it.
    """
    head = first_token(status)
    if not head:
        return True
    return not head.startswith(CLOSED_WORDS)
