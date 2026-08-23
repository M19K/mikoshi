#!/usr/bin/env python3
"""
calibration.py — was the confidence honest?

    python3 -m synth.calibration                    # every project
    python3 -m synth.calibration --project project-three
    python3 -m synth.calibration --due              # what is checkable now

**The gap this closes, and the honest size of it.** Decisions and learnings are
both written *after* the fact, so neither can ever be wrong in a way anybody can
measure. A prediction is the only record in this vault that can be graded — and
grading needs the claim to exist before the outcome does. That is why this file
is small and its input is empty: no cleverness added later can produce a score
for a past nobody wrote down.

**The score.** Brier: the mean squared error between the confidence you stated
and what happened, where a resolved-true outcome is 1 and false is 0. Lower is
better. Two reference points that make the number readable:

    0.25   what you get by saying 50% to everything — the do-nothing baseline
    0.00   perfect, and if you are near it you are only recording safe claims

**Why the buckets matter more than the headline.** One number says whether you
are calibrated; the buckets say *how you are wrong*. Being 90% confident and
right 60% of the time is overconfidence, and it is invisible in the mean if it
is balanced by hedging elsewhere. The buckets are the diagnosis.

**`unresolvable` is a first-class outcome, not a failure to answer.** Measured
elsewhere on a large corpus: of 500 candidate claims at high confidence, only
34 survived a falsifiability filter — 93% were beliefs, present-state
observations, advice or vibes. A vault that hides its unresolvable rate is
reporting a calibration for the small tail of claims it happened to phrase
sharply. It is printed beside the score, always.
"""
import argparse
import collections
import datetime as dt
import json
import pathlib

VAULT = pathlib.Path(__file__).resolve().parent.parent.parent
PROJECTS = VAULT / "02-Projects"

# Wide enough that a bucket has members before the corpus is large, narrow
# enough to separate hedging from conviction. Revisit against the real
# distribution once any bucket has 20 resolved claims — not before.
BUCKETS = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 101)]

BASELINE = 0.25          # the score you get by saying 50% to everything


def load(project: str | None = None) -> list[dict]:
    out = []
    paths = ([PROJECTS / project / "Predictions.jsonl"] if project
             else sorted(PROJECTS.glob("*/Predictions.jsonl")))
    for p in paths:
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            row["project"] = p.parent.name
            out.append(row)
    return out


def resolved(project: str | None = None) -> list[dict]:
    """Predictions joined to their newest outcome. Append-only: a changed
    verdict is a new line and the last one wins."""
    rows = load(project)
    claims = {}
    for r in rows:
        if r.get("kind") == "prediction":
            claims[(r["project"], r["key"])] = dict(r)
    for r in rows:                       # file order is chronological
        if r.get("kind") == "resolution":
            k = (r["project"], r["key"])
            if k in claims:
                claims[k]["outcome"] = r.get("outcome")
                claims[k]["note"] = r.get("note", "")
    return [c for c in claims.values() if c.get("outcome")]


def open_claims(project: str | None = None, due_only: bool = False) -> list[dict]:
    rows = load(project)
    settled = {(r["project"], r["key"]) for r in rows if r.get("kind") == "resolution"}
    today = dt.date.today().isoformat()
    out = [r for r in rows if r.get("kind") == "prediction"
           and (r["project"], r["key"]) not in settled]
    if due_only:
        out = [r for r in out if str(r.get("by_when", "")) <= today]
    return sorted(out, key=lambda r: str(r.get("by_when", "")))


def brier(rows: list[dict]) -> float | None:
    """Mean squared error of stated confidence against outcome.

    `unresolvable` is excluded rather than counted as a miss — a claim nobody
    can check is a fault in the claim, not a wrong call, and scoring it as a
    miss would punish exactly the ambitious predictions worth making."""
    scored = [r for r in rows if r.get("outcome") in ("true", "false")]
    if not scored:
        return None
    total = 0.0
    for r in scored:
        p = r.get("confidence", 50) / 100
        actual = 1.0 if r["outcome"] == "true" else 0.0
        total += (p - actual) ** 2
    return total / len(scored)


def by_bucket(rows: list[dict]) -> list[dict]:
    """How you are wrong, not just whether. Overconfidence at the top of the
    range and hedging at the bottom cancel out in the mean."""
    scored = [r for r in rows if r.get("outcome") in ("true", "false")]
    out = []
    for lo, hi in BUCKETS:
        band = [r for r in scored if lo <= r.get("confidence", 50) < hi]
        if not band:
            continue
        hits = sum(1 for r in band if r["outcome"] == "true")
        stated = sum(r.get("confidence", 50) for r in band) / len(band)
        out.append({"band": f"{lo}-{hi - 1}%", "n": len(band),
                    "stated": stated, "actual": 100 * hits / len(band)})
    return out


def report(project: str | None = None) -> dict:
    rows = resolved(project)
    counts = collections.Counter(r.get("outcome") for r in rows)
    unres = counts["unresolvable"]
    return {"resolved": len(rows), "scored": counts["true"] + counts["false"],
            "unresolvable": unres,
            "unresolvable_rate": (unres / len(rows)) if rows else None,
            "brier": brier(rows), "buckets": by_bucket(rows),
            "open": len(open_claims(project)), "due": len(open_claims(project, True))}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project")
    ap.add_argument("--due", action="store_true",
                    help="list claims whose date has passed and nobody has scored")
    a = ap.parse_args()

    if a.due:
        due = open_claims(a.project, due_only=True)
        if not due:
            print("Nothing is due. Predictions still open: "
                  f"{len(open_claims(a.project))}.")
            return 0
        print(f"{len(due)} prediction(s) checkable now — resolve them while the "
              f"outcome is still visible:\n")
        for r in due:
            print(f"  {r['by_when']}  [{r.get('confidence')}%] "
                  f"{r['project']}/{r['key']}")
            print(f"              {str(r.get('claim',''))[:100]}")
        return 0

    rep = report(a.project)
    scope = a.project or "every project"
    print(f"calibration — {scope}\n")

    if not rep["resolved"]:
        print(f"  Nothing resolved yet. {rep['open']} prediction(s) open, "
              f"{rep['due']} of them checkable now.\n")
        print("  This is the expected state on a vault that has just started")
        print("  recording them. A calibration score cannot be reconstructed")
        print("  from records written after the outcome — only predictions")
        print("  made before it can ever be graded, so the number arrives")
        print("  when enough of them come due, and not before.")
        return 0

    b = rep["brier"]
    print(f"  resolved       {rep['resolved']}")
    print(f"  scored         {rep['scored']}")
    print(f"  unresolvable   {rep['unresolvable']}"
          + (f"  ({rep['unresolvable_rate']:.0%} — a claim nobody can check "
             f"is a fault in the claim)" if rep["unresolvable"] else ""))
    if b is None:
        print("\n  No scorable outcome yet — every resolution so far was "
              "unresolvable.")
        return 0
    verdict = ("better than saying 50% to everything" if b < BASELINE
               else "no better than saying 50% to everything")
    print(f"\n  Brier          {b:.3f}   ({verdict}; baseline {BASELINE})")

    if rep["buckets"]:
        print("\n  where the confidence was honest, and where it was not")
        print(f"    {'band':<10} {'n':>3}  {'said':>6}  {'was right':>10}")
        for row in rep["buckets"]:
            gap = row["actual"] - row["stated"]
            flag = ("  overconfident" if gap < -10 else
                    "  underconfident" if gap > 10 else "")
            print(f"    {row['band']:<10} {row['n']:>3}  {row['stated']:>5.0f}%  "
                  f"{row['actual']:>9.0f}%{flag}")

    if rep["due"]:
        print(f"\n  {rep['due']} prediction(s) are checkable now and unscored. "
              f"An unresolved\n  claim is not a kind score — it is no score. "
              f"`--due` lists them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
