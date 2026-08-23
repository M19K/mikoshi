#!/usr/bin/env python3
"""
llm.py — the one place the funnel and the synthesis layer talk to a model.

**Two routes, and local is still the default.** With nothing set in the
environment this drives `gpt-oss:20b` on local Ollama exactly as before: free,
private, no credential anywhere.

`gpt-oss:20b` must be driven through /api/chat, not /api/generate: it is a
harmony-format model and raw prompt mode returns its scratchpad instead of an
answer. `think: "low"` keeps latency down.

**The second route is project-four** — an OpenAI-compatible endpoint running on
this machine that picks, per call, the model measured to hold quality on that
task rather than one hardcoded here:

    python3 -m project-four.serve --port 8787            # in another shell
    export MIKOSHI_LLM_BASE_URL=http://localhost:8787/v1
    export MIKOSHI_LLM_MODEL=project-four/text-faithful  # or project-four/auto

**Why this exists, measured 2026-08-22.** The local model is good at this work
when it answers — 97% of planted falsehoods caught, no false alarms — but it
holds 13 GB of RAM, takes about two minutes a question, and ran the machine out
of application memory during a scored run. The routed alternative is around two
hundredths of a cent per call. Free and cheap are not the same thing.

**Costing a run is not this file's job.** The router logs task, model, cost and
latency for every call it serves, which is what turns a predicted saving into a
measured one — see `project-four.shadow`.

Every caller must survive `None`. A model that is down degrades the caller to
its deterministic path; it never takes the run down.
"""
import json
import os
import urllib.error
import urllib.request

# Overridable because not everyone runs Ollama on this machine, on this
# port, or wants this model. Hardcoding any of the three is the difference
# between "works here" and "works". [no-hardcoding rule, @owner · 2026-07-16]
OLLAMA = os.environ.get("MIKOSHI_OLLAMA_URL", "http://localhost:11434")
MODEL = os.environ.get("MIKOSHI_LOCAL_MODEL", "gpt-oss:20b")

# Set to an OpenAI-compatible base URL to leave the local route. Unset means
# local Ollama, which is the default on purpose: switching to a paid route is a
# money decision and money decisions are never a default.
BASE_URL = (os.environ.get("MIKOSHI_LLM_BASE_URL") or "").rstrip("/")
REMOTE_MODEL = os.environ.get("MIKOSHI_LLM_MODEL") or "project-four/auto"

# Headroom above what the caller asked for, in output tokens.
#
# **Two numbers, because reasoning changes the size of the problem.** Measured
# on a real 3,244-token synthesis prompt, 2026-08-22: the model spent **1,800
# reasoning tokens at `effort: low` and 2,442 unconstrained**, all of it drawn
# from `max_tokens` before a single character of answer. With only 400 spare
# the answer is starved and comes back `null` — which every layer above then
# reports as "the model failed", and a whole 30-question run scored 0/30
# looking exactly like a quality collapse. Twice.
#
# So: 3,000 when reasoning is on, which clears the worst case measured with
# room over; 400 when it is off, where nothing competes for the budget.
REASONING_HEADROOM = 400
REASONING_HEADROOM_THINKING = 3000


def routed() -> bool:
    """True when calls leave local Ollama. Anything that reports a cost or
    writes a score needs to know which route ran, and re-reading the env var at
    each call site is how two places end up disagreeing about what happened."""
    return bool(BASE_URL)


def where() -> str:
    """One line naming the route in use, for logs and eval headers — so a score
    cannot be filed without saying which model produced it."""
    return f"{BASE_URL} · {REMOTE_MODEL}" if routed() \
        else f"{OLLAMA} · {MODEL} (local, free)"


def _key():
    """Only needed when the endpoint is not on this machine. project-four runs
    locally and holds its own upstream key — that is the point of it: the
    credential stays in one process instead of being copied per caller.

    For any remote endpoint the key comes from the vault's resolver under the
    `mikoshi-internal` label, because this is vault tooling. Never read a raw
    key out of the environment; that is how one product's spend lands on
    another product's bill."""
    if not BASE_URL or "localhost" in BASE_URL or "127.0.0.1" in BASE_URL:
        return "local-proxy-holds-its-own-key"
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from ledger.keys import resolve
    return resolve("openrouter", internal=True)


def available(timeout: int = 5) -> bool:
    try:
        if routed():
            with urllib.request.urlopen(f"{BASE_URL}/models", timeout=timeout) as r:
                return r.status == 200
        with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=timeout) as r:
            return MODEL.split(":")[0] in r.read().decode()
    except Exception:
        return False


def _post(url, body, headers, timeout):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _parse(content, as_json):
    # A provider can answer 200 with `content: null` — a reasoning model that
    # spent its whole budget thinking does exactly that. Every caller here is
    # promised None on failure, so this must not raise.
    if content is None:
        return None
    if not as_json:
        return content
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start, end = content.find("{"), content.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(content[start:end + 1])
            except json.JSONDecodeError:
                return None
        return None


def chat(prompt: str, system: str = "", *, as_json: bool = True,
         max_tokens: int = 1200, timeout: int = 300, reasoning: bool = True):
    """One turn. Returns parsed JSON (or text), or None on any failure.

    Both routes are held to one contract deliberately. A caller that has to know
    which endpoint answered is a caller that will eventually be wrong about it,
    and the reason this file exists is that there is exactly one place to look.
    """
    messages = ([{"role": "system", "content": system}] if system else []) \
        + [{"role": "user", "content": prompt}]
    try:
        if routed():
            # **Reasoning tokens are spent out of `max_tokens`.** Measured
            # 2026-08-22 on the first routed call: `max_tokens: 200` came back
            # `finish_reason: "length"` with a full paragraph of `reasoning`
            # and `content: null` — the model thought its entire budget away
            # and never answered. This is the same trap the local model set
            # (`truncated-model-output-is-worse-than-empty`), and it is worse
            # hosted, because you are billed for the thinking that produced
            # nothing. So: ask for the cheapest reasoning the model offers, and
            # keep a floor of headroom underneath the caller's request so the
            # answer cannot be starved by it.
            body = {"model": REMOTE_MODEL, "messages": messages,
                    "temperature": 0,
                    "max_tokens": max(max_tokens, 512) + (
                        REASONING_HEADROOM_THINKING if reasoning
                        else REASONING_HEADROOM),
                    }
            # **Reasoning defaults ON, and that is a correction.** [2026-08-22]
            # This file shipped with `reasoning: {"enabled": false}` for every
            # routed call, on the grounds that it was 6x cheaper, 5.75x faster
            # and returned a real answer where `effort: low` had starved the
            # output. All of that is true and none of it made it free:
            # project-four re-measured the same exam at both settings and
            # **catch fell 94→90 on qwen3.7-flash, 100→67 and 100→84 on two
            # others.** It was not waste; it was doing the work.
            #
            # So a model at two reasoning settings is **two candidates** with
            # different cost, latency AND quality, and a score taken at one is
            # not comparable to a score taken at the other. Callers that judge
            # — classification, claim-checking — keep reasoning. A caller that
            # has measured its own task and found it does not need it passes
            # `reasoning=False` and takes the saving deliberately.
            #
            # Not set from taste: the default is ON because switching it off is
            # the change now measured to cost quality, and there is no
            # measurement yet for this vault's own tasks.
            if not reasoning:
                body["reasoning"] = {"enabled": False}
            if as_json:
                body["response_format"] = {"type": "json_object"}
            data = _post(f"{BASE_URL}/chat/completions", body,
                         {"Authorization": f"Bearer {_key()}"}, timeout)
            content = data["choices"][0]["message"]["content"]
        else:
            body = {"model": MODEL, "messages": messages, "stream": False,
                    "think": "low",
                    "options": {"temperature": 0, "num_predict": max_tokens}}
            if as_json:
                body["format"] = "json"
            data = _post(f"{OLLAMA}/api/chat", body, {}, timeout)
            content = data["message"]["content"]
    except Exception:
        return None
    return _parse(content, as_json)
