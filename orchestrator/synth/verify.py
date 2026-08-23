#!/usr/bin/env python3
"""
verify.py — does each sentence actually follow from the line it cites?

**The failure this exists for, measured 2026-08-21.** Asked what
`keyword=refresh` requires, the synthesis wrote *"the thread should clear its
scratch directory"* and cited two Open Board lines that genuinely contain the
words *"clear at the next refresh"* — about an entirely different subject. Both
citations resolve. Both lines exist. The claim is invented.

**No amount of citation checking catches that**, because the citation is real.
`answer.py` verifies that a marker *resolves*; this verifies that the line it
resolves to *supports the sentence*. They are different questions and the second
is the harder one.

**One claim, one line, no context — deliberately.** The verifier is a fresh call
that sees the sentence and the cited line and nothing else: not the question, not
the other evidence, not the rest of the answer. That is the whole point. The
author model had context and used it to bridge a gap; a verifier holding the same
context would bridge the same gap and agree with itself. Stripping the context is
what makes the check adversarial rather than confirmatory.

**Why it gets more necessary as retrieval improves.** Better recall means more
adjacent-but-unrelated material in the evidence set, so the chance of a plausible
wrong pairing goes UP with retrieval quality. Entity wiring on 2026-08-21 added
4 relevant notes per entity question — and made this class of error likelier at
the same time.

    python3 -m synth.verify "why is there one API key per product"
    python3 -m synth.verify --selftest      # proves it catches the known case
"""
import argparse
import re

from funnel import llm

MARKER = re.compile(r"\[([EDL])(\d+)\]")
# Sentence split that survives "e.g." and decimals well enough for prose.
SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z`\"'*])")

SYSTEM = (
    "You check whether one piece of evidence supports one claim. You are "
    "deliberately strict and you see no other context.\n"
    "\n"
    "Answer supported=true ONLY if the evidence states the claim, about the "
    "same subject. Shared vocabulary is not support: evidence saying that "
    "something clears at the next refresh does NOT support a claim that a "
    "DIFFERENT thing clears at the next refresh. Evidence that merely makes "
    "the claim plausible, or that would need a second fact to complete it, is "
    "not support either.\n"
    "\n"
    "If you are unsure, answer false. A missed real claim costs a sentence; an "
    "approved invented one costs the reader's trust in every other sentence."
)

TEMPLATE = """CLAIM:
{claim}

EVIDENCE (the only thing you may rely on):
{evidence}

JSON only:
{{"supported": true or false, "why": "one short sentence"}}"""


def split_claims(answer: str) -> tuple[list[dict], list[str]]:
    """(sentences carrying a marker, sentences carrying none).

    **The second list is why this returns a pair.** A first version counted only
    marked sentences and reported "1/1 supported" on an answer whose other two
    sentences cited nothing at all — a perfect score covering an answer that was
    two-thirds unchecked. An unmarked sentence is not a passing sentence; it is
    an unverifiable one, and the metric has to say so."""
    marked, unmarked = [], []
    for raw in SENTENCE.split(answer or ""):
        s = raw.strip()
        if len(s) < 15:
            continue
        marks = [(k, int(n)) for k, n in MARKER.findall(s)]
        if marks:
            marked.append({"claim": MARKER.sub("", s).replace("  ", " ").strip(),
                           "markers": marks, "raw": s})
        else:
            unmarked.append(s)
    return marked, unmarked


def _evidence_text(kind: str, idx: int, ev: list, dec: list,
                   lrn: list | None = None) -> str | None:
    pool = {"E": ev, "D": dec, "L": lrn or []}[kind]
    if not (1 <= idx <= len(pool)):
        return None
    item = pool[idx - 1]
    if kind == "E":
        return f"{item['path']}:{item['line']}\n{item['text']}"
    if kind == "L":
        return (f"A recorded learning ({item.get('type','')}, confidence "
                f"{item.get('confidence','?')}/10).\n{item.get('key','')}\n"
                f"{str(item.get('insight',''))[:700]}")
    over = ", ".join(item.get("over") or [])
    return (f"A recorded decision.\nCHOSE: {item.get('chose','')}\n"
            f"OVER: {over}\nBECAUSE: {str(item.get('why',''))[:600]}")


def check(claim: str, evidence_text: str, timeout: int = 120) -> dict:
    raw = llm.chat(TEMPLATE.format(claim=claim, evidence=evidence_text),
                   system=SYSTEM, as_json=True, max_tokens=300, timeout=timeout)
    if not isinstance(raw, dict) or "supported" not in raw:
        # An unreachable or unusable verifier must not read as approval.
        return {"supported": None, "why": "verifier gave no usable verdict"}
    return {"supported": bool(raw["supported"]),
            "why": str(raw.get("why", ""))[:200]}


def verify(res: dict, timeout: int = 120) -> dict:
    """Score every claim in a synthesis result. Adds `claims` and `support`."""
    ev, dec = res.get("evidence") or [], res.get("rejected") or []
    lrn = res.get("learnings") or []
    claims, unmarked = split_claims(res.get("answer") or "")
    for c in claims:
        verdicts = []
        for kind, idx in c["markers"]:
            text = _evidence_text(kind, idx, ev, dec, lrn)
            if text is None:
                verdicts.append({"marker": f"[{kind}{idx}]", "supported": False,
                                 "why": "marker does not resolve"})
                continue
            v = check(c["claim"], text, timeout=timeout)
            v["marker"] = f"[{kind}{idx}]"
            verdicts.append(v)
        c["verdicts"] = verdicts
        # One supporting citation is enough; a claim with none is unsupported.
        c["supported"] = any(v["supported"] for v in verdicts)

    ok = sum(1 for c in claims if c["supported"])
    total = len(claims) + len(unmarked)
    res["claims"] = claims
    res["unmarked"] = unmarked
    res["support"] = {
        "sentences": total, "marked": len(claims), "supported": ok,
        "unsupported": len(claims) - ok, "unmarked": len(unmarked),
        # The number that matters: of everything asserted, how much is backed?
        "verified_fraction": round(ok / total, 2) if total else 0.0,
    }
    return res


def render(res: dict) -> str:
    s = res.get("support") or {}
    out = [f"CLAIM CHECK — {s.get('supported',0)} of {s.get('sentences',0)} "
           f"sentences are backed by a line that supports them "
           f"({int(100*s.get('verified_fraction',0))}%)"]
    if s.get("unmarked"):
        out.append(f"  {s['unmarked']} sentence(s) cite nothing at all — "
                   f"unverifiable, not passing:")
        for u in res.get("unmarked") or []:
            out.append(f"        {u[:88]}")
    for c in res.get("claims") or []:
        mark = "ok  " if c["supported"] else "UNSUPPORTED"
        out.append(f"  {mark} {c['claim'][:88]}")
        if not c["supported"]:
            for v in c["verdicts"]:
                out.append(f"        {v['marker']} — {v['why']}")
    return "\n".join(out)


def selftest() -> int:
    """The known case must fail, and a true one must pass."""
    bad_claim = ("the thread should clear its scratch directory or any temporary "
                 "state for the next refresh cycle")
    bad_evidence = ("05-Orchestrator/Open Board.md:76\n"
                    "completed work and carried its own instruction to clear at "
                    "the next refresh.")
    good_claim = "every API key lives in one file and you use the one labelled for your project"
    good_evidence = ("CLAUDE.md:302\n**Every API key lives in one file, and you "
                     "use the one labelled for your project.** [@owner · 2026-08-20]")

    a = check(bad_claim, bad_evidence)
    b = check(good_claim, good_evidence)
    print(f"  known conflation  supported={a['supported']}  ({a['why']})")
    print(f"  true claim        supported={b['supported']}  ({b['why']})")
    ok = a["supported"] is False and b["supported"] is True
    print("\n  PASS — catches the invented claim, keeps the real one" if ok
          else "\n  FAIL — the verifier does not discriminate")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question", nargs="?")
    ap.add_argument("--scope")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest or not a.question:
        return selftest()

    from . import answer
    res = answer.synthesize(a.question, scope=a.scope)
    if not res.get("answer"):
        print(res.get("error", "no answer"))
        return 1
    print(answer.render(res))
    print()
    print(render(verify(res)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
