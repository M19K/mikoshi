#!/usr/bin/env python3
"""
harvest.py — turn a finished run into an answer.

**The stage the protocol did not have.** Until 2026-08-19 the protocol assumed a
tool that runs and records produces findings. It does not. Hercules produces
evidence — video, screenshots, network and console logs, its own reasoning — and
a JUnit XML. Nothing read any of it. `verify_run.py` then failed the run for a
missing `report.md`, which is the gate correctly reporting the wrong thing:
the pixels were all there and the answer was missing.

**And it repairs the answers upstream throws away.** Hercules writes the literal
string "Runtime Failure" into the XML whenever it cannot parse the planner's
final reply — not when a scenario fails. On gamma, 2026-08-19, that
took 10 of 11 completed, judged scenarios and rendered them causeless. The
verdicts were never lost; they sat in the container log, one JSON object each.
So this reads the log whenever the XML says "Runtime Failure", recovers the real
verdict, and marks it `salvaged` so nobody mistakes a repair for a clean read.

The image at 05-Orchestrator/qa/engine/ fixes the parse at source. Salvage stays
because runs already on disk still carry the damage, and because a repair that
only exists inside a rebuilt image is a repair you cannot audit.

    python3 05-Orchestrator/qa/harvest.py <project> [--run YYYY-MM-DD]

Writes <run>/report.md, <run>/verdicts.json, and one clean shot per scenario
into <project>/QA/portfolio/.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import sys
import xml.etree.ElementTree as ET

VAULT = pathlib.Path(__file__).resolve().parents[2]
PROJECTS = VAULT / "02-Projects"

RUNTIME_FAILURE = "Runtime Failure"


# ---------------------------------------------------------------- extraction

def balanced_json_objects(text: str):
    """Yield every balanced top-level {...} span in `text`, in order.

    Quote-aware, so a brace inside a JSON string does not close the object.
    The same scan the engine patch uses; kept here so a run can be harvested
    without the patched image.
    """
    i, n = 0, len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        depth, in_str, esc = 0, False, False
        for j in range(i, n):
            c = text[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    yield text[i : j + 1]
                    i = j + 1
                    break
        else:
            return


def planner_verdict(log_text: str):
    """The planner's last complete verdict object in a container log."""
    best = None
    for span in balanced_json_objects(log_text):
        if '"is_passed"' not in span:
            continue
        try:
            obj = json.loads(span)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "is_passed" in obj:
            best = obj
    return best


# ---------------------------------------------------------------- evidence

def _rel(p: str, run: pathlib.Path) -> str | None:
    """Turn a container path (./opt/x, /testzeus-hercules/opt/x) into a run-relative one."""
    if not p:
        return None
    p = re.sub(r"^\.?/?(testzeus-hercules/)?opt/", "", p.strip())
    return p if (run / p).exists() else None


def count_jsonl(path: pathlib.Path, pred) -> int:
    if not path.is_file():
        return 0
    n = 0
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if pred(rec):
            n += 1
    return n


def evidence_for(run: pathlib.Path, props: dict) -> dict:
    base = _rel(props.get("Proofs Base Folder, includes screenshots, recording, netwrok logs, api logs, sec logs, accessibility logs", ""), run)
    shots_rel = _rel(props.get("Proofs Screenshot", ""), run)
    video_rel = _rel(props.get("Proofs Video", ""), run)
    thoughts_rel = _rel(props.get("Agents Internal Logs", ""), run)

    shots = sorted((run / shots_rel).glob("*.png")) if shots_rel else []
    console_errors = network_failures = 0
    if base:
        console_errors = count_jsonl(
            run / base / "console_logs.json",
            lambda r: str(r.get("level", "")).lower() in {"error", "severe"}
            or r.get("type") == "pageerror",
        )
        network_failures = count_jsonl(
            run / base / "network_logs.json",
            lambda r: r.get("type") == "response" and isinstance(r.get("status"), int) and r["status"] >= 400,
        )
    return {
        "base": base,
        "video": video_rel,
        "screenshots_dir": shots_rel,
        "screenshot_count": len(shots),
        "screenshots": [str(p.relative_to(run)) for p in shots],
        "agent_thoughts": thoughts_rel,
        "console_errors": console_errors,
        "network_failures": network_failures,
    }


# ---------------------------------------------------------------- harvesting

def harvest_run(project: pathlib.Path, run: pathlib.Path) -> list[dict]:
    results = []
    xmls = sorted(run.rglob("*_result.xml"))
    seen_files = set()
    for xml in xmls:
        feature_file = xml.name.replace("_result.xml", "")           # uc-01-arrive.feature
        stem = feature_file.replace(".feature", "")                   # uc-01-arrive
        seen_files.add(stem)
        log = run / "output" / f"{stem}.log"
        log_text = log.read_text(encoding="utf-8", errors="ignore") if log.is_file() else ""

        try:
            root = ET.parse(xml).getroot()
        except ET.ParseError as e:
            results.append({"scenario": stem, "feature": "?", "passed": None,
                            "source": "unreadable", "summary": f"unreadable JUnit XML: {e}",
                            "evidence": {}})
            continue

        for suite in root.iter("testsuite"):
            for case in suite.iter("testcase"):
                props = {p.get("name"): p.get("value") for p in case.iter("property")}
                fail = case.find("failure")
                err = case.find("error")
                node = fail if fail is not None else err
                msg = (node.get("message") or "").strip() if node is not None else ""

                passed = node is None
                source = "engine"
                salvaged = None

                if node is not None and msg == RUNTIME_FAILURE:
                    salvaged = planner_verdict(log_text)
                    if salvaged:
                        passed = bool(salvaged.get("is_passed"))
                        msg = (salvaged.get("assert_summary")
                               or salvaged.get("final_response") or "").strip()
                        source = "salvaged"
                    else:
                        source = "lost"
                        msg = ("The engine could not parse its own planner's final answer, "
                               "and no verdict object was recoverable from the container log. "
                               "This scenario has no result.")

                results.append({
                    "scenario": case.get("name") or stem,
                    "feature": suite.get("name") or case.get("classname") or "?",
                    "file": stem,
                    "seconds": float(case.get("time") or 0.0),
                    "passed": passed,
                    "source": source,
                    "summary": msg,
                    "final_response": (salvaged or {}).get("final_response", ""),
                    "evidence": evidence_for(run, props),
                })

    # A feature that produced no XML at all must not silently vanish. A
    # container killed before it publishes results leaves nothing to parse, and
    # a report that lists only what it could parse reads as complete coverage —
    # the same shape as the failure this whole stage exists to prevent.
    for feat in sorted((run / "input").glob("*.feature")):
        stem = feat.stem
        if stem in seen_files:
            continue
        log = run / "output" / f"{stem}.log"
        log_text = log.read_text(encoding="utf-8", errors="ignore") if log.is_file() else ""
        salvaged = planner_verdict(log_text)
        tail = " ".join(log_text.strip().splitlines()[-3:])[:300] if log_text else ""
        results.append({
            "scenario": stem,
            "feature": "(no JUnit XML was written)",
            "file": stem,
            "seconds": 0.0,
            "passed": bool(salvaged.get("is_passed")) if salvaged else None,
            "source": "salvaged-no-xml" if salvaged else "lost",
            "summary": ((salvaged.get("assert_summary") or salvaged.get("final_response") or "").strip()
                        if salvaged else
                        "The engine wrote no results file for this feature. It did not finish. "
                        + (f"Last lines of its log: {tail}" if tail else
                           f"No container log at output/{stem}.log either.")),
            "final_response": (salvaged or {}).get("final_response", ""),
            "evidence": {},
        })
    return results


def capture_portfolio(project: pathlib.Path, run: pathlib.Path, results: list[dict]) -> int:
    """One clean shot per scenario. Prefer the landing frame; fall back to the last."""
    out = project / "QA" / "portfolio"
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for r in results:
        shots = r["evidence"].get("screenshots") or []
        if not shots:
            continue
        landing = [s for s in shots if "openurl_end" in pathlib.Path(s).name]
        pick = run / (landing[0] if landing else shots[-1])
        dest = out / f"{r['file']}.png"
        shutil.copyfile(pick, dest)
        r["evidence"]["portfolio_shot"] = str(dest.relative_to(project))
        n += 1
    return n


# ---------------------------------------------------------------- report

def write_report(project: pathlib.Path, run: pathlib.Path, results: list[dict]) -> pathlib.Path:
    passed = [r for r in results if r["passed"] is True]
    failed = [r for r in results if r["passed"] is False]
    lost = [r for r in results if r["source"] == "lost"]
    salvaged = [r for r in results if r["source"] == "salvaged"]

    L = []
    L.append(f"# QA run — {project.name} — {run.name}")
    L.append("")
    L.append(f"Produced by `05-Orchestrator/qa/harvest.py` from the engine's own evidence. "
             f"Every line below traces to a file in this directory.")
    L.append("")
    L.append(f"- scenarios: **{len(results)}** · passed **{len(passed)}** · failed **{len(failed)}** · no result **{len(lost)}**")
    if salvaged:
        L.append(f"- **{len(salvaged)} verdict(s) salvaged** from the container log after the engine "
                 f"failed to parse its own planner's reply. Treat them as the engine's verdict, "
                 f"recovered — not as a second opinion.")
    L.append("")
    L.append("## Scenarios")
    L.append("")
    L.append("| Scenario | Verdict | Source | Images | Video | Console errors | Failed requests |")
    L.append("|---|---|---|---|---|---|---|")
    for r in results:
        v = {True: "pass", False: "**fail**", None: "—"}[r["passed"]]
        e = r["evidence"]
        L.append(f"| {r['scenario']} | {v} | {r['source']} | {e.get('screenshot_count', 0)} | "
                 f"{'yes' if e.get('video') else 'no'} | {e.get('console_errors', 0)} | "
                 f"{e.get('network_failures', 0)} |")
    L.append("")

    if failed or lost:
        L.append("## Findings")
        L.append("")
        L.append("**A failed assertion from this engine is a question until it reproduces "
                 "deterministically.** Drive the same steps a second way before touching the "
                 "product; if it only fails under the model's eye, the fix belongs in the "
                 "feature file. See `05-Orchestrator/qa/README.md`.")
        L.append("")
        for r in failed + [x for x in lost if x not in failed]:
            L.append(f"### {r['scenario']}")
            L.append("")
            L.append(f"- feature: {r['feature']}")
            L.append(f"- verdict source: {r['source']}")
            summary = " ".join((r["summary"] or "(no summary)").split())
            L.append(f"- what the engine said: {summary}")
            e = r["evidence"]
            if e.get("video"):
                L.append(f"- video: `{e['video']}`")
            if e.get("screenshots_dir"):
                L.append(f"- screenshots: `{e['screenshots_dir']}` ({e['screenshot_count']})")
            if e.get("agent_thoughts"):
                L.append(f"- the agent's own reasoning: `{e['agent_thoughts']}`")
            if e.get("portfolio_shot"):
                L.append(f"- clean shot: `{e['portfolio_shot']}`")
            L.append("")

    L.append("## Evidence index")
    L.append("")
    for r in results:
        e = r["evidence"]
        L.append(f"- **{r['scenario']}** — {e.get('screenshot_count', 0)} image(s), "
                 f"video {'present' if e.get('video') else 'absent'}, "
                 f"base `{e.get('base') or '—'}`")
    L.append("")

    out = run / "report.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    (run / "verdicts.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project")
    ap.add_argument("--run", help="run directory name, e.g. 2026-08-19 (default: newest)")
    a = ap.parse_args()

    project = PROJECTS / a.project
    if not project.is_dir():
        sys.exit(f"no such project: {a.project}")
    runs_dir = project / "QA" / "runs"
    runs = sorted([p for p in runs_dir.iterdir() if p.is_dir()]) if runs_dir.is_dir() else []
    if not runs:
        sys.exit(f"no QA runs for {a.project}")
    run = (runs_dir / a.run) if a.run else runs[-1]
    if not run.is_dir():
        sys.exit(f"no such run: {run}")

    results = harvest_run(project, run)
    if not results:
        sys.exit(f"no JUnit XML found under {run} — the engine produced no results to harvest")
    shots = capture_portfolio(project, run, results)
    report = write_report(project, run, results)

    p = sum(1 for r in results if r["passed"] is True)
    f = sum(1 for r in results if r["passed"] is False)
    l = sum(1 for r in results if r["source"] == "lost")
    s = sum(1 for r in results if r["source"] == "salvaged")
    print(f"→ {len(results)} scenario(s): {p} passed, {f} failed, {l} with no result"
          + (f" ({s} salvaged from the log)" if s else ""))
    print(f"→ {shots} clean shot(s) → {project.name}/QA/portfolio/")
    print(f"→ {report.relative_to(VAULT)}")
    if l:
        sys.exit(1)


if __name__ == "__main__":
    main()
