#!/usr/bin/env python3
"""
answer.py — the synthesis itself: a cited answer, entirely on local models.

**Five things this does that a page-citing answer layer does not.**

1. **Citations are verified, not trusted.** Every `[E3]` the model emits is
   checked against the evidence actually handed to it, and an invented marker is
   stripped and counted. The usual fallback is to parse markers out of prose
   when the model omits the structured field — reasonable, but it renders
   whatever it finds. Here a citation that does not resolve to a
   real file and line **cannot survive**, which matters far more on a local model
   than a frontier one, because small models invent markers more often.

2. **The answer separates authority from assertion.** Each citation carries
   whether the owner decided it or an agent wrote it, and the summary states the mix.
   *"Two of the four claims rest on lines an agent wrote"* is a different answer
   from *"all four are his"*, and no page-citing brain can say either.

3. **It answers "why not X?" and "what did we learn?".** Rejected alternatives
   come from `Decisions.jsonl` — 165 of them, all carrying `over`. Hard-won
   findings come from `Learnings.jsonl` — 292 of them, each with a confidence
   score. **Both were invisible to retrieval until 2026-08-21**, because
   `funnel.retrieve` builds its corpus from markdown and these are JSONL: the
   vault's most structured records were its least reachable. See
   `decisions.py` and `learnings.py`.

4. **It states how much of the available material it used.** When a question
   names an entity the funnel already indexed, the answer reports *"OpenRouter
   appears in 20 notes; this answer draws on 3."* That is a **completeness**
   signal and it is not the same as a gap signal: gaps say what the vault does
   not hold, coverage says how much of what it does hold was actually read.
   Neither `think` nor any page-citing brain gives the second, and until
   2026-08-21 nothing in Mikoshi read the entity pages at all — 135 of 177 had
   no inbound links.

5. **It runs with no API key.** `funnel.llm` drives `gpt-oss:20b` on local
   Ollama, which is the whole posture. The common failure is a system that
   advertises a local path and still demands `OPENAI_API_KEY` somewhere inside
   it. There is no key in this path to demand.

**The honest limit, stated here so it is not discovered later:** a local model
writes a weaker answer than Sonnet. The defence is not that the model is good —
it is that every claim it makes is pinned to a line you can open. A wrong
sentence with a real citation is caught in seconds; a fluent one without is not
caught at all.

    python3 -m synth.answer "why is there one API key per product"
    python3 -m synth.answer "what is blocking the job search" --scope alpha
"""
import argparse
import json
import re

from funnel import llm

from . import decisions, evidence, learnings

MARKER = re.compile(r"\[([EDL])(\d+)\]")

SYSTEM = (
    "You answer questions about a private working vault, strictly from the "
    "evidence given. You never use outside knowledge.\n"
    "\n"
    "EVERY factual sentence MUST end with the marker of the evidence it came "
    "from, like [E2] or [D1]. A sentence you cannot mark is a sentence you "
    "must delete — never write an unmarked claim.\n"
    "\n"
    "Choose the marker by checking that the line states that claim about THE "
    "SUBJECT OF THE QUESTION. Evidence often repeats words while discussing "
    "something else: a line saying something 'clears at the next refresh' "
    "about one topic is NOT evidence that the thing you were asked about "
    "clears at the next refresh. When a line is about a different subject, "
    "drop that point rather than citing it, and never merge two lines into a "
    "claim neither makes alone.\n"
    "\n"
    "Three marked sentences you are certain of beat five that round out the "
    "answer. Put anything the evidence does not cover in gaps. Write plainly, "
    "never pad."
)

TEMPLATE = """Question: {question}

EVIDENCE — lines from the vault. The label says who wrote each one.
{evidence}
{rejections}
Reply with JSON only:
{{
  "answer": "2-5 sentences. Every factual sentence ends with its marker, e.g. [E2], [D1] or [L3].",
  "gaps": ["what the question asked that the evidence above does not cover"],
  "conflicts": ["any two pieces of evidence that disagree, named by marker"]
}}"""


def _render(text: str, ev: list[dict], dec: list[dict],
            lrn: list[dict] | None = None) -> tuple[str, list[dict], int]:
    """Bind markers to real locations; drop any that do not exist."""
    used, invented = [], 0

    def sub(m):
        nonlocal invented
        kind, idx = m.group(1), int(m.group(2))
        pool = {"E": ev, "D": dec, "L": lrn or []}[kind]
        if 1 <= idx <= len(pool):
            item = pool[idx - 1]
            if item not in used:
                used.append(item)
            return m.group(0)
        invented += 1
        return ""

    return MARKER.sub(sub, text).replace("  ", " ").strip(), used, invented


def drop_unmarked(body: str) -> tuple[str, list[str]]:
    """Remove sentences that cite nothing. Returns (kept, dropped).

    **This is the thesis applied to its own output.** The whole claim of this
    module is that every sentence is pinned to a line you can open. A sentence
    with no marker is not a weaker version of that — it is the thing the module
    exists to refuse. Measured 2026-08-21 before this existed: only 37% of
    asserted sentences were backed, and most of the shortfall was sentences
    citing nothing at all rather than sentences citing badly.

    The cost is real and worth naming: answers get shorter and read more
    clipped, because the connective prose a model writes to round out a
    paragraph is exactly what carries no citation."""
    kept, dropped = [], []
    for raw in re.split(r"(?<=[.!?])\s+(?=[A-Z`\"'*])", body or ""):
        s = raw.strip()
        if not s:
            continue
        (kept if MARKER.search(s) else dropped).append(s)
    return " ".join(kept), dropped


# Which stores the answer is allowed to read. All three on is the product;
# switching one off is how `synth.ablate` finds out what that store is actually
# worth. Named here rather than passed as three booleans so a caller cannot
# half-configure it.
ALL_TIERS = ("evidence", "decisions", "learnings")


def synthesize(question: str, scope: str | None = None, k: int = 6,
               timeout: int = 300, strict: bool = True,
               tiers: tuple = ALL_TIERS) -> dict:
    """`tiers` exists so each store can be switched off and measured.

    Until 2026-08-23 this layer read all three and nobody could say what any
    one of them contributed — only that the whole thing scored 85%. A number
    for the whole is not a number for the parts, and a store that adds nothing
    is a store still paying for itself in tokens on every single call.
    """
    ev = evidence.gather(question, scope=scope, k=k) if "evidence" in tiers else []
    dec = decisions.rejected(question, project=scope) if "decisions" in tiers else []
    lrn = learnings.matching(question, project=scope) if "learnings" in tiers else []
    cover = evidence.coverage(question)

    if not ev and not dec and not lrn:
        # A PRINCIPLED refusal: retrieval swept the vault and matched nothing,
        # so "it has not been written down" is the true answer. Tagged so the
        # eval can tell this apart from the model failing — see `outcome`.
        return {"question": question, "scope": scope, "answer": None,
                "outcome": "no_evidence",
                "error": "Nothing in the vault matches. That is an answer: it "
                         "has not been written down."}

    rej = ""
    if dec:
        rej = ("\nREJECTED ALTERNATIVES — options considered and turned down.\n"
               + decisions.format_for_prompt(dec) + "\n")
    if lrn:
        rej += ("\nLEARNINGS — things found out the hard way, with confidence.\n"
                + learnings.format_for_prompt(lrn) + "\n")

    prompt = TEMPLATE.format(question=question,
                             evidence=evidence.format_for_prompt(ev),
                             rejections=rej)

    # Ollama is not reliably deterministic even at temperature 0, and the
    # observed failure is not a bad answer — it is a CORRECT answer with no
    # markers at all, which would then be presented as grounded when nothing
    # was checked. Measured 2026-08-21: it happens intermittently on the same
    # question. One retry, then say so plainly rather than dress it up.
    def declined(r):
        """A well-formed DECLINE: no answer, plus a gap naming what is missing.

        Measured 2026-08-21 and it inverts the earlier reading. Asked three
        questions the vault genuinely does not hold, the local model returned
        `{"answer": "", "gaps": ["which model was measured best for classify"]}`
        — twice, identically. That is the layer working, and it was being
        reported to the reader as *"the local model returned nothing usable"*.
        An empty answer has three causes and only this one is the model doing
        its job; collapsing them blamed the model for being right."""
        return (isinstance(r, dict) and not str(r.get("answer", "")).strip()
                and bool(r.get("gaps")))

    raw = None
    for attempt in range(2):
        raw = llm.chat(prompt, system=SYSTEM, as_json=True, max_tokens=1200,
                       timeout=timeout)
        if raw and isinstance(raw, dict) and MARKER.search(str(raw.get("answer", ""))):
            break
        # A decline is a final answer, not a failed attempt. Retrying it buys
        # nothing and costs a second full generation on a local model.
        if declined(raw):
            break

    if declined(raw):
        return {"question": question, "scope": scope, "answer": None,
                "outcome": "declined",
                "gaps": [str(g) for g in raw.get("gaps") if str(g).strip()],
                "evidence": ev, "rejected": dec, "learnings": lrn,
                "coverage": cover,
                "error": "The vault does not record this. What is missing is "
                         "named below, and the closest evidence follows it."}

    if not raw or not isinstance(raw, dict) or not raw.get("answer"):
        # NOT a refusal. The evidence was there and the model failed to use it.
        # Scoring this as honesty is how a broken run reads as a good one.
        return {"question": question, "scope": scope, "answer": None,
                "outcome": "model_error",
                "evidence": ev, "rejected": dec,
                "error": "The local model returned nothing usable. The evidence "
                         "above is still real — read it directly."}

    body, used, invented = _render(str(raw["answer"]), ev, dec, lrn)
    dropped = []
    if strict:
        body, dropped = drop_unmarked(body)
        if body:
            _, used, _ = _render(body, ev, dec, lrn)
    uncited = not used
    lines = [u for u in used if "line" in u]
    mix = {}
    for u in lines:
        mix[u["authority"]] = mix.get(u["authority"], 0) + 1

    # Strict mode deletes every sentence that cites nothing. When that empties
    # the answer the model DID reply — it simply asserted only unmarked claims,
    # which is a grounding failure and not a decline. It used to leave an empty
    # string that read as a refusal to anything testing `if answer`.
    outcome = "answered" if body else "all_unmarked"

    return {
        "question": question, "scope": scope,
        "outcome": outcome,
        "answer": body,
        "error": None if body else (
            "The model answered, but every sentence cited nothing and strict "
            "mode removed them all. That is a grounding failure, not a "
            "refusal — the evidence below is still real."),
        "gaps": [str(g) for g in (raw.get("gaps") or []) if str(g).strip()],
        "conflicts": [str(c) for c in (raw.get("conflicts") or []) if str(c).strip()],
        "cited": used, "evidence": ev, "rejected": dec, "learnings": lrn,
        "coverage": cover,
        "invented_citations": invented, "uncited": uncited,
        "dropped_unmarked": dropped,
        "authority": mix,
    }


def render(res: dict) -> str:
    out = []
    scope = f" · scope: {res['scope']}" if res.get("scope") else " · whole vault"
    out.append(f"Q: {res['question']}{scope}\n")
    if res.get("error"):
        out.append(res["error"])
        # A decline names what is missing. Printing the error without the gap
        # would leave the reader with a shrug where the useful part is.
        if res.get("gaps"):
            out.append("\nNOT RECORDED — what the question asked and the vault "
                       "does not hold:")
            out += [f"  · {g}" for g in res["gaps"]]
        if res.get("evidence"):
            out.append("\nEvidence found:")
            for n, p in enumerate(res["evidence"][:6], 1):
                out.append(f"  [E{n}] {p['path']}:{p['line']}  {p['text'][:90]}")
        return "\n".join(out)

    out.append(res["answer"] + "\n")

    if res["cited"]:
        out.append("SOURCES — open any of these and check the claim:")
        for item in res["cited"]:
            if "line" in item:
                who = {"owner": "the owner decided", "owner-block": "block the owner authored",
                       "agent": "an agent wrote", "unattributed": "unattributed"}[item["authority"]]
                n = res["evidence"].index(item) + 1
                out.append(f"  [E{n}] {item['path']}:{item['line']}  ({who})")
            elif item in (res.get("learnings") or []):
                n = res["learnings"].index(item) + 1
                out.append(f"  [L{n}] {item['project']} learning, "
                           f"{str(item.get('ts',''))[:10]} — {item.get('key','')} "
                           f"(confidence {item.get('confidence','?')}/10)")
            else:
                n = res["rejected"].index(item) + 1
                out.append(f"  [D{n}] {item['project']} decision, "
                           f"{str(item.get('ts',''))[:10]} — rejected: "
                           f"{', '.join(item.get('over') or [])}")
        out.append("")

    if res["authority"]:
        m = res["authority"].get("owner", 0) + res["authority"].get("owner-block", 0)
        a = res["authority"].get("agent", 0)
        u = res["authority"].get("unattributed", 0)
        out.append(f"AUTHORITY — {m} cited line(s) the owner authored · {a} an agent "
                   f"wrote · {u} unattributed")

    for c in res.get("coverage") or []:
        drew = len({e["path"] for e in res["cited"] if "path" in e})
        out.append(f"COVERAGE — {c['name']} appears in {c['notes']} notes; "
                   f"this answer drew on {drew}")

    if res["gaps"]:
        out.append("\nNOT ANSWERED — the vault does not cover this:")
        out += [f"  · {g}" for g in res["gaps"]]

    if res["conflicts"]:
        out.append("\nCONFLICTS — evidence that disagrees:")
        out += [f"  · {c}" for c in res["conflicts"]]

    if res.get("dropped_unmarked"):
        out.append(f"{len(res['dropped_unmarked'])} unmarked sentence(s) removed "
                   f"— they cited nothing, so they could not be checked.")

    if res.get("uncited"):
        out.append("\nUNCITED — the model returned no usable citation on two "
                   "attempts, so nothing above has been checked against a line. "
                   "Treat this as a lead, not an answer, and read the evidence "
                   "yourself:")
        for n, p_ in enumerate(res["evidence"][:5], 1):
            out.append(f"  [E{n}] {p_['path']}:{p_['line']}")

    if res["invented_citations"]:
        out.append(f"\n{res['invented_citations']} invented citation(s) were "
                   f"stripped — the model referenced evidence it was not given.")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question")
    ap.add_argument("--scope", help="one project folder; default is the whole vault")
    ap.add_argument("-k", type=int, default=6)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--loose", action="store_true",
                    help="keep sentences that cite nothing (they cannot be checked)")
    a = ap.parse_args()
    res = synthesize(a.question, scope=a.scope, k=a.k, strict=not a.loose)
    print(json.dumps(res, indent=2, default=str) if a.json else render(res))
    return 0 if res.get("answer") else 1


if __name__ == "__main__":
    raise SystemExit(main())
