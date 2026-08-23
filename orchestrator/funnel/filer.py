#!/usr/bin/env python3
"""
filer.py — Stage 8. Write the survivors, and write the digest.

Staging is a checkpoint, not a boundary. The funnel owns `01-Knowledge Base/`
[@owner · 2026-08-16]; `promote.py` moves these entries there in the same run.
Writing them out first means a run is inspectable before it lands, and gives
`--no-promote` something to stop at.

`merge_kb()` below is the original in-memory promoter, kept because it works on
live envelopes and is what the unit check exercises. `promote.py` is the one the
runner calls, because it works from the staged files and so can be re-run.
"""
import datetime as dt
import pathlib
import re

from . import distil, link, store

ORCH = store.ORCH
STAGED = ORCH / "staged"
DIGESTS = ORCH / "digests"
KB = store.VAULT / "01-Knowledge Base" / "Tooling Sources"

HEADING_RE = re.compile(r"^####\s+(.+?)\s*$", re.M)


def _yaml_list(xs):
    return "[" + ", ".join(f'"{str(x)}"' for x in xs) + "]"


def staged_note(env: dict) -> str:
    e = env["entry"]
    fm = [
        "---",
        "tags: [ingested, " + env["category"] + "]",
        f"created: {env['fetched'][:10]}",
        f"category: {env['category']}",
        f"filed: {env['fetched'][:10]}",
        f"half_life: {env['half_life']}",
        f"source: {env['source']}",
        f"source_url: {env['url']}",
        f"confidence: {env['confidence']:.2f}",
        f"score: {env['score']}",
        f"entities: {_yaml_list(env.get('entities') or [])}",
        f"citations: {_yaml_list(env.get('cluster_sources') or [env['source']])}",
        f"destination: {env['destination']}",
    ]
    if e["supersedes"]:
        fm.append(f"supersedes: \"{e['supersedes']}\"")
    if env.get("novel"):
        fm.append("novel: true")
    fm.append("links:")
    for l in env.get("links") or []:
        fm.append(f"  - target: \"{l['target']}\"")
        fm.append(f"    type: {l['type']}")
        fm.append(f"    distance: {l['distance']}")   # keeps link quality auditable
    fm.append("---")

    body = [f"\n# {e['name']}\n", e["what"]]
    if e["why"]:
        body.append(f"\n**Why it matters.** {e['why']}")
    n = len(env.get("cluster_sources") or [])
    if n > 1:
        body.append(f"\n**Adoption signal — {n} independent sources this run:** "
                    + ", ".join(env["cluster_sources"])
                    + ". Recorded, not collapsed: for tooling the count is the finding.")
    body.append(f"\n**Source.** [{env['source']}]({env['url']}) · retrieved {env['fetched'][:10]}")
    body.append(f"\n**Score {env['score']}** — " + " × ".join(
        f"{k}:{v:.2f}" for k, v in (env.get("score_terms") or {}).items()))
    if env.get("links"):
        body.append("\n## Related\n\n" + link.render(env["links"]))
    elif env.get("novel"):
        body.append("\n## Related\n\n_Nothing in the vault is near this. Flagged `novel` — "
                    "either genuinely new, or it does not belong._")
    return "\n".join(fm) + "\n" + "\n".join(body) + "\n"


def stage(envelopes, run_date: str):
    out_dir = STAGED / run_date
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for env in envelopes:
        env["destination"] = (f"01-Knowledge Base/Tooling Sources/{env['entry']['kb_category']}.md"
                              if env["category"] == "tooling"
                              else "01-Knowledge Base/Workflows and Best Practices.md"
                              if env["category"] == "workflow"
                              else f"01-Knowledge Base/{distil.slug(env['entry']['name'])}.md")
        p = out_dir / f"{env['slug'] or env['id']}.md"
        p.write_text(staged_note(env), encoding="utf-8")
        written.append(p)
    return written


# ── the gated promotion step ────────────────────────────────────────────────

def merge_kb(envelopes, apply: bool = False):
    """Promote staged tooling entries into `Tooling Sources/`. Dedup by heading."""
    plan = []
    for env in envelopes:
        if env["category"] != "tooling":
            plan.append((env, "skip", "not tooling — no automated destination yet"))
            continue
        target = KB / f"{env['entry']['kb_category']}.md"
        if not target.exists():
            plan.append((env, "skip", f"no such category file: {target.name}"))
            continue
        text = target.read_text(encoding="utf-8")
        existing = {h.strip().lower() for h in HEADING_RE.findall(text)}
        name = env["entry"]["name"].strip()
        plan.append((env, "cite" if name.lower() in existing else "insert", target.name))

    if not apply:
        return plan

    for env, action, target_name in plan:
        if action == "skip":
            continue
        target = KB / f"{env['entry']['kb_category']}.md"
        text = target.read_text(encoding="utf-8")
        block = distil.as_kb_block(env)
        if action == "cite":
            # append this run's citation to the existing entry, don't duplicate it
            pat = re.compile(rf"(^####\s+{re.escape(env['entry']['name'])}\s*$)", re.M | re.I)
            text = pat.sub(lambda m: m.group(1), text, count=1)
            note = (f"\n- *(also seen: {env['source']}, {env['fetched'][:10]}, "
                    f"[link]({env['url']}))*")
            idx = pat.search(text)
            if idx:
                nxt = text.find("\n#### ", idx.end())
                cut = nxt if nxt != -1 else len(text.rstrip())
                text = text[:cut].rstrip() + note + text[cut:]
        else:
            heads = [(m.start(), m.group(1)) for m in HEADING_RE.finditer(text)]
            spot = len(text.rstrip())
            for pos, h in heads:
                if h.lower() > env["entry"]["name"].lower():
                    spot = pos
                    break
            text = text[:spot].rstrip() + "\n\n" + block + "\n\n" + text[spot:].lstrip()
        target.write_text(text, encoding="utf-8")
    return plan


# ── the digest ──────────────────────────────────────────────────────────────

def digest(run_date, stats, filed, digested, dropped, feed_status, unreachable, staged_paths):
    DIGESTS.mkdir(parents=True, exist_ok=True)
    L = [f"---\ntags: [orchestrator, digest]\ncreated: {run_date}\ntype: digest\n---\n",
         f"# Digest — {run_date}\n",
         f"**{stats['fetched']} items from {stats['feeds_ok']}/{stats['feeds']} feeds** · "
         f"{len(filed)} filed · {len(digested)} digest-only · {len(dropped)} dropped · "
         f"{stats['already_seen']} already seen.\n"]

    if filed:
        L.append("## Filed\n")
        for e in sorted(filed, key=lambda x: -x["score"]):
            n = len(e.get("cluster_sources") or [])
            adopt = f" · **{n} sources**" if n > 1 else ""
            L.append(f"- **{e['entry']['name']}** · `{e['category']}` · score {e['score']}{adopt}  \n"
                     f"  {e['entry']['what'][:220]}  \n"
                     f"  [{e['source']}]({e['url']})"
                     + (" · ⚠️ novel, nothing to link to" if e.get("novel") else ""))
        L.append("")

    # Adoption is measured in *independent sources*, not item count. One outlet
    # publishing three pieces on its own launch is not five people adopting a tool.
    clusters = {}
    for e in filed + digested:
        if len(e.get("cluster_sources") or []) > 1:
            clusters.setdefault(e["cluster_id"], e)
    if clusters:
        L.append("## Adoption signals — same subject, independent sources\n")
        for e in sorted(clusters.values(), key=lambda x: -len(x["cluster_sources"])):
            L.append(f"- **{e['title'][:80]}** — {len(e['cluster_sources'])} sources · "
                     + ", ".join(e["cluster_sources"]))
        L.append("")

    if digested:
        L.append("## Digest only — not filed\n")
        for e in sorted(digested, key=lambda x: -x["score"])[:40]:
            L.append(f"- {e['title'][:110]} · `{e['category']}` · {e['source']} · {e['score']}")
        L.append("")

    if dropped:
        L.append(f"## Dropped — {len(dropped)}\n")
        by_reason = {}
        for e in dropped:
            by_reason.setdefault(e.get("reason") or "below threshold", []).append(e)
        for reason, items in sorted(by_reason.items(), key=lambda kv: -len(kv[1])):
            L.append(f"- **{reason}** — {len(items)}: "
                     + "; ".join(i["title"][:52] for i in items[:5])
                     + (" …" if len(items) > 5 else ""))
        L.append("")

    bad = [(n, s) for n, s in feed_status if s not in ("ok", "unchanged")]
    L.append("## Sources\n")
    L.append(f"- Reached: {stats['feeds_ok']}/{stats['feeds']} feeds")
    if bad:
        L.append("- **Failed or not a feed:**")
        for n, s in bad:
            L.append(f"  - `{s}` — {n}")
    if unreachable:
        by_reach = {}
        for s in unreachable:
            by_reach.setdefault(s.reach, []).append(s.name)
        L.append("- **Not reachable by this funnel yet** (registered, not fetched):")
        for reach, names in by_reach.items():
            L.append(f"  - `{reach}` — {len(names)} sources")
    L.append("")

    if staged_paths:
        L.append(f"## Filed to the Knowledge Base\n\n{len(staged_paths)} entries staged at "
                 f"`05-Orchestrator/staged/{run_date}/` and promoted into "
                 "`01-Knowledge Base/` — tooling into `Tooling Sources/`, workflow and "
                 "concept into `Ingested/`. Snapshot taken first, in "
                 "`05-Orchestrator/state/kb-backups/`.\n")

    path = DIGESTS / f"{run_date}.md"
    path.write_text("\n".join(L), encoding="utf-8")
    return path
