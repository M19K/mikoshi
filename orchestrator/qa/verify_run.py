#!/usr/bin/env python3
"""
verify_run.py — the gate. A QA run is not finished until this exits 0.

**Why this exists.** The first universal-QA run, on project-three 2026-08-18, produced a
confident report describing keyboard focus order, tab order and text selection —
and its evidence directory contained **zero screenshots**, an **empty portfolio
folder**, and nothing but `.json`, `.sse` and `.txt`. It had driven the app's
loopback HTTP API. It never opened the window.

the owner watched it happen: *"It didn't type in any prompts, it didn't navigate the
UI, it did nothing."* He was right, and the app's own history confirmed it —
there was no record of any test case having been run.

**The protocol already said "evidence, not assertion" and it did not help.** An
instruction cannot stop an agent that believes it complied. So the rule stops
being an instruction and becomes a gate: a run with no pixels is a failed run,
enforced by a program, and the agent cannot file a report until it passes.

    python3 verify_run.py <project>                  # newest run
    python3 verify_run.py <project> --run 2026-08-18
    python3 verify_run.py --all
"""
import argparse
import json
import pathlib
import re
import sys

VAULT = pathlib.Path(__file__).resolve().parents[2]
PROJECTS = VAULT / "02-Projects"

IMAGE = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
VIDEO = {".mp4", ".mov", ".webm"}

# Hercules writes its evidence under proofs/ alongside the run, so a run that
# used it satisfies the gate by construction — which is the whole reason for
# preferring a tool that records over an instruction that asks.

# An API transcript is real evidence for a backend claim and no evidence at all
# for a claim about what a person sees. Keeping the distinction is the point.
API_ONLY = {".json", ".sse", ".txt", ".log", ".har"}

# Findings phrased about the interface. If a report makes one of these claims,
# a screenshot has to exist or the claim was not observed — it was inferred.
UI_CLAIM = re.compile(
    r"\b(focus|tab order|keyboard|click|hover|scroll|layout|spacing|contrast|"
    r"colou?r|theme|button|screen|visible|on-screen|selectable|cursor|icon|"
    r"menu ?bar|dialog|modal|placeholder|alignment|font|dark mode|light mode)\b",
    re.I)


def _runs(project: pathlib.Path):
    d = project / "QA" / "runs"
    return sorted([p for p in d.iterdir() if p.is_dir()]) if d.is_dir() else []


def _use_cases(project: pathlib.Path):
    f = project / "QA" / "Use Cases.md"
    if not f.is_file():
        return []
    return re.findall(r"^##\s+(UC-\d+)", f.read_text(encoding="utf-8"), re.M)


def verify(project: pathlib.Path, run: pathlib.Path):
    fail, warn = [], []
    files = [p for p in run.rglob("*") if p.is_file()]
    images = [p for p in files if p.suffix.lower() in IMAGE]

    # A Midscene report embeds its screenshots as base64 inside the HTML rather
    # than writing loose files, so counting only image files would fail the one
    # engine that cannot skip capturing. Each embedded frame counts as an image.
    for r in run.rglob("*.html"):
        try:
            embedded = r.read_text(encoding="utf-8", errors="ignore").count(
                "data:image/png;base64")
        except OSError:
            continue
        images += [r] * embedded
    videos = [p for p in files if p.suffix.lower() in VIDEO]
    api = [p for p in files if p.suffix.lower() in API_ONLY]

    report = run / "report.md"
    text = report.read_text(encoding="utf-8") if report.is_file() else ""

    # --- phases ----------------------------------------------------------
    # A run now asks for named phases. The gate's job is that a phase which was
    # requested and never performed cannot pass as coverage — the same failure
    # this file exists for, one level up: silence reading as a clean result.
    man = {}
    mf = run / "phases.json"
    if mf.is_file():
        try:
            man = json.loads(mf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            man = {}
    requested = man.get("requested") or []
    results = man.get("results") or {}
    owed, broke, blocked = [], [], []
    for ph in requested:
        st = (results.get(ph) or {}).get("state")
        if st in (None, "needs-agent"):
            owed.append((ph, (results.get(ph) or {}).get("invoke")))
        elif st == "failed":
            broke.append(ph)
        elif st == "blocked":
            blocked.append(ph)
    for ph, inv in owed:
        fail.append(f"phase '{ph}' was requested and never ran"
                    + (f" — invoke {inv} and file its output into {run.name}/{ph}/"
                       if inv else ""))
    for ph in broke:
        fail.append(f"phase '{ph}' FAILED — see {run.name}/{ph}/")
    for ph in blocked:
        warn.append(f"phase '{ph}' could not run and said so; it is not coverage.")

    ran_feature = (results.get("feature") or {}).get("state") == "done"
    # report.md is owed only by the phase that produces verdicts.
    if not report.is_file() and (ran_feature or not requested):
        fail.append("no report.md in the run directory — run "
                    "`python3 05-Orchestrator/qa/harvest.py <project>`, which "
                    "builds it from the evidence the engine already captured. "
                    "Do not hand-write one.")

    # 1 · pixels must exist at all — but only from a phase that drives the
    # product. A `code` run compiles things; there is nothing to photograph.
    drives = (not requested) or bool({"feature", "design", "behaviour"} & set(requested))
    if drives and not images and not videos:
        fail.append(
            f"NO SCREENSHOTS AND NO VIDEO. {len(api)} API transcript(s) present. "
            "An HTTP response is evidence about a server, never about what a "
            "person sees. This run did not use the product.")

    # 2 · UI claims need pixels behind them
    ui_claims = [ln.strip() for ln in text.splitlines()
                 if ln.strip().startswith("###") and UI_CLAIM.search(ln)]
    if ui_claims and not images:
        fail.append(
            f"{len(ui_claims)} finding(s) describe the interface but the run "
            f"captured no image. Example: {ui_claims[0][:90]}")

    # 3 · every use case the report calls tested needs its own artifact
    tested = set(re.findall(r"\b(UC-\d+)\b", text))
    declared = set(_use_cases(project))
    named_by_file = {m.group(1).upper() for p in files
                     for m in [re.match(r"(uc-\d+)", p.name, re.I)] if m}
    named_by_file = {n.replace("UC-", "UC-") for n in named_by_file}
    missing = sorted(
        uc for uc in tested & declared
        if not any(re.match(rf"{uc}\b", p.name, re.I) for p in images))
    if missing:
        warn.append(f"{len(missing)} use case(s) referenced with no screenshot "
                    f"named for them: {', '.join(missing[:8])}")

    # 4 · portfolio capture is part of the protocol, not an extra
    shots = project / "QA" / "portfolio"
    if not shots.is_dir() or not any(p.suffix.lower() in IMAGE
                                     for p in shots.iterdir() if p.is_file()):
        warn.append("QA/portfolio/ holds no images. The protocol asks for one "
                    "clean labelled shot per use case.")

    # 5 · a scenario the engine could not answer must fail the run
    #
    # harvest.py already exits non-zero on this, but the gate has to know it
    # independently: a run verified on its own, or re-verified later, would
    # otherwise pass while carrying a scenario that never produced a verdict.
    # That is the 2026-08-19 failure in miniature — silence reading as coverage.
    verdicts = run / "verdicts.json"
    if verdicts.is_file():
        try:
            rows = json.loads(verdicts.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            rows = []
        lost = [r for r in rows if isinstance(r, dict) and r.get("source") == "lost"]
        if lost:
            fail.append(
                f"{len(lost)} scenario(s) produced NO verdict at all — the engine "
                f"did not answer for them. Example: {lost[0].get('scenario', '?')}. "
                f"Re-run those with `run.sh <project> --only <slug>`.")
        salvaged = [r for r in rows if isinstance(r, dict)
                    and str(r.get("source", "")).startswith("salvaged")]
        if salvaged:
            warn.append(f"{len(salvaged)} verdict(s) were salvaged from the container "
                        f"log because the engine could not parse its own reply. Check "
                        f"the run used the patched image `mikoshi/hercules`.")

    # 6 · a report claiming coverage while capturing nothing is the worst case
    if re.search(r"\bverdict:\s*(usable|passing|healthy|ship)", text, re.I) \
            and not images:
        fail.append("the report states a positive verdict on a run that "
                    "captured no image of the product.")

    return fail, warn, {"images": len(images), "videos": len(videos),
                        "api": len(api), "files": len(files)}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", nargs="?")
    ap.add_argument("--run")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()

    targets = []
    if a.all:
        for p in sorted(PROJECTS.iterdir()):
            targets += [(p, r) for r in _runs(p)]
    else:
        if not a.project:
            ap.error("give a project, or --all")
        proj = PROJECTS / a.project
        runs = _runs(proj)
        if not runs:
            print(f"no QA runs found for {a.project}")
            sys.exit(2)
        targets = [(proj, proj / "QA" / "runs" / a.run)] if a.run else [(proj, runs[-1])]

    bad = 0
    for proj, run in targets:
        if not run.is_dir():
            print(f"✗ {proj.name}/{run.name}: no such run"); bad += 1; continue
        fail, warn, c = verify(proj, run)
        head = (f"{proj.name} · {run.name} — {c['images']} image(s), "
                f"{c['videos']} video(s), {c['api']} API transcript(s)")
        if fail:
            bad += 1
            print(f"\n✗ FAILED  {head}")
            for f in fail:
                print(f"    · {f}")
        else:
            print(f"\n✓ passed  {head}")
        for w in warn:
            print(f"    ! {w}")

    if bad:
        print(f"\n{bad} run(s) FAILED verification. A run with no pixels is not a "
              f"QA run — re-run it driving the real interface.")
        sys.exit(1)
    print("\nall runs verified.")


if __name__ == "__main__":
    main()
