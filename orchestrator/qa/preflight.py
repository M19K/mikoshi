#!/usr/bin/env python3
"""
preflight.py — prove the configured model can answer before a QA run starts.

**The failure this exists for is silent.** `gpt-oss:20b` over Ollama's
OpenAI-compatible `/v1` endpoint spends most of a small budget on a reasoning
trace before it writes anything, and `/v1` has no field to turn that off —
`think` is an Ollama parameter, not an OpenAI one. When the budget runs out the
call still returns HTTP 200, with `finish_reason: "length"` and no error at all.

Measured on this machine 2026-08-20, agent-shaped prompts, `gpt-oss:20b`:

    prompt   cap=32    -> 32 tok   length   EMPTY STRING
    prompt   cap=256   -> 222 tok  stop     correct answer
    plan     cap=256   -> 256 tok  length   TRUNCATED MID-TABLE
    plan     uncapped  -> 440 tok  stop     correct answer

Two things follow, and the second is not in H-019. **The threshold is
prompt-dependent, not a fixed number** — 256 is fine for a one-line decision and
not fine for a plan, so "keep the cap above 300" is a rule that passes and then
fails on a longer prompt. And **truncation is not always empty**: a cut answer
comes back as plausible partial text, which is worse than nothing because it
reads like a verdict.

`reasoning_effort: "low"` IS honoured by Ollama's `/v1` and costs 31 tokens
instead of 222 — a 7x reduction — but only if the client sends it, and Hercules
is upstream code we do not control. So this file does not try to make the model
cheap. It makes the failure loud, before a run rather than inside one.

    python3 preflight.py                       # defaults, exit 0 if healthy
    python3 preflight.py --model gpt-oss:20b --base-url http://localhost:11434/v1
"""
import os
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

# Two shapes, deliberately. A one-line decision is the cheapest thing an agent
# asks for; a plan is the most expensive. A model that passes one and fails the
# other is exactly the case a single probe would have called healthy.
PROBES = [
    ("decision", "You are a browser agent. The page is a login form with fields "
                 "Email and Password and a Sign in button. Decide the next single "
                 "action. Answer in one short line."),
    ("plan", "You are a browser agent planning a test. Goal: open the site, scroll "
             "to the Business cases section, confirm at least one case is listed. "
             "List the steps, briefly."),
]


def ask(base_url, model, key, prompt, cap, timeout):
    body = {"model": model, "temperature": 0,
            "messages": [{"role": "user", "content": prompt}]}
    if cap:
        body["max_tokens"] = cap
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"})
    start = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode())
    c = d["choices"][0]
    return {"text": (c["message"].get("content") or "").strip(),
            "finish": c.get("finish_reason"),
            "tokens": d.get("usage", {}).get("completion_tokens", -1),
            "secs": time.time() - start}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default=os.environ.get("MIKOSHI_LOCAL_MODEL", "gpt-oss:20b"))
    ap.add_argument("--base-url", default=os.environ.get("MIKOSHI_OLLAMA_URL", "http://localhost:11434") + "/v1")
    ap.add_argument("--key", default="ollama")
    ap.add_argument("--cap", type=int, default=0,
                    help="max_tokens to send; 0 (default) sends none, which is "
                         "what a healthy configuration looks like")
    ap.add_argument("--timeout", type=int, default=180)
    args = ap.parse_args()

    print(f"preflight · {args.model} · {args.base_url}"
          + (f" · cap {args.cap}" if args.cap else " · no cap"))

    bad = []
    for name, prompt in PROBES:
        try:
            r = ask(args.base_url, args.model, args.key, prompt,
                    args.cap, args.timeout)
        except urllib.error.URLError as e:
            print(f"  {name:<9} UNREACHABLE — {e}")
            bad.append(f"{name}: unreachable")
            continue

        note = ""
        if not r["text"]:
            note = "EMPTY — the whole budget went to the reasoning trace"
        elif r["finish"] == "length":
            note = "TRUNCATED — partial text returned as if it were an answer"
        print(f"  {name:<9} {r['tokens']:>4} tok  {r['finish']:<6} "
              f"{r['secs']:5.1f}s  {r['text'][:52]!r}")
        if note:
            print(f"  {'':<9} {note}")
            bad.append(f"{name}: {note.split(' —')[0].lower()}")

    if bad:
        print("\nThis model cannot be trusted to answer under this configuration:")
        for b in bad:
            print(f"  · {b}")
        print("Raise or remove the completion cap, send reasoning_effort=low, "
              "or pass a different --model. Do not run QA against it — the "
              "symptom is a clean empty string, not an error.")
        return 1

    print("\nhealthy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
