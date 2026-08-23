#!/usr/bin/env python3
"""
record.py — write down what the owner said, and what an agent learned, as it happens.

**The problem this fixes.** Mikoshi's log is past tense and outcome-only: an
agent writes what it did, in prose, at the end. So the *inputs* vanish — the
options weighed, the preference stated, the reason one was rejected. A
project-three session on 2026-08-16 lost an entire A/B/C conversation
exactly this way, and said so plainly: "I record what I did, not what you said
and preferred."

Chat is not storage. If a preference is only ever spoken, it dies with the thread.

Three files per project, all append-only, all machine-readable:

  Decisions.jsonl   — what the owner chose, and what he turned down
  Learnings.jsonl   — what an agent found out the hard way
  Predictions.jsonl — a claim with a date, so it can be scored later

**Why predictions are separate, and why they are the one that has to start
now.** A decision records what was chosen; a learning records what was found
out. Neither can ever be *wrong* in a way you can measure — they are both
written after the fact. A prediction is the only record here that can be
graded, and grading it needs the claim to exist before the outcome does.
Nothing in this vault has ever recorded one, so no amount of later cleverness
can produce a calibration score for the past. The clock starts when the first
one is written. [added 2026-08-23]

Write the moment it happens, not at the end of the session. A session that ends
early still keeps everything recorded up to that point.

    ./record.py decision project-three \\
        --chose "vertical layout" --over "horizontal,grid" \\
        --why "wanted the journey to read top to bottom"

    ./record.py learning project-four \\
        --key whole-note-embeddings-are-averaged \\
        --insight "One vector for a 15KB note points at nothing in particular." \\
        --files funnel/store.py --confidence 9

    ./record.py show project-three
"""
import argparse
import datetime as dt
import json
import pathlib
import sys

# **Windows consoles default to cp1252 and cannot encode the characters this
# codebase prints** — the log separator `·`, the em dash, and the `→` in every
# "here is the fix" line. On 2026-08-23 CI showed `record.py` dying on its own
# arrow, which meant the WRITE PATH was broken on Windows while every other
# check passed. Done at package import so a new script in here inherits it
# rather than having to remember; fixing twenty entry points one at a time is
# how the twenty-first gets missed.
import sys as _sys

for _s in (_sys.stdout, _sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


VAULT = pathlib.Path(__file__).resolve().parent.parent
PROJECTS = VAULT / "02-Projects"


def project_dir(name):
    d = PROJECTS / name
    if not d.is_dir():
        sys.exit(f"no such project: {name}\navailable: "
                 + ", ".join(sorted(p.name for p in PROJECTS.iterdir()
                                    if p.is_dir() and not p.name.startswith("."))))
    return d


def append(path, row):
    row["ts"] = dt.datetime.now().isoformat(timespec="seconds")
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("decision", help="something the owner chose")
    d.add_argument("project")
    d.add_argument("--chose", required=True, help="what he picked")
    d.add_argument("--over", default="", help="comma-separated options he rejected")
    d.add_argument("--why", default="", help="his reason, in his words where possible")
    d.add_argument("--scope", default="project", choices=["project", "vault", "taste"])
    d.add_argument("--by", default="@owner")

    l = sub.add_parser("learning", help="something an agent found out")
    l.add_argument("project")
    l.add_argument("--key", required=True, help="short stable slug, e.g. macos-tcc-binds-to-signature")
    l.add_argument("--insight", required=True)
    l.add_argument("--type", default="pitfall",
                   choices=["pitfall", "technique", "constraint", "correction"])
    l.add_argument("--files", default="", help="comma-separated paths this touches")
    l.add_argument("--confidence", type=int, default=7, help="1-10, how sure you are")
    l.add_argument("--by", default="@claude-code")

    pr = sub.add_parser("prediction", help="a claim with a date, so it can be scored")
    pr.add_argument("project")
    pr.add_argument("--key", required=True, help="short stable slug — you resolve it by this")
    pr.add_argument("--claim", required=True,
                    help="what will be true, stated so someone else could check it")
    pr.add_argument("--by-when", required=True,
                    help="YYYY-MM-DD — the date it becomes checkable")
    pr.add_argument("--confidence", type=int, required=True,
                    help="0-100, how likely you think it is. 50 says nothing and scores nothing")
    pr.add_argument("--why", default="", help="the reasoning, so a wrong call is diagnosable")
    pr.add_argument("--by", default="@claude-code")

    rs = sub.add_parser("resolve", help="say how a prediction turned out")
    rs.add_argument("project")
    rs.add_argument("--key", required=True, help="the slug the prediction was filed under")
    rs.add_argument("--outcome", required=True,
                    choices=["true", "false", "unresolvable"],
                    help="unresolvable is a real answer — 93%% of candidate claims are")
    rs.add_argument("--note", default="", help="what actually happened")
    rs.add_argument("--by", default="@claude-code")

    c = sub.add_parser("cost", help="a money change — subscription, plan, cancellation")
    c.add_argument("project")
    c.add_argument("--service", required=True, help="platform or vendor, e.g. Hume AI")
    c.add_argument("--plan", required=True, help="plan name, or 'cancelled'")
    c.add_argument("--monthly", required=True, help="USD per month as a number, or 0")
    c.add_argument("--why", default="", help="why this plan and not another")
    c.add_argument("--period-ends", default="", help="YYYY-MM-DD, if the billing period is known")
    c.add_argument("--by", default="@claude-code")

    s = sub.add_parser("show", help="read a project's records back")
    s.add_argument("project")
    s.add_argument("--kind", default="both",
                   choices=["both", "decisions", "learnings", "predictions", "costs"])

    a = ap.parse_args()
    pdir = project_dir(a.project)

    if a.cmd == "decision":
        row = append(pdir / "Decisions.jsonl", {
            "kind": "decision", "chose": a.chose,
            "over": [x.strip() for x in a.over.split(",") if x.strip()],
            "why": a.why, "scope": a.scope, "by": a.by})
        print(f"recorded → {pdir.name}/Decisions.jsonl\n  {row['chose']}"
              + (f"  (over: {', '.join(row['over'])})" if row["over"] else ""))

    elif a.cmd == "learning":
        row = append(pdir / "Learnings.jsonl", {
            "kind": "learning", "key": a.key, "type": a.type,
            "insight": a.insight,
            "files": [x.strip() for x in a.files.split(",") if x.strip()],
            "confidence": a.confidence, "by": a.by})
        print(f"recorded → {pdir.name}/Learnings.jsonl\n  [{row['type']}] {row['key']}")

    elif a.cmd == "prediction":
        if not (0 <= a.confidence <= 100):
            sys.exit("confidence is 0-100")
        try:
            dt.date.fromisoformat(a.by_when)
        except ValueError:
            sys.exit(f"--by-when must be YYYY-MM-DD, got {a.by_when!r}")
        row = append(pdir / "Predictions.jsonl", {
            "kind": "prediction", "key": a.key, "claim": a.claim,
            "by_when": a.by_when, "confidence": a.confidence,
            "why": a.why, "by": a.by})
        print(f"recorded -> {pdir.name}/Predictions.jsonl\n"
              f"  [{row['confidence']}%] {row['key']} — checkable {row['by_when']}")

    elif a.cmd == "resolve":
        f = pdir / "Predictions.jsonl"
        if not f.exists():
            sys.exit(f"no predictions recorded for {pdir.name}")
        keys = {json.loads(l).get("key") for l in f.read_text(encoding="utf-8").splitlines()
                if l.strip()}
        if a.key not in keys:
            sys.exit(f"no prediction keyed {a.key!r} in {pdir.name}. "
                     f"known: {', '.join(sorted(k for k in keys if k))}")
        row = append(f, {"kind": "resolution", "key": a.key,
                         "outcome": a.outcome, "note": a.note, "by": a.by})
        print(f"recorded -> {pdir.name}/Predictions.jsonl\n"
              f"  {row['key']} resolved {row['outcome']}")
        print("\n  Score it:  python3 -m synth.calibration --project " + pdir.name)

    elif a.cmd == "cost":
        row = append(pdir / "Costs.jsonl", {
            "kind": "cost", "service": a.service, "plan": a.plan,
            "monthly_usd": a.monthly, "why": a.why,
            "period_ends": a.period_ends, "by": a.by})
        print(f"recorded → {pdir.name}/Costs.jsonl\n  {row['service']} · {row['plan']} · ${row['monthly_usd']}/mo")
        print("\n  NOT YET IN THE LEDGER. One command, now — not later:")
        print("    python3 -m ledger.ledger render        (from 05-Orchestrator/)")
        print("  A recurring subscription also needs its row in")
        print("    05-Orchestrator/ledger/products.json")
        print("  which is what `render` reads. The prose ledger is generated from it.")
        print("\n  Agents DO have read access to spend through the platform connectors")
        print("  (Vercel, Supabase, Railway, Render, OpenRouter). What no agent can do")
        print("  is MOVE money. So a figure you can read, you record — today.")

    else:
        for name, want in (("Decisions.jsonl", "decisions"), ("Learnings.jsonl", "learnings"),
                           ("Predictions.jsonl", "predictions"), ("Costs.jsonl", "costs")):
            if a.kind not in ("both", want):
                continue
            f = pdir / name
            if not f.exists():
                print(f"\n{name}: nothing recorded yet")
                continue
            print(f"\n{name}")
            for line in f.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("kind") == "decision":
                    over = f"  · turned down: {', '.join(r['over'])}" if r.get("over") else ""
                    print(f"  {r['ts'][:10]}  chose {r['chose']}{over}")
                    if r.get("why"):
                        print(f"              why: {r['why']}")
                elif r.get("kind") == "prediction":
                    print(f"  {r['ts'][:10]}  [{r.get('confidence')}%] {r.get('key')} "
                          f"— checkable {r.get('by_when')}")
                    print(f"              {str(r.get('claim', ''))[:110]}")
                elif r.get("kind") == "resolution":
                    print(f"  {r['ts'][:10]}  -> {r.get('key')} resolved "
                          f"{r.get('outcome')}  {str(r.get('note', ''))[:70]}")
                elif r.get("kind") == "cost":
                    print(f"  {r['ts'][:10]}  ${r.get('monthly_usd')}/mo  {r.get('service')} · {r.get('plan')}")
                    if r.get("why"):
                        print(f"              why: {r['why']}")
                else:
                    print(f"  {r['ts'][:10]}  [{r.get('type')}] {r.get('key')} "
                          f"(confidence {r.get('confidence')})")
                    print(f"              {r.get('insight', '')[:110]}")


if __name__ == "__main__":
    main()
