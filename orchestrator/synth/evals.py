#!/usr/bin/env python3
"""
evals.py — does the synthesis cite the right line, and does it invent any?

**Why this exists before the feature is called done.** A synthesis layer's
failure mode is not silence, it is fluency: a well-written wrong answer with a
plausible citation. On a local model that risk is higher than on a frontier one.
Without a scored set we cannot tell a good answer from a confident one, and
shipping it would replace *"read these five notes"* — which is honest — with
*"here is the answer"* — which may not be.

Three things are scored, and only the first is about the model:

  grounded    did the answer cite the file that actually holds the answer?
              This is retrieval plus binding, and it is the number that matters.
  clean       did it invent any citation? Invented markers are stripped before
              the reader sees them, so this counts how often the guard fired —
              a rising number means the model is drifting, not that the output
              is wrong.
  backed      of every sentence asserted, how many are supported by a line that
              actually says so? This is CLAIM-level and it is the strict one:
              a sentence citing nothing counts against it, because an unmarked
              sentence is unverifiable rather than passing. Added 2026-08-21
              after `grounded` reported a perfect score on an answer whose
              citation was real and whose claim was invented.
  honest      When the vault genuinely does not hold something, does the layer
              say so rather than answering anyway? The absent cases have no
              answer in the vault ON PURPOSE, and an answer there is a failure.

              **Fixed 2026-08-21, and the fix lowered the score — which was the
              point.** This used to count any empty answer as honest, and
              `synthesize` returns an empty answer for three different reasons.
              Only one of them is honesty. `answer.py` now returns an `outcome`:

                no_evidence   retrieval matched nothing anywhere in the vault.
                              A principled refusal. Counts as honest.
                declined      evidence was found, and the model answered with
                              no claim plus a gap naming what is missing. This
                              is the layer working. Counts as honest.
                answered      a real answer came back. Honest only if it also
                              flagged the shortfall in `gaps`.
                model_error   the local model returned nothing usable. NOT
                              honesty — the evidence was there and it failed.
                all_unmarked  the model asserted only uncited sentences and
                              strict mode deleted them all. A grounding
                              failure, and it used to read as a refusal.

              The last two are reported in their own column. A run carrying
              them is a run whose other numbers are measured on fewer cases
              than it claims, so they are never silently absorbed.

              **What separating them found.** All three absent cases were being
              reported as `model_error` — and the model had in fact declined
              correctly each time, with the gap named. `answer.py` was throwing
              a right answer away and telling the reader the model had failed.
              The 3/3 was accidentally right; the reason under it was wrong,
              and over MCP the reader saw the wrong reason.

Expectations are file-level, not line-level, deliberately: which line best
supports a claim is a judgement, and an eval that encodes my judgement measures
my agreement with myself.

    python3 -m synth.evals              # all 30, roughly 15 minutes
    python3 -m synth.evals --quick      # the first six
    python3 -m synth.evals --no-claim-check   # faster, and much weaker
"""
import argparse
import hashlib
import json
import time

from funnel import llm
from . import answer, verify

CASES = [
    # question, acceptable source(s) — a string or a tuple of them.
    # None means the vault does not hold it and answering is the failure.
    #
    # Thirty questions, grouped by the KIND of question rather than by topic,
    # because the failure modes differ by kind. A "why" question is answered by
    # a recorded decision; a "what is the rule" question by CLAUDE.md; a "what
    # is the state" question by a project's Live Status. Mixing them evenly is
    # what stops the set measuring one retrieval path and calling it quality.

    # --- standing rules: answered by CLAUDE.md -------------------------------
    ("why is there one API key per product", "CLAUDE.md"),
    ("what does keyword=refresh require an agent to do", "CLAUDE.md"),
    ("why does QA bill the product under test", "CLAUDE.md"),
    ("why are log entries never rewritten", "CLAUDE.md"),
    ("what happens when a key label is missing", ("CLAUDE.md", "keys.py")),
    ("how many points may a reply to the owner contain", "CLAUDE.md"),
    ("what are the four things that stop at the owner", "CLAUDE.md"),
    ("what must an agent do before finishing a piece of work", "CLAUDE.md"),
    ("what does keyword=handoff do", "CLAUDE.md"),

    # --- decisions: answered by Decisions.jsonl ------------------------------
    ("why not use the macOS Keychain for storing keys", ("CLAUDE.md", "Decisions.jsonl")),
    # Widened 2026-08-21 after the first full run: the system cited the
    # 2026-08-17 decision, which records the measurement verbatim, where my
    # expectation named the code comment. A "why" question is answered by the
    # decision, not by the file the decision produced — the expectation was
    # wrong, not the answer. Recorded rather than quietly edited, because
    # loosening an eval until it passes is how an eval stops measuring anything.
    ("why is reranking turned off by default", ("funnel/retrieve.py", "Decisions.jsonl")),
    ("why was Hermes not adopted as the dispatcher", "Decisions.jsonl"),
    ("why did we fork FounderOS for the console", "Decisions.jsonl"),
    ("why is there one universal QA protocol instead of one per project", "Decisions.jsonl"),
    ("why do agents fix defects instead of asking first", "Decisions.jsonl"),
    ("why was the Open Board's handoff section made generated", "Decisions.jsonl"),
    ("why does the funnel own the Knowledge Base folder", "Decisions.jsonl"),

    # --- procedures: answered by a README or a script -------------------------
    ("which QA phase runs first and why", ("qa/README.md", "Vault Changelog.md")),
    ("what does the QA gate reject a run for", "qa/README.md"),
    ("what are the stages of the ingestion funnel", "funnel/README.md"),
    ("how does the funnel decide what to file", ("funnel/README.md", "score.py", "Live Status.md")),
    ("how is per-product OpenRouter spend attributed", ("ledger", "CLAUDE.md")),
    ("what does the daily vault check do", ("CLAUDE.md", "jobs/README.md", "vault_check.py")),

    # --- state: answered by a project's own files -----------------------------
    ("why is the score floor not the lever for what gets filed", "Live Status.md"),
    ("what is still blocking the job search from going out", ("Live Status.md", "Queue.md", "Home.md")),
    ("what did we learn about reasoning models eating the token budget",
     ("Learnings.jsonl", "qa/README.md", "preflight.py")),
    ("what is the state of the portfolio website's content", "Live Status.md"),

    # --- deliberately absent, and HARD ---------------------------------------
    # Rewritten 2026-08-21 after H-028. The first version asked about a shoe
    # size, a Frankfurt cluster and a shareholder meeting — absurd on their
    # face, so refusing them measured nothing. delta hit the same fault
    # from the other side: their faithful cases were the source verbatim, which
    # asks "is this passage supported by itself", and a whole axis measured
    # nothing. The honest test of honesty is a question that is plausible,
    # on-topic, and simply not recorded — where refusing costs the model
    # something. Each of these is adjacent to material the vault really holds.
    ("what did the QA run on 2026-08-14 find", None),          # no run that day
    ("how much did the Higgsfield trial cost in its second month", None),
    ("which model did we measure as best for the funnel's classify step", None),
]



def fingerprint(cases) -> str:
    """A stable id for THIS question set.

    **Runs from different sets are not comparable, and the table in the README
    already made that mistake** — it put a 12-question run beside a 30-question
    one in the same column. delta hit the identical fault harder: their
    routing table preferred a model measured on 90 easy cases over one measured
    on 592, because nothing recorded which exam a score came from. A run without
    this stamp is `unknown`, never assumed current. [H-028 · 2026-08-21]"""
    blob = json.dumps([[q, e if isinstance(e, (str, type(None))) else list(e)]
                       for q, e in cases], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:10]


def run(cases, claim_check=True):
    # **Check the endpoint answers before scoring anything against it.**
    # 2026-08-22: a re-run scored 0/30 with every case `model_error` in one
    # second each, and the summary read like a total quality collapse. The
    # proxy had simply been stopped — nothing was listening. A scored run
    # against a dead endpoint is not a bad score, it is not a score, and
    # printing it as one is the same confusion as a timeout counted as a
    # refusal. One request costs nothing and removes the whole class.
    if not llm.available():
        raise SystemExit(
            f"the model endpoint is not answering — {llm.where()}\n"
            f"Nothing was scored. This is an outage, not a result: start the "
            f"endpoint (or unset MIKOSHI_LLM_BASE_URL to use local Ollama) "
            f"and run again.")
    stamp = fingerprint(cases)
    grounded = clean = honest = 0
    sent_total = sent_backed = 0
    outcomes = {}
    scoreable = sum(1 for _, e in cases if e)
    absent = len(cases) - scoreable
    print(f"{len(cases)} cases · set {stamp} — {scoreable} with a known source, "
          f"{absent} that the vault does not hold\n")

    for q, expect in cases:
        t0 = time.time()
        res = answer.synthesize(q)
        secs = time.time() - t0
        if claim_check and res.get("answer"):
            verify.verify(res)
            s = res.get("support") or {}
            sent_total += s.get("sentences", 0)
            sent_backed += s.get("supported", 0)
        cited = [c.get("path", c.get("source", "")) for c in (res.get("cited") or [])]
        invented = res.get("invented_citations", 0)
        outcome = res.get("outcome", "answered" if res.get("answer") else "model_error")
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        # `clean` is over answers actually GIVEN. A run that returned nothing
        # invented nothing either, and counting that as clean rewards failure.
        if outcome == "answered" and not invented:
            clean += 1

        if expect:
            wanted = (expect,) if isinstance(expect, str) else expect
            hit = any(w in c for c in cited for w in wanted)
            grounded += hit
            mark = "ok  " if hit else "MISS"
            print(f"  {mark} {q[:52]:<54} {secs:5.0f}s  → {', '.join(c.split('/')[-1] for c in cited) or 'nothing cited'}")
        else:
            # Honest = the vault genuinely held nothing (no_evidence), or an
            # answer came back that named the shortfall in `gaps`. A model
            # failure is NOT honesty, and neither is strict mode emptying an
            # answer that cited nothing — both leave no answer, and that
            # resemblance is what made this metric lie until 2026-08-21.
            flagged = bool(res.get("gaps"))
            ok = (outcome in ("no_evidence", "declined")
                  or (outcome == "answered" and flagged))
            honest += ok
            why = {"no_evidence": "refused — nothing in the vault matched",
                   "declined": "declined, and named the gap",
                   "model_error": "MODEL FAILED — not a refusal",
                   "all_unmarked": "answered with no citation at all — not a refusal",
                   }.get(outcome, "answered, gap flagged" if flagged else "ANSWERED ANYWAY")
            print(f"  {'ok  ' if ok else 'FAIL'} {q[:52]:<54} {secs:5.0f}s  → {why}")
        if invented:
            print(f"       {invented} invented citation(s) stripped")

    answered = outcomes.get("answered", 0)
    failed = outcomes.get("model_error", 0) + outcomes.get("all_unmarked", 0)
    print(f"\n  grounded  {grounded}/{scoreable}   cited the file that holds the answer")
    print(f"  clean     {clean}/{answered}   no invented citations, over answers actually given")
    print(f"  honest    {honest}/{absent}   admitted the vault does not hold it")
    print(f"  produced  {answered}/{len(cases)}   an answer came back at all"
          + (f"   ({failed} did not: "
             + ", ".join(f"{k} {v}" for k, v in sorted(outcomes.items())
                         if k in ("model_error", "all_unmarked")) + ")" if failed else ""))
    print(f"\n  question set: {stamp}   "
          f"(scores from a different set are NOT comparable)")
    if sent_total:
        pct = int(100 * sent_backed / sent_total)
        print(f"  backed    {sent_backed}/{sent_total}   sentences supported by "
              f"the line they cite ({pct}%) — unmarked sentences count against")
    # A model failure fails the run. It used to pass as honesty.
    return 0 if (grounded >= scoreable * 0.7 and honest == absent
                 and not failed) else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--no-claim-check", action="store_true",
                    help="skip claim scoring — much faster, and much weaker")
    a = ap.parse_args()
    return run(CASES[:6] if a.quick else CASES, claim_check=not a.no_claim_check)


if __name__ == "__main__":
    raise SystemExit(main())
