#!/usr/bin/env python3
"""
phases.py — what the QA protocol checks, as five separate things you can ask for.

**Why it is not one pipeline.** It was, and that was wrong: starting it meant
running everything, so re-checking how the portfolio site *looks* meant sitting
through a functional suite whose answer nobody was in doubt about. A check you
cannot run on its own is a check you run less often than you should.
[@owner · 2026-08-20]

    run.sh <project>                       every phase, in the order below
    run.sh <project> --phase design        only that one
    run.sh <project> --phase design,code   only those

**Design runs first.** [@owner · 2026-08-20] Not because it is cheapest — it is
not — but because a build that looks wrong is not worth driving, and how a thing
looks is what reaches a person before any of its behaviour does.

**Two of the five cannot be run by a shell script and that is stated, not
hidden.** `design` and `behaviour` are judgment work carried out by an agent
against a proven skill; `code` and `feature` are commands. A phase that needs an
agent exits with NEEDS_AGENT and names exactly what to invoke, and the run's
`phases.json` records it as requested-but-not-done — so `verify_run.py` fails a
run that claims a phase it never performed. Silence never counts as coverage.
"""
from __future__ import annotations

import json
import pathlib

# Order is the default pipeline order. Design first, deliberately.
PHASES = {
    "design": {
        "what": "how it looks — spacing, hierarchy, contrast, overlap, drift",
        "runs": "agent",
        "engine": "gstack /design-review",
        "invoke": "/design-review",
        "why": (
            "10 categories and ~80 items, with letter grades and a "
            "design-baseline.json for regression. Already built, already proven; "
            "we route to it rather than reimplementing a checklist."
        ),
        "evidence": ["design/**"],
    },
    "code": {
        "what": "does it build, does it typecheck, do its own tests pass",
        "runs": "script",
        "engine": "the project's own commands",
        "invoke": None,
        "why": (
            "Cheap, deterministic, and the only phase that can tell you the "
            "thing under test is not even the thing you think it is."
        ),
        "evidence": ["code/*.log"],
    },
    "feature": {
        "what": "does it do what the written use cases say",
        "runs": "script",
        "engine": "TestZeus Hercules (web) · Midscene (desktop)",
        "invoke": None,
        "why": (
            "The use cases are Gherkin .feature files, confirmed by the owner. "
            "This is the phase that produces video, screenshots and verdicts."
        ),
        "evidence": ["proofs/**", "report.md"],
    },
    "behaviour": {
        "what": "what breaks when someone uses it in ways nobody wrote down",
        "runs": "agent",
        "engine": "gstack /qa",
        "invoke": "/qa",
        "why": (
            "Feature files only ever find what someone thought to write. This "
            "is the phase that finds the rest, and it is complementary, never a "
            "substitute."
        ),
        "evidence": ["behaviour/**"],
    },
    "access": {
        "what": "can it be used with a keyboard and a screen reader",
        "runs": "script",
        "engine": "Hercules' accessibility pass (WCAG)",
        "invoke": None,
        "why": (
            "Separated from `feature` because it is the one thing a passing "
            "functional run says nothing about — project-three's whole "
            "interactive core sat outside the accessibility tree for an entire "
            "run and every functional verdict was still honest."
        ),
        "evidence": ["proofs/**/accessibility_logs.csv"],
    },
}

ORDER = list(PHASES)


def parse(spec: str | None) -> list[str]:
    """'design,code' -> ['design', 'code']. Empty or None -> every phase."""
    if not spec or not spec.strip():
        return list(ORDER)
    want, unknown = [], []
    for raw in spec.split(","):
        name = raw.strip().lower()
        if not name:
            continue
        if name in PHASES:
            if name not in want:
                want.append(name)
        else:
            unknown.append(name)
    if unknown:
        raise ValueError(
            f"no such phase: {', '.join(unknown)}. "
            f"Phases are: {', '.join(ORDER)}."
        )
    if not want:
        return list(ORDER)
    return [p for p in ORDER if p in want]      # always in pipeline order


def manifest_path(run: pathlib.Path) -> pathlib.Path:
    return run / "phases.json"


def write_manifest(run: pathlib.Path, requested: list[str], results: dict) -> None:
    """Record what was asked for and what actually happened. Append-safe."""
    p = manifest_path(run)
    prior = {}
    if p.is_file():
        try:
            prior = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            prior = {}
    merged_req = list(dict.fromkeys(prior.get("requested", []) + requested))
    merged_res = {**prior.get("results", {}), **results}
    p.write_text(json.dumps({
        "requested": merged_req,
        "results": merged_res,
        "phases": {k: {"what": v["what"], "runs": v["runs"], "engine": v["engine"]}
                   for k, v in PHASES.items()},
    }, indent=2), encoding="utf-8")


def read_manifest(run: pathlib.Path) -> dict:
    p = manifest_path(run)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def describe() -> str:
    out = ["The QA protocol runs in five phases. Name none and it runs them all,",
           "in this order.", ""]
    for i, name in enumerate(ORDER, 1):
        p = PHASES[name]
        by = "a script" if p["runs"] == "script" else f"an agent, via {p['invoke']}"
        out.append(f"  {i}. {name:10} {p['what']}")
        out.append(f"     {'':10} run by {by} · {p['engine']}")
    out += ["", "  run.sh <project> --phase design",
            "  run.sh <project> --phase design,code"]
    return "\n".join(out)


def _need_agent(phase: str, run: pathlib.Path, project: str) -> None:
    """Say exactly what to invoke, and record the phase as owed."""
    spec = PHASES[phase]
    print(f"  · this phase is judgment work — a script cannot do it.")
    print(f"    Engine: {spec['engine']}")
    print(f"    Invoke: {spec['invoke']}  (from inside the {project} project)")
    print(f"    File its output into: {run.name}/{phase}/")
    print(f"  · recorded as REQUESTED BUT NOT DONE. Verification fails until it is.")
    write_manifest(run, [], {phase: {"state": "needs-agent", "invoke": spec["invoke"]}})


def _access(run: pathlib.Path) -> None:
    """Read the accessibility logs the feature phase already captured."""
    import csv
    rows, files = [], sorted(run.rglob("accessibility_logs.csv"))
    for f in files:
        try:
            with f.open(encoding="utf-8", errors="ignore") as fh:
                rows += list(csv.DictReader(fh))
        except OSError:
            continue
    if not rows:
        print(f"  · {len(files)} accessibility log(s), no rows in them.")
        return
    key = next((k for k in rows[0] if "impact" in k.lower() or "severity" in k.lower()), None)
    idk = next((k for k in rows[0] if k.lower() in {"id", "rule", "ruleid", "violation"}), None)
    from collections import Counter
    sev = Counter((r.get(key) or "unrated") for r in rows) if key else {}
    top = Counter((r.get(idk) or "?") for r in rows).most_common(6) if idk else []
    print(f"  · {len(rows)} finding(s) across {len(files)} scenario(s)")
    if sev:
        print("    by severity: " + ", ".join(f"{k}={v}" for k, v in sev.most_common()))
    for rule, n in top:
        print(f"      {n:4}  {rule}")
    out = run / "access-summary.txt"
    out.write_text(
        f"{len(rows)} accessibility finding(s) across {len(files)} scenario(s)\n"
        + ("by severity: " + ", ".join(f"{k}={v}" for k, v in sev.most_common()) + "\n" if sev else "")
        + "".join(f"{n:6}  {rule}\n" for rule, n in top), encoding="utf-8")


if __name__ == "__main__":
    import sys
    a = sys.argv[1:]
    if not a:
        print(describe()); raise SystemExit(0)
    try:
        if a[0] == "--resolve":
            print("\n".join(parse(a[1] if len(a) > 1 else None))); raise SystemExit(0)
        if a[0] == "--request":
            run = pathlib.Path(a[1]); write_manifest(run, parse(a[2]), {}); raise SystemExit(0)
        if a[0] == "--record":
            phase, run, state = a[1], pathlib.Path(a[2]), a[3]
            write_manifest(run, [], {phase: {"state": state}}); raise SystemExit(0)
        if a[0] == "--need-agent":
            _need_agent(a[1], pathlib.Path(a[2]), a[3]); raise SystemExit(0)
        if a[0] == "--access":
            _access(pathlib.Path(a[1])); raise SystemExit(0)
    except ValueError as e:
        print(e, file=sys.stderr); raise SystemExit(2)
    print(describe())
