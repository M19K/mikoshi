#!/usr/bin/env python3
"""
doctor.py — what is missing, before you find out one failure at a time.

    python3 doctor.py            # check everything, explain what is absent
    python3 doctor.py --strict   # exit 1 if anything REQUIRED is missing

**Why this exists instead of a test suite.** Almost nothing here breaks because
the code is wrong; it breaks because a model was never pulled, a folder was
never declared, or a routine was never installed. Unit tests would pass on a
machine where none of that is true. So the useful instrument is one that looks
at *this* install and says what it hasn't got.

**Three levels, and the middle one is the point.**

    REQUIRED   nothing works without it
    DEGRADED   it runs and quietly does less — the dangerous kind
    OPTIONAL   a whole feature is off, which is fine if you know

The degraded row is why this file exists. Vector search failing prints
`vec=False` on one line of a run that otherwise looks healthy, and a source that
stopped arriving looks exactly like a quiet week. **Anything a person might
mistake for working is listed here whether it is broken or not.**
"""
import argparse
import pathlib
import shutil
import subprocess
import sys
import urllib.request

def _force_utf8_console() -> None:
    """Windows consoles default to cp1252 and cannot encode `→` (U+2192).

    `doctor.py` produced a completely correct report and then died on its own
    arrow, on Windows only — caught by CI, 2026-08-23. Nothing about the checks
    was wrong; the output encoding was. Any tool a person runs directly needs
    this, and it is three lines.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


HERE = pathlib.Path(__file__).resolve().parent
REQUIRED, DEGRADED, OPTIONAL = "REQUIRED", "DEGRADED", "OPTIONAL"


def _ollama_url() -> str:
    """The same override the code uses. A doctor that checks a different host
    from the one the funnel calls is worse than no doctor."""
    import os
    return os.environ.get("MIKOSHI_OLLAMA_URL", "http://localhost:11434")


def _ollama_models():
    try:
        with urllib.request.urlopen(_ollama_url() + "/api/tags", timeout=4) as r:
            import json
            return {m["name"] for m in json.load(r).get("models", [])}
    except Exception:
        return None


def _port(port: int) -> bool:
    try:
        urllib.request.urlopen(f"http://localhost:{port}", timeout=2)
        return True
    except Exception as e:
        # A refused connection means nothing is there; an HTTP error means
        # something is, and answered. Only the first is an absence.
        return not isinstance(e, (ConnectionRefusedError, OSError)) or hasattr(e, "code")


def checks(vault: pathlib.Path):
    out = []

    def add(level, name, ok, detail, fix):
        out.append({"level": level, "name": name, "ok": ok,
                    "detail": detail, "fix": fix})

    add(REQUIRED, "Python 3.11+", sys.version_info >= (3, 11),
        f"running {sys.version_info.major}.{sys.version_info.minor}",
        "the code uses 3.11 syntax; upgrade Python")

    try:
        import sqlite_vec  # noqa: F401
        vec = True
    except ImportError:
        vec = False
    add(REQUIRED, "sqlite-vec", vec,
        "vector search available" if vec else
        "NOT installed — the funnel prints `vec=False` and keeps going, so "
        "semantic search is off while everything still looks healthy",
        "pip install sqlite-vec")

    models = _ollama_models()
    add(REQUIRED, "Ollama running", models is not None,
        f"answering on {_ollama_url()}" if models is not None else f"not answering on {_ollama_url()}",
        "install from ollama.com and start it")
    for m, why, size in (
            ("gpt-oss:20b", "every judgement and every written answer", "about 13 GB"),
            ("nomic-embed-text", "embeddings for semantic search", "about 270 MB")):
        have = models is not None and any(x.startswith(m.split(":")[0]) for x in models)
        add(REQUIRED, f"model {m}", have,
            f"present — used for {why}" if have else f"missing — needed for {why}",
            f"ollama pull {m}   ({size})")

    add(REQUIRED, "PROTOCOL.md", (vault / "PROTOCOL.md").is_file(),
        "the bedrock is present",
        "run init.py — without it agents have no rules to follow")
    add(REQUIRED, "CLAUDE.md", (vault / "CLAUDE.md").is_file(),
        "your half is present", "run init.py")

    src = vault / "05-Orchestrator" / "Sources.md"
    declared = 0
    if src.is_file():
        declared = sum(1 for l in src.read_text(encoding="utf-8").splitlines()
                       if l.startswith("| ") and "http" in l and "_" not in l)
    add(DEGRADED, "sources declared", declared > 0,
        f"{declared} feed(s) declared" if declared else
        "none — the funnel will stop and tell you so, and nothing is ingested",
        "add feeds to 05-Orchestrator/Sources.md, verified by fetching them")

    notes = len(list((vault / "01-Knowledge Base").rglob("*.md"))) if \
        (vault / "01-Knowledge Base").is_dir() else 0
    projects = len([p for p in (vault / "02-Projects").iterdir() if p.is_dir()]) if \
        (vault / "02-Projects").is_dir() else 0
    add(DEGRADED, "something to answer from", notes + projects > 0,
        f"{notes} note(s), {projects} project(s)" if notes + projects else
        "empty — measured at 0 of 27 questions answered on an empty store. Not "
        "broken: it has nothing to cite and will not invent",
        "write notes and record decisions as you make them; notes are two "
        "thirds of the measured value")

    dec = list(vault.glob("02-Projects/*/Decisions.jsonl"))
    add(DEGRADED, "decisions being recorded", bool(dec),
        f"{len(dec)} project(s) recording" if dec else
        "none yet — measured as the second most valuable store in the system, "
        "worth 5 of 27 correct answers",
        "record.py decision <project> --chose ... --over ... --why ...")

    routines = vault / "05-Orchestrator" / "routines"
    installed = routines.is_dir() and any(routines.glob("*.md"))
    add(DEGRADED, "routines installed", installed,
        "prompts are present — check they are actually scheduled in your agent"
        if installed else
        "NOT installed — nothing repairs the vault, ingests, or notices a "
        "source going quiet. Mikoshi does not maintain itself without these",
        "copy templates/routines/ into the vault and schedule the three")

    add(OPTIONAL, "RSSHub (X ingestion)", _port(1200),
        "answering on :1200" if _port(1200) else "not running — the X tier is off",
        "self-host RSSHub, or leave X out")
    add(OPTIONAL, "gallery-dl (Instagram)", shutil.which("gallery-dl") is not None,
        "present" if shutil.which("gallery-dl") else
        "absent — the Instagram tier is off. It also needs a logged-in Chrome "
        "profile it can read cookies from",
        "brew install gallery-dl, or leave Instagram out")
    add(OPTIONAL, "gh (claim verification)", shutil.which("gh") is not None,
        "present" if shutil.which("gh") else
        "absent — verify_claims cannot check whether a cited commit exists",
        "install the GitHub CLI, or ignore if you do not use GitHub")
    add(OPTIONAL, "Docker (QA layer)", shutil.which("docker") is not None,
        "present" if shutil.which("docker") else
        "absent — the QA layer is unreachable. It also needs a 12.8 GB image",
        "install Docker, or leave the QA layer out")
    add(OPTIONAL, "routed model endpoint", bool(
        __import__("os").environ.get("MIKOSHI_LLM_BASE_URL")),
        "MIKOSHI_LLM_BASE_URL is set — calls leave this machine and cost money"
        if __import__("os").environ.get("MIKOSHI_LLM_BASE_URL") else
        "not set — everything runs on the local model, free and private",
        "set it to ANY OpenAI-compatible endpoint if you want speed or a "
        "stronger model; this is opt-in on purpose")
    return out


def main():
    _force_utf8_console()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 if anything REQUIRED is missing")
    ap.add_argument("--vault", default=".", help="vault root (default: here)")
    a = ap.parse_args()
    vault = pathlib.Path(a.vault).resolve()

    rows = checks(vault)
    print(f"mikoshi doctor · {vault}\n")
    for level in (REQUIRED, DEGRADED, OPTIONAL):
        group = [r for r in rows if r["level"] == level]
        if not group:
            continue
        print(f"{level}")
        for r in group:
            mark = " ok " if r["ok"] else " -- "
            print(f"  [{mark}] {r['name']:<26} {r['detail']}")
            if not r["ok"]:
                print(f"           {'':<26} → {r['fix']}")
        print()

    missing_req = [r for r in rows if r["level"] == REQUIRED and not r["ok"]]
    degraded = [r for r in rows if r["level"] == DEGRADED and not r["ok"]]
    print(f"{len(missing_req)} required missing · {len(degraded)} running degraded")
    if degraded and not missing_req:
        print("\nIt will run. The degraded rows are the ones worth reading — "
              "each is\nsomething that looks like it is working.")
    if missing_req and a.strict:
        sys.exit(1)


if __name__ == "__main__":
    main()
