#!/usr/bin/env python3
"""
evals.py — does the search actually work? Answer with a number, not an opinion.

Every search change so far was checked against two or three questions by hand.
That is spot-checking. If a change made other questions worse, nobody would have
known — and one did: the `--fast` path returns the wrong top result for a
question the reranked path gets right, and a hand-written assertion passed it
anyway because it only checked that *something* came back.

Two numbers, both plain:

  **Top-1**  how often the correct note is the very first result.
  **Top-5**  how often the correct note appears at all in the top five.

Top-5 is the one that matters most for an agent, because an agent reads all five.
Top-1 matters for anything that acts on a single answer without looking further.

Every question lists **every** note that genuinely answers it, verified by
reading them. A single gold answer under-measures this vault on purpose: the
same fact is deliberately stated in more than one place — the rule in
`CLAUDE.md`, the mechanism in the module README — and marking the second one
wrong measures the test set, not the search. Three of the first run's seven
"misses" were exactly that.

Questions are written the way someone would actually ask, including the filler
words a real question carries. A test set of tidy keywords measures something
nobody does.

    python3 -m funnel.evals              # score the current settings
    python3 -m funnel.evals --fast       # score without reranking, to compare
    python3 -m funnel.evals --verbose    # show every miss
"""
import argparse
import time

from . import retrieve, store

# (question, every note that genuinely answers it)
CASES = [
    ("which vector store did we choose and why",
     ["05-Orchestrator/Dependencies.md", "05-Orchestrator/Information Lifecycle.md"]),
    ("how does macOS bind screen recording grants to a build signature",
     "02-Projects/project-two/Live Status.md"),
    ("what happens to an entry that matches nothing in the vault", "CLAUDE.md"),
    ("why was the board retired", "04-Comms/_Retired.md"),
    ("what are the seven decision rules", "CLAUDE.md"),
    ("how do I record something the owner decided", "CLAUDE.md"),
    ("which newsletters is the intake inbox subscribed to", "05-Orchestrator/Sources.md"),
    ("what does the funnel do with five sources posting the same tool",
     ["05-Orchestrator/Information Lifecycle.md", "05-Orchestrator/funnel/README.md"]),
    ("how long does a tooling entry stay valid before review",
     "05-Orchestrator/Information Lifecycle.md"),
    ("what is the ownership lock and where does it live", "CLAUDE.md"),
    ("which platforms charge per project rather than pooling",
     "01-Knowledge Base/Infrastructure Ledger.md"),
    ("what did we decide about the Apple Developer Program", "05-Orchestrator/Queue.md"),
    ("what does the daily maintenance routine have authority to fix",
     ["05-Orchestrator/_index.md", "CLAUDE.md"]),
    ("which feeds are blocked to curl and need a browser", "05-Orchestrator/Sources.md"),
    ("what is the owner's monthly income target", "the owner Profile.md"),
    ("what are the areas of focus", ["CLAUDE.md", "the owner Profile.md"]),
    ("how should an agent report back to the owner", "CLAUDE.md"),
    ("what happened to Recall and what replaced it",
     ["CLAUDE.md", "01-Knowledge Base/Recall Pipeline/AI Knowledge Tracking.md"]),
    ("why is Instagram treated as first class rather than a fallback",
     "05-Orchestrator/Information Lifecycle.md"),
    ("what is in the funnel's database",
     ["05-Orchestrator/funnel/README.md", "05-Orchestrator/_index.md",
      "05-Orchestrator/Dependencies.md"]),
]


def score(k=5, do_rerank=True, verbose=False):
    db = store.connect()
    top1 = top5 = 0
    misses = []
    started = time.time()

    for question, answer in CASES:
        good = {answer} if isinstance(answer, str) else set(answer)
        results, _ = retrieve.search(question, k=k, do_rerank=do_rerank, db=db)
        paths = [p for p, _s in results]
        hit1 = bool(paths) and paths[0] in good
        hit5 = bool(good & set(paths))
        top1 += hit1
        top5 += hit5
        if not hit5 or (verbose and not hit1):
            misses.append((question, sorted(good)[0], paths[:3]))

    n = len(CASES)
    return {"cases": n, "top1": top1, "top5": top5,
            "top1_pct": round(100 * top1 / n), "top5_pct": round(100 * top5 / n),
            "seconds": round(time.time() - started)}, misses


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", help="skip reranking")
    ap.add_argument("--verbose", action="store_true", help="show near-misses too")
    ap.add_argument("-k", type=int, default=5)
    a = ap.parse_args()

    label = "fusion only (no rerank)" if a.fast else "full pipeline"
    print(f"scoring {len(CASES)} questions · {label}\n")
    stats, misses = score(k=a.k, do_rerank=not a.fast, verbose=a.verbose)

    if misses:
        print("misses:")
        for q, answer, got in misses:
            mark = "MISS" if answer not in got else "rank>1"
            print(f"  {mark}  {q[:56]}")
            print(f"        want {answer}")
            print(f"        got  {got[0] if got else '(nothing)'}")
        print()

    print(f"  correct answer ranked first  : {stats['top1']}/{stats['cases']}"
          f"  ({stats['top1_pct']}%)")
    print(f"  correct answer in the top {a.k}  : {stats['top5']}/{stats['cases']}"
          f"  ({stats['top5_pct']}%)")
    print(f"  took {stats['seconds']}s")


if __name__ == "__main__":
    main()
