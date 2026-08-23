#!/usr/bin/env python3
"""
learn.py — the loop that makes the funnel's judgment improve instead of only its size.

    python3 -m funnel.learn mark <item-id> signal --why "..."
    python3 -m funnel.learn mark <item-id> noise  --why "..."
    python3 -m funnel.learn review              # walk the newest filed items
    python3 -m funnel.learn status              # what has been learned so far
    python3 -m funnel.learn examples            # what the classifier is being shown

**What was missing, stated plainly.** Mikoshi grows and repairs itself: it
ingests daily, ages notes out, fixes its own broken links, and agents read each
other's recorded decisions. All of that makes it know *more*. **None of it makes
it judge better.** The rules that decide what is worth keeping were the same the
day this was written as the day the funnel was built — a bigger library with the
same librarian.

This is the missing loop, and it is deliberately the smallest thing that closes
it: **the owner marks an item signal or noise, and the classifier is shown those
verdicts.** No training, no fine-tune, no scoring model. Real examples of one
person's taste, in the prompt, where the judgment actually happens.

**Why it does nothing until it has enough marks.** The vault's second decision
rule is *measure before you make a rule* — no threshold without a number you
actually took. So `examples()` returns nothing until `MIN_VERDICTS` marks exist,
and says so. A preference learned from three clicks is a guess with a mechanism
attached, and it would fire wrongly and then be distrusted, which is worse than
not existing. Until the floor is reached this only collects.

**Why examples and not a recalibrated score.** Both were on the table. A score
recalibrated against verdicts changes a number nobody can inspect; examples
change a prompt anyone can read, and when the classifier gets something wrong
you can see exactly which of your own verdicts led it there. The recalibration
is the better instrument later, once there are enough marks to fit anything to
— and it will need this store either way, so this is the prerequisite for both.

**The corpus is the record.** Verdicts land in `state/verdicts.jsonl`,
append-only, one JSON object per line, with what was marked and why. Nothing is
ever rewritten: a changed opinion is a new line, and the newest verdict for an
item wins.
"""
import argparse
import collections
import datetime as dt
import json
import pathlib

from . import store

VERDICTS = store.VAULT / "05-Orchestrator" / "state" / "verdicts.jsonl"

# The floor. Below this the loop collects and changes nothing.
#
# 30 is not a measurement and is not presented as one — there is no data to set
# it from yet, which is the whole point. It is chosen as the smallest number
# that can show a pattern across the funnel's three filing categories (tooling,
# concept, workflow) at ten each. **Revisit it against the real distribution
# once it is reached**, and record what the number became and why.
MIN_VERDICTS = 30

# How many of each verdict to show the classifier. More is not better: the
# examples compete with the item being judged for the model's attention, and a
# long tail of near-duplicates teaches nothing a few clear cases do not.
SHOW_EACH = 6


def _load() -> list[dict]:
    if not VERDICTS.exists():
        return []
    out = []
    for line in VERDICTS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def latest() -> dict:
    """Newest verdict per item. A changed opinion is a new line, not an edit."""
    by_item = {}
    for v in _load():                      # file order is chronological
        by_item[v["item_id"]] = v
    return by_item


def mark(item_id: str, verdict: str, why: str = "", by: str = "@owner") -> dict:
    if verdict not in ("signal", "noise"):
        raise SystemExit("verdict must be 'signal' or 'noise'")
    db = store.connect()
    row = db.execute(
        "SELECT id, title, source, domain FROM items WHERE id = ?", (item_id,)
    ).fetchone()
    if row is None:
        raise SystemExit(f"no item {item_id!r}. Run `funnel.learn review` to list ids.")
    rec = {
        "item_id": row[0],
        "title": row[1],
        "source": row[2],
        "domain": row[3],
        "verdict": verdict,
        "why": why.strip(),
        "by": by,
        "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    VERDICTS.parent.mkdir(parents=True, exist_ok=True)
    with VERDICTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def ready() -> bool:
    return len(latest()) >= MIN_VERDICTS


def examples() -> str:
    """The block the classifier is shown, or an empty string.

    Empty is a valid and common answer, and callers must treat it as normal
    rather than as a failure — a funnel with no verdicts yet behaves exactly as
    it did before this file existed.
    """
    if not ready():
        return ""
    rows = list(latest().values())
    sig = [r for r in rows if r["verdict"] == "signal"][-SHOW_EACH:]
    noi = [r for r in rows if r["verdict"] == "noise"][-SHOW_EACH:]
    if not sig or not noi:
        # One-sided examples teach the model to answer one way. A set with no
        # counter-examples is worse than none — this is the same fault that
        # made project-four's first golden set separate nobody.
        return ""

    def fmt(rs):
        out = []
        for r in rs:
            line = f'  - "{r["title"][:90]}"'
            if r["why"]:
                line += f' — {r["why"][:120]}'
            out.append(line)
        return "\n".join(out)

    return (
        "\nWHAT THIS READER HAS ALREADY JUDGED. These are their own verdicts on "
        "real items, not guidance written for you. Match the taste they show; "
        "where an item resembles neither list, judge it on the rules above.\n"
        f"\nKEPT as signal:\n{fmt(sig)}\n"
        f"\nDISCARDED as noise:\n{fmt(noi)}\n"
    )


def status() -> dict:
    rows = list(latest().values())
    c = collections.Counter(r["verdict"] for r in rows)
    by_domain = collections.Counter(
        (r.get("domain") or "?", r["verdict"]) for r in rows)
    return {"total": len(rows), "signal": c["signal"], "noise": c["noise"],
            "ready": len(rows) >= MIN_VERDICTS, "need": max(0, MIN_VERDICTS - len(rows)),
            "by_domain": by_domain}


def review(limit: int = 20):
    """The newest filed items, with the ones already judged marked."""
    db = store.connect()
    seen = latest()
    rows = db.execute(
        "SELECT id, title, source, domain FROM items "
        "WHERE state = 'filed' ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
    return [{"id": r[0], "title": r[1], "source": r[2], "domain": r[3],
             "verdict": seen.get(r[0], {}).get("verdict")} for r in rows]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("mark", help="record a verdict on one item")
    m.add_argument("item_id")
    m.add_argument("verdict", choices=["signal", "noise"])
    m.add_argument("--why", default="", help="one line — this is what teaches")
    m.add_argument("--by", default="@owner")
    r = sub.add_parser("review", help="newest filed items, with ids to mark")
    r.add_argument("--limit", type=int, default=20)
    sub.add_parser("status", help="how much has been learned")
    sub.add_parser("examples", help="exactly what the classifier is being shown")
    a = ap.parse_args()

    if a.cmd == "mark":
        rec = mark(a.item_id, a.verdict, a.why, a.by)
        s = status()
        print(f"{rec['verdict']}: {rec['title'][:70]}")
        print(f"  {s['total']} verdict(s) — {s['signal']} signal, {s['noise']} noise")
        print(f"  {s['need']} more before the classifier is shown any of them."
              if not s["ready"] else
              "  The classifier is now being shown these.")
        return

    if a.cmd == "review":
        rows = review(a.limit)
        if not rows:
            print("nothing filed yet — run the funnel first")
            return
        print(f"{len(rows)} newest filed item(s). Mark one:\n"
              f"  python3 -m funnel.learn mark <id> signal --why \"...\"\n")
        for r in rows:
            flag = {"signal": "[+]", "noise": "[-]"}.get(r["verdict"], "[ ]")
            print(f"  {flag} {r['id'][:22]:<22} {(r['domain'] or '?')[:9]:<9} "
                  f"{(r['title'] or '')[:58]}")
        return

    if a.cmd == "examples":
        block = examples()
        s = status()
        if not block:
            print(f"Nothing is being shown to the classifier yet.\n"
                  f"{s['total']} verdict(s) recorded; {s['need']} more needed, "
                  f"and both sides must be represented.\n"
                  f"Until then the funnel judges exactly as it did before.")
            return
        print(block)
        return

    s = status()
    print(f"verdicts: {s['total']}  ({s['signal']} signal / {s['noise']} noise)")
    print(f"floor:    {MIN_VERDICTS}"
          + ("  — reached, the classifier is being shown examples"
             if s["ready"] else f"  — {s['need']} more needed"))
    if s["by_domain"]:
        print("\nby domain:")
        for (dom, verdict), n in sorted(s["by_domain"].items()):
            print(f"  {dom[:14]:<14} {verdict:<7} {n}")
    print("\nA verdict is never rewritten. A changed opinion is a new line, "
          "and the newest one wins.")


if __name__ == "__main__":
    main()
