#!/usr/bin/env python3
"""
classify.py — Stage 3. Category, confidence, and the entities named.

Category decides whether an item is kept at all and how long it lives
(Information Lifecycle → Stage 3). Two passes:

  1. deterministic signals — cheap, auditable, and they run even with no model
  2. the local model, in batches of 8, for the judgment the signals cannot make

**The recall bias is implemented here, not just documented.** Practical tooling
is the primary domain, so a strong deterministic tooling signal overrides a
model verdict of `news`. A missed tool is unrecoverable; a noisy tooling section
costs a two-second skim. That override is recorded on the item, never silent.
"""
import json
import re

from . import learn, llm

CATEGORIES = ("tooling", "workflow", "concept", "industry", "news")

HALF_LIFE = {"tooling": 180, "workflow": 365, "concept": 1095,
             "industry": 365, "news": 7}

# Deterministic tooling signals. Each is a thing you can point at in the text.
SIGNALS = {
    "mcp": re.compile(r"\bMCP\b|model context protocol", re.I),
    "repo": re.compile(r"github\.com/[\w.-]+/[\w.-]+", re.I),
    "install": re.compile(r"\b(npm install|pip install|brew install|npx |uvx |docker run)", re.I),
    "agent": re.compile(r"\b(agent|subagent|agentic|tool[- ]use|function calling)\b", re.I),
    "build": re.compile(r"\b(how i built|i built|step[- ]by[- ]step|tutorial|walkthrough|"
                        r"here'?s how|guide to)\b", re.I),
    "release": re.compile(r"\b(released?|launch(ed|ing)?|introducing|now available|v?\d+\.\d+)\b", re.I),
    "codeish": re.compile(r"\b(API|SDK|CLI|repo|open[- ]source|self[- ]host|prompt|"
                          r"context window|embedding|RAG|fine[- ]tun)\w*\b", re.I),
}
STRONG = ("mcp", "repo", "install")

# Gate 3 (relevance). Hacker News alone puts ~30 Show HN posts a day into the
# funnel, and a karaoke game with a GitHub repo trips every tooling signal there
# is. This gate asks a different question from "is it tooling": is it about AI,
# agents, or building with them at all?
#
# It does NOT narrow the recall bias. The bias says never miss an AI tool; this
# says a blood-sugar logbook was never in the domain to begin with.
DOMAIN = re.compile(
    r"\b(AI|A\.I\.|artificial intelligence|machine learning|ML|LLM|GPT|GPT-\d|Claude|"
    r"Anthropic|OpenAI|Gemini|Llama|Mistral|Grok|DeepSeek|Qwen|transformer|neural|"
    r"agent|agentic|MCP|model context protocol|prompt|embedding|vector|RAG|fine[- ]tun|"
    r"inference|token|context window|copilot|cursor|codex|diffusion|multimodal|"
    r"chatbot|自动|AGI|frontier model|foundation model|open[- ]weights?)\b", re.I)

SYSTEM = (
    "You classify items for a personal AI-knowledge vault. The owner's priority, "
    "in order: (1) practical tooling and implementation — tools, MCPs, repos, SDKs, "
    "workflows, how people actually build; (2) startup ecosystem; (3) business "
    "reaction. Be generous with 'tooling': when an item plausibly helps someone "
    "build, prefer tooling over news. Answer only with JSON."
)

TEMPLATE = """Classify each item.

Categories:
- tooling: a tool, MCP, repo, SDK, model release, or anything usable to build with
- workflow: a repeatable way of working; a pattern or process, not a product
- concept: a durable technical idea or named discipline
- industry: a structural shift, funding round, market move, company strategy
- news: an event with no durable value once the week passes

Also answer, separately from the category: is this item about AI, ML, agents, or
tools for building with them? A well-built app that has nothing to do with AI is
`"ai": false` even though it is a tool.

Return ONLY: {{"results":[{{"i":<index>,"category":"...","confidence":0.0-1.0,
"ai":true|false,"entities":["named tools, models, companies, people"],
"why":"<8 words max>"}}]}}
One object per item, same indexes, nothing else.

ITEMS:
{items}"""


def signals_for(text: str):
    return [k for k, rx in SIGNALS.items() if rx.search(text)]


def heuristic(env: dict):
    """The no-model path. Also the tie-breaker the model cannot override."""
    text = f"{env['title']}\n{env.get('body', '')[:2000]}"
    hits = signals_for(text)
    strong = [h for h in hits if h in STRONG]
    if strong or len(hits) >= 3:
        return "tooling", 0.55 + 0.1 * len(strong), hits
    if "build" in hits:
        return "workflow", 0.45, hits
    return "news", 0.35, hits


def _prompt_block(batch):
    out = []
    for i, e in enumerate(batch):
        body = re.sub(r"\s+", " ", e.get("body") or "")[:900]
        out.append(f"[{i}] SOURCE: {e['source']} ({e.get('domain', '')})\n"
                   f"    TITLE: {e['title']}\n    TEXT: {body}")
    return "\n\n".join(out)


def classify(batch):
    """Classify a list of envelopes in place. Returns the same list.

    **The one place the funnel's judgment can improve.** `learn.examples()`
    returns the owner's own verdicts on real items — kept and discarded — and
    they are appended to the system prompt. It returns an empty string until
    enough verdicts exist and both sides are represented, and an empty string
    means this behaves exactly as it did before the loop was built. That is the
    intended common case, not a failure: a preference learned from three clicks
    is a guess with a mechanism attached.
    """
    system = SYSTEM + learn.examples()
    got = llm.chat(TEMPLATE.format(items=_prompt_block(batch)), system,
                   max_tokens=180 * len(batch) + 300)
    results = {}
    if isinstance(got, dict):
        for r in got.get("results", []) or []:
            try:
                idx = int(r.get("i"))
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(batch):
                results[idx] = r

    for i, env in enumerate(batch):
        hcat, hconf, hits = heuristic(env)
        r = results.get(i)
        if r and r.get("category") in CATEGORIES:
            env["category"] = r["category"]
            env["confidence"] = float(r.get("confidence") or 0.5)
            env["entities"] = [str(x) for x in (r.get("entities") or [])][:12]
            env["why"] = str(r.get("why") or "")[:80]
        else:
            env["category"], env["confidence"], env["entities"] = hcat, hconf, []
            env["why"] = "heuristic: " + ",".join(hits) if hits else "heuristic"

        # the recall bias, applied and recorded
        strong = [h for h in hits if h in STRONG]
        if strong and env["category"] == "news":
            env["category"] = "tooling"
            env["confidence"] = min(env["confidence"], 0.5)
            env["why"] = f"recall-bias({','.join(strong)}) over model:news"

        # gate 3 — relevance. The regex rescues the model, never the reverse:
        # a named model or "MCP" in the text keeps an item the model called
        # off-domain, because recall on this domain is what matters.
        in_domain = bool(DOMAIN.search(f"{env['title']} {(env.get('body') or '')[:1500]}"))
        env["relevant"] = in_domain or bool(r and r.get("ai"))
        if not env["relevant"]:
            env["off_domain"] = True

        env["signals"] = hits
        env["half_life"] = HALF_LIFE[env["category"]]
    return batch


def run(envelopes, batch_size: int = 8, progress=None):
    if not llm.available():
        for env in envelopes:
            cat, conf, hits = heuristic(env)
            env["category"], env["confidence"] = cat, conf
            env["entities"], env["signals"] = [], hits
            env["why"] = "no model available — deterministic only"
            env["relevant"] = bool(DOMAIN.search(f"{env['title']} {(env.get('body') or '')[:1500]}"))
            env["half_life"] = HALF_LIFE[cat]
        return envelopes
    for start in range(0, len(envelopes), batch_size):
        classify(envelopes[start:start + batch_size])
        if progress:
            progress(min(start + batch_size, len(envelopes)), len(envelopes))
    return envelopes
