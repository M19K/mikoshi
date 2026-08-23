#!/usr/bin/env python3
"""
distil.py — Stage 6. Compress to the smallest form that survives without the source.

Output matches the entry shape already used in `01-Knowledge Base/Tooling Sources/`
so a distilled entry can be merged there without reformatting: name, what it is,
source link, confidence.

Provenance is a real URL and a real retrieval date. **Never a fabricated Recall
card id** — the same rule the 2026-08-10 web-research entries followed.
"""
import pathlib
import re

from . import llm, store

KB = store.VAULT / "01-Knowledge Base" / "Tooling Sources"

SYSTEM = ("You distil items into one-entry knowledge-base records. Be concrete and "
          "specific: name the tool, say what it does, say what it is for. Never pad. "
          "Answer only with JSON.")

TEMPLATE = """Distil this into a knowledge-base entry.

Return ONLY:
{{"name":"<the thing itself — a tool/repo/pattern name, not a headline>",
 "what":"<1-2 sentences: what it is and what it does>",
 "why":"<1 sentence: why it matters to someone building with AI>",
 "kb_category":"<one of: {cats}>",
 "supersedes":"<name of the older thing this replaces, or empty>"}}

CATEGORY: {category}
SOURCE: {source}
TITLE: {title}
TEXT: {body}"""


def kb_categories():
    """Read the category list off disk — the folder is the registry, not a constant."""
    if not KB.exists():
        return ["Other and Reference"]
    return sorted(p.stem for p in KB.glob("*.md"))


def slug(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")[:60]


def fallback(env: dict) -> dict:
    """No model: the title is the name and the first sentences are the body."""
    body = re.sub(r"\s+", " ", env.get("body") or "").strip()
    return {"name": env["title"][:90],
            "what": body[:280] or env["title"],
            "why": "", "kb_category": "Other and Reference", "supersedes": ""}


def distil(env: dict) -> dict:
    cats = ", ".join(kb_categories())
    got = llm.chat(TEMPLATE.format(
        cats=cats, category=env.get("category", ""), source=env.get("source", ""),
        title=env["title"], body=re.sub(r"\s+", " ", env.get("body") or "")[:2500]),
        SYSTEM, max_tokens=600)
    d = got if isinstance(got, dict) and got.get("name") else fallback(env)
    if d.get("kb_category") not in kb_categories():
        d["kb_category"] = "Other and Reference"
    env["entry"] = {
        "name": str(d.get("name"))[:90].strip(),
        "what": str(d.get("what") or "").strip(),
        "why": str(d.get("why") or "").strip(),
        "kb_category": d["kb_category"],
        "supersedes": str(d.get("supersedes") or "").strip(),
    }
    env["slug"] = slug(env["entry"]["name"]) or env["id"]
    return env


def run(envelopes, progress=None):
    for i, env in enumerate(envelopes, 1):
        distil(env)
        if progress:
            progress(i, len(envelopes))
    return envelopes


def as_kb_block(env: dict) -> str:
    """The `#### Name` block used throughout `Tooling Sources/`."""
    e = env["entry"]
    cites = env.get("cluster_sources") or [env["source"]]
    n = len(cites)
    conf = ("**High**" if env["confidence"] >= 0.8 else
            "**Medium**" if env["confidence"] >= 0.55 else "**Low**")
    lines = [f"#### {e['name']}",
             f"- **Category**: {e['kb_category']} ({env['category']})",
             f"- **What it is**: {e['what']}" + (f" {e['why']}" if e["why"] else ""),
             f"- **Source**: [{env['source']}]({env['url']})",
             f"- **Confidence**: {conf} — {env.get('why_class') or env.get('why', '')}"]
    if n > 1:
        lines.append(f"- **Adoption**: seen in {n} independent sources this run — "
                     + ", ".join(cites))
    if e["supersedes"]:
        lines.append(f"- **Supersedes**: {e['supersedes']}")
    lines.append(f"- *(funnel, {env['source']}, retrieved {env['fetched'][:10]})*")
    return "\n".join(lines)
