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

import re as _re

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


# Splitting a row on the literal `" | "` assumes every author padded their
# pipes. On 2026-08-28 one did not: H-064's row was written `| H-064|`from`|`to`|…`
# with no spaces, so `sync_board.py` read the WHOLE ROW as the status cell, its
# first token came out `h-064`, and `is_open` — open by default, correctly —
# published a handoff that had been **answered on 2026-08-27** to the board the owner
# reads. `mine.py` read the same row correctly, so the two tools disagreed about
# what was outstanding, which is the exact failure this module was created to
# end. The splitter belongs beside the definition it feeds.
# [@claude-code/maintenance · 2026-08-28]
_PIPE = _re.compile(r"(?<!\\)\|")


def cells(line: str) -> list:
    r"""A table row's content cells, padded or not, `\|` escapes respected."""
    parts = _PIPE.split(line.strip())
    if parts and not parts[0].strip():
        parts = parts[1:]
    if parts and not parts[-1].strip():
        parts = parts[:-1]
    return parts
