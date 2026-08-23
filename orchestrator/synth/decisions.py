#!/usr/bin/env python3
"""
decisions.py — the "why not X?" index. The thing a page store cannot do.

**Why this is the sharpest differentiator available, and why it costs nothing.**
A brain that stores pages can tell you what was decided, because someone wrote
it down. It cannot tell you what was *rejected*, because a rejected option
leaves no page — nobody writes a note about the thing they did not do. The
best a page store manages is a gap report: what it does not *know*, never what
was *considered and turned down*.

Mikoshi records the rejection as a first-class field. `record.py decision` takes
`--chose`, `--over` and `--why`, and the measurement on 2026-08-21 is the whole
argument: **165 decisions across 6 projects, and all 165 carry their rejected
alternatives.** The corpus for this is already complete. Nothing needs
backfilling and no new discipline has to be adopted.

What that buys, concretely: an agent proposing the macOS Keychain for key
storage can be told *it was proposed and refused on 2026-08-20, because a
routine that stops for a password has already failed* — with the date and the
reason. Today that only happens if someone remembers. Re-proposing something
already refused is the single most common way this vault wastes the owner's time.

    python3 -m synth.decisions "keychain"
    python3 -m synth.decisions "hermes" --project project-four
"""
import json
import pathlib
import re

VAULT = pathlib.Path(__file__).resolve().parent.parent.parent
PROJECTS = VAULT / "02-Projects"


def load(project: str | None = None) -> list[dict]:
    """Every recorded decision, newest first, tagged with its project."""
    out = []
    paths = ([PROJECTS / project / "Decisions.jsonl"] if project
             else sorted(PROJECTS.glob("*/Decisions.jsonl")))
    for p in paths:
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            row["project"] = p.parent.name
            row["source"] = f"{p.relative_to(VAULT)}"
            out.append(row)
    out.sort(key=lambda r: str(r.get("ts", "")), reverse=True)
    return out


def _terms(q: str) -> set:
    return {t for t in re.findall(r"[a-z0-9][a-z0-9\-]+", q.lower()) if len(t) > 2}


def rejected(query: str, project: str | None = None, top: int = 5) -> list[dict]:
    """Decisions whose REJECTED alternatives match the query.

    Deliberately searches `over` first and `chose` second. Asking "why not the
    Keychain" should surface the decision that refused it, not every decision
    that happens to mention keys — so a match in the rejected list is worth more
    than a match in the chosen one."""
    terms = _terms(query)
    if not terms:
        return []
    scored = []
    for d in load(project):
        over = " ".join(d.get("over") or []).lower()
        chose = str(d.get("chose", "")).lower()
        why = str(d.get("why", "")).lower()
        score = (3 * sum(1 for t in terms if t in over)
                 + 1 * sum(1 for t in terms if t in chose)
                 + 1 * sum(1 for t in terms if t in why))
        if score:
            scored.append((score, d))
    scored.sort(key=lambda s: (-s[0], str(s[1].get("ts", ""))))
    return [d for _, d in scored[:top]]


def format_for_prompt(rows: list[dict]) -> str:
    """Rejections, as evidence the model must cite by index."""
    if not rows:
        return ""
    out = []
    for n, d in enumerate(rows, 1):
        over = ", ".join(d.get("over") or []) or "—"
        out.append(
            f"[D{n}] {d['project']} · {str(d.get('ts', ''))[:10]}\n"
            f"  CHOSE:    {d.get('chose', '')}\n"
            f"  OVER:     {over}\n"
            f"  BECAUSE:  {str(d.get('why', ''))[:600]}")
    return "\n\n".join(out)


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="?", help="an option you want to know the fate of")
    ap.add_argument("--project")
    ap.add_argument("--all", action="store_true", help="just count what exists")
    a = ap.parse_args()

    if a.all or not a.query:
        rows = load(a.project)
        withalts = [r for r in rows if r.get("over")]
        print(f"{len(rows)} decisions · {len(withalts)} carry rejected alternatives")
        by = {}
        for r in rows:
            by[r["project"]] = by.get(r["project"], 0) + 1
        for k, v in sorted(by.items(), key=lambda kv: -kv[1]):
            print(f"  {k:<28} {v}")
        return 0

    rows = rejected(a.query, a.project)
    if not rows:
        print(f"Nothing recorded for {a.query!r}. That is not the same as "
              f"'never considered' — it means no decision names it.")
        return 1
    print(f"{len(rows)} decision(s) bearing on {a.query!r}\n")
    print(format_for_prompt(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
