#!/usr/bin/env python3
"""
ablate.py — what does each memory store actually contribute?

    python3 -m synth.ablate                 # all five configurations
    python3 -m synth.ablate --quick         # six questions each, for a smoke test
    python3 -m synth.ablate --only all-on,no-learnings

**The gap this closes.** The synthesis layer reads three stores — the markdown
corpus, `Decisions.jsonl`, and `Learnings.jsonl` — and every quality number
this project has ever published measured **all three together**. So "85% of
answers cite the right file" was true and told us nothing about which store
earned it. A store that contributes nothing is not free: it is paying for
itself in tokens on every single call, and it is one more thing to keep
correct.

**How it works.** The same fixed question set is run five times, switching off
one store each time, plus a floor with all three off. Every run is scored by
the same `synth.evals` scorer, so the only thing that differs between two rows
is the store that was removed. A column that does not move when a store is
removed is a store that is not doing anything for that question set.

  all-on          the product as shipped
  no-evidence     the markdown corpus removed
  no-decisions    what the owner chose, and turned down, removed
  no-learnings    what agents found out the hard way, removed
  no-memory       all three removed — the floor everything else is read against

**Read it as a diagonal, not as five scores.** The useful output is the drop
from `all-on`, per store, per column. And read the drops against the noise
floor: run-to-run variation on this kind of set is about ±2 points, so a store
whose removal costs less than that has not been shown to do anything — it has
been shown to be smaller than the measurement.

**It costs real money on a routed model** — five runs rather than one. On the
local model it costs only time. Either way it is the cheapest way to find out
that a store is dead weight, which is otherwise never discovered at all.
"""
import argparse
import time

from . import answer, evals, verify


CONFIGS = [
    ("all-on", answer.ALL_TIERS),
    ("no-evidence", ("decisions", "learnings")),
    ("no-decisions", ("evidence", "learnings")),
    ("no-learnings", ("evidence", "decisions")),
    ("no-memory", ()),
]


def score_one(cases, tiers, claim_check=True):
    """One configuration, scored exactly the way `synth.evals` scores a run."""
    grounded = clean = honest = 0
    sent_total = sent_backed = 0
    answered = failed = 0
    scoreable = sum(1 for _, e in cases if e)
    absent = len(cases) - scoreable

    for q, expect in cases:
        res = answer.synthesize(q, tiers=tiers)
        outcome = res.get("outcome", "answered" if res.get("answer") else "model_error")
        if outcome == "answered":
            answered += 1
        elif outcome in ("model_error", "all_unmarked"):
            failed += 1
        if claim_check and res.get("answer"):
            verify.verify(res)
            sup = res.get("support") or {}
            sent_total += sup.get("sentences", 0)
            sent_backed += sup.get("supported", 0)
        if outcome == "answered" and not res.get("invented_citations", 0):
            clean += 1
        cited = [c.get("path", c.get("source", "")) for c in (res.get("cited") or [])]
        if expect:
            wanted = (expect,) if isinstance(expect, str) else expect
            grounded += any(w in c for c in cited for w in wanted)
        else:
            honest += (outcome in ("no_evidence", "declined")
                       or (outcome == "answered" and bool(res.get("gaps"))))

    return {"grounded": grounded, "scoreable": scoreable,
            "clean": clean, "answered": answered, "failed": failed,
            "honest": honest, "absent": absent,
            "backed": sent_backed, "sentences": sent_total}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true", help="six questions per configuration")
    ap.add_argument("--only", help="comma-separated configuration names")
    ap.add_argument("--no-claim-check", action="store_true")
    a = ap.parse_args()

    # Same guard the scorer uses: a run against a dead endpoint is not a score.
    from funnel import llm
    if not llm.available():
        raise SystemExit(f"the model endpoint is not answering — {llm.where()}\n"
                         f"Nothing was ablated. Start it and run again.")

    cases = evals.CASES[:6] if a.quick else evals.CASES
    stamp = evals.fingerprint(cases)
    picked = CONFIGS
    if a.only:
        want = {w.strip() for w in a.only.split(",")}
        picked = [c for c in CONFIGS if c[0] in want]

    print(f"ablation · {len(cases)} cases · set {stamp} · {llm.where()}\n")
    rows = []
    for name, tiers in picked:
        t0 = time.time()
        r = score_one(cases, tiers, claim_check=not a.no_claim_check)
        r["name"] = name
        r["secs"] = time.time() - t0
        rows.append(r)
        print(f"  ran {name:<14} {r['secs']:5.0f}s")

    base = next((r for r in rows if r["name"] == "all-on"), None)

    def pct(n, d):
        return f"{round(100 * n / d)}%" if d else "—"

    print(f"\n{'configuration':<15}{'grounded':>12}{'backed':>10}{'honest':>9}"
          f"{'clean':>8}{'failed':>8}")
    for r in rows:
        print(f"{r['name']:<15}"
              f"{str(r['grounded']) + '/' + str(r['scoreable']):>12}"
              f"{pct(r['backed'], r['sentences']):>10}"
              f"{str(r['honest']) + '/' + str(r['absent']):>9}"
              f"{str(r['clean']) + '/' + str(r['answered']):>8}"
              f"{r['failed']:>8}")

    if base and len(rows) > 1:
        print("\nwhat each store is worth — drop from all-on, in points\n")
        bg = 100 * base["grounded"] / base["scoreable"] if base["scoreable"] else 0
        bb = 100 * base["backed"] / base["sentences"] if base["sentences"] else 0
        for r in rows:
            if r["name"] == "all-on":
                continue
            g = 100 * r["grounded"] / r["scoreable"] if r["scoreable"] else 0
            b = 100 * r["backed"] / r["sentences"] if r["sentences"] else 0
            dg, db = bg - g, bb - b
            # **Two different floors, because they were measured on two
            # different things.** ±2 for `grounded` comes from running one exam
            # twice at temperature 0. `backed` has no measured floor at all —
            # the all-on configuration read 82% and then 94% two hours apart on
            # the same model and set, so its run-to-run spread is at least 12
            # points. Quoting the grounded floor for both was carrying a level
            # measured on one task across to another, which is the mistake this
            # vault has now recorded three times.
            verdict = ("below the noise floor — not shown to contribute"
                       if abs(dg) < 2.5 and abs(db) < 12 else "contributes")
            print(f"  {r['name']:<14} grounded {dg:+5.1f}   backed {db:+5.1f}   {verdict}")
        print("\n  Noise floors, and they are NOT the same for both columns:"
              "\n    grounded  ±2 points, measured by running one exam twice."
              "\n    backed    at least ±12 — all-on read 82% then 94% on the"
              " same model and set,"
              "\n              two hours apart. Its real floor has never been"
              " measured."
              "\n  A drop smaller than its column's floor is not a zero. It is"
              " an unknown.")


if __name__ == "__main__":
    main()
