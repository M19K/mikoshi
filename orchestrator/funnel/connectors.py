#!/usr/bin/env python3
"""
connectors.py — sources a script cannot reach, read by a session that can.

    python3 -m funnel.connectors status          # every connector, one view
    python3 -m funnel.connectors status --strict # exit 1 if a source was ABSENT

**The split, and it is the whole design.** RSS, YouTube and the rest are fetched
by `funnel.fetch` because a headless script can reach them. Mail, meeting notes
and Drive cannot be reached that way without an API key for each — and a key is
a thing to store, leak and rotate. But a scheduled routine *is* a session, so it
already holds the MCP connections the app is signed into.

  **The routine reads** — it calls the connector it already has and writes what
  it found to `state/<name>/`, as JSON, one file per run.
  **This module parks** — it turns those files into items the funnel classifies
  exactly like any other source.

No credential ever touches this code. It cannot leak a key because it never has
one. This is the same boundary `funnel.newsletters` proved on 2026-08-19.

**The failure this module exists to prevent.** On 2026-08-20 the newsletter tier
produced nothing and *reported a healthy run*, because the parking step parks
files from disk and there was no file to park. Nine subscriptions went missing
from the corpus and the log looked clean. **Silence from a source has two
completely different causes and they must never print the same line:**

    nothing new      the routine ran, the connector answered, there was nothing.
    ABSENT           no drop file for today at all — the routine did not run, or
                     it ran without the connector attached.

The second is an outage. `status --strict` exits non-zero on it so a scheduled
run fails loudly instead of passing quietly.

**Everything read here is untrusted input.** Mail, transcripts and documents are
written by other people and land unread, and their text ends up in a knowledge
base that agents read as context. A document saying "ignore your instructions"
is a prompt injection with a delivery mechanism. Nothing here executes,
evaluates or follows content — it stores text for the same classify path every
other source takes, where a model judges it rather than obeys it.

**Scope is deliberate and narrow.** None of these three reads everything it
could. A whole mailbox, a whole Drive and every meeting ever held are not a
knowledge base, they are someone's private life — and the vault's standing rule
is professional-facing by default. Each connector declares what it is allowed to
see and the routine is told to send only that.
"""
import argparse
import datetime as dt
import hashlib
import json
import pathlib

from . import store

STATE = store.VAULT / "05-Orchestrator" / "state"

# How long a source may stay silent before silence is treated as an outage.
# Set per connector rather than globally: mail arrives daily, a Drive folder
# may genuinely go a week without an edit, and one threshold for both would
# either cry wolf or say nothing.
ABSENT_AFTER_DAYS = {"newsletters": 2, "email": 2, "meetings": 7, "drive": 14}


def drop_dir(name: str) -> pathlib.Path:
    return STATE / name


def load_drops(name: str) -> list[dict]:
    """Every record the routine has dropped for this connector, oldest first.

    Accepts either a bare list or `{"items": [...]}` / `{"messages": [...]}`,
    because three different connectors write three different envelopes and
    arguing with them is not worth a schema.
    """
    d = drop_dir(name)
    if not d.exists():
        return []
    out = []
    for f in sorted(d.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        rows = data
        if isinstance(data, dict):
            for key in ("items", "messages", "documents", "meetings", "records"):
                if isinstance(data.get(key), list):
                    rows = data[key]
                    break
            else:
                rows = [data]
        for r in rows if isinstance(rows, list) else []:
            if isinstance(r, dict):
                r["_file"] = f.name
                out.append(r)
    return out


def last_drop(name: str):
    """When the routine last wrote anything at all for this connector.

    Deliberately the FILE's date, not the newest item inside it. An empty drop
    file is the routine saying "I looked and there was nothing", which is a
    healthy answer and the thing that distinguishes quiet from absent.
    """
    d = drop_dir(name)
    if not d.exists():
        return None
    files = sorted(d.glob("*.json"))
    if not files:
        return None
    newest = max(files, key=lambda f: f.stat().st_mtime)
    return dt.datetime.fromtimestamp(newest.stat().st_mtime, dt.timezone.utc)


def health(name: str) -> dict:
    """quiet, absent, or flowing — never one word for all three."""
    seen = last_drop(name)
    limit = ABSENT_AFTER_DAYS.get(name, 3)
    if seen is None:
        return {"name": name, "state": "ABSENT", "days": None,
                "why": "no drop file has ever been written — the routine has "
                       "never run with this connector attached"}
    # **Clamped at zero because a drop can legitimately look like the future.**
    # A file written a moment ago can carry an mtime a fraction ahead of the
    # clock we compare it to, and `timedelta.days` floors, so the age came back
    # as `-1` and the routine reported a fresh drop as "-1d ago". Caught on
    # Windows by the test suite, 2026-08-23. It never mis-classified anything —
    # a negative age is not greater than any limit — but an age nobody can read
    # is a report nobody trusts.
    days = max(0, (dt.datetime.now(dt.timezone.utc) - seen).days)
    if days > limit:
        return {"name": name, "state": "ABSENT", "days": days,
                "why": f"nothing dropped for {days} days (limit {limit}) — the "
                       f"routine is not running, or is running without this "
                       f"connector. This is an outage, not a quiet week."}
    n = len(load_drops(name))
    return {"name": name, "state": "flowing" if n else "quiet", "days": days,
            "why": f"{n} record(s) waiting" if n else
                   "the routine looked and there was nothing new"}


def item_id(prefix: str, *parts) -> str:
    raw = "|".join(str(p) for p in parts if p)
    return f"{prefix}-" + hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()[:16]


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def park(spec, dry_run: bool = False) -> dict:
    """Turn one connector's dropped records into funnel items.

    `spec` supplies only what differs between connectors — everything else is
    identical on purpose, so a fourth source is a table entry rather than a
    fourth copy of this function.
    """
    db = store.connect()
    rows = load_drops(spec.name)
    stats = {"found": len(rows), "skipped": 0, "already": 0, "parked": 0}

    for r in rows:
        env = spec.envelope(r)
        if env is None:                       # the connector's own noise rule
            stats["skipped"] += 1
            continue
        if store.seen(db, env["id"]):
            stats["already"] += 1
            continue
        if len(env.get("body") or "") < spec.min_body:
            # A title without a body ranks on its title alone. Skipping is
            # recoverable; filing a stub quietly poisons the corpus.
            stats["skipped"] += 1
            continue
        env.setdefault("fetched", now_iso())
        env.setdefault("state", "seen")
        env["body"] = env["body"][:20000]
        if not dry_run:
            store.put_item(db, env)
        stats["parked"] += 1

    if not dry_run:
        db.commit()
    return stats


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status", help="every connector's health in one view")
    s.add_argument("--strict", action="store_true",
                   help="exit 1 if any source is ABSENT — for scheduled runs")
    a = ap.parse_args()

    names = ["newsletters", "email", "meetings", "drive"]
    rows = [health(n) for n in names]
    width = max(len(r["name"]) for r in rows)
    print("connector health — a source that is ABSENT is an outage, not a quiet day\n")
    for r in rows:
        mark = {"ABSENT": "!!", "quiet": "  ", "flowing": "->"}[r["state"]]
        age = f"{r['days']}d ago" if r["days"] is not None else "never"
        print(f"  {mark} {r['name']:<{width}}  {r['state']:<8} {age:<9} {r['why']}")

    absent = [r for r in rows if r["state"] == "ABSENT"]
    if absent and a.strict:
        print(f"\n{len(absent)} source(s) ABSENT. The run is not healthy.")
        raise SystemExit(1)
    print()


if __name__ == "__main__":
    main()
