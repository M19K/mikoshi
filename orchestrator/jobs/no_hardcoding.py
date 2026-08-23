#!/usr/bin/env python3
"""
no_hardcoding.py — enforce the rule that has been in force, unchecked, since July.

    python3 -m jobs.no_hardcoding                 # scan the vault's own code
    python3 -m jobs.no_hardcoding --path <dir>    # scan anything, e.g. a repo
    python3 -m jobs.no_hardcoding --strict        # exit 1 on any finding

**Why this exists.** `CLAUDE.md` has said since **2026-07-16**: *"No hardcoding.
Protocols and pipelines should generalise to the category they belong to, not be
built for one instance."* Nothing checked it. On 2026-08-23 a hand sweep of the
shipped repo found **seven** violations that had accumulated over five weeks —
an Ollama host, two model names, an RSSHub address, a GitHub account, and two
tests written against one particular vault's contents.

**That is the pattern worth naming, not the seven.** A standing rule with no
check is a hope, and this vault has now learned it three times: the closing
duties drifted until `vault_check.py` enforced them, the board tally drifted
until the page derived it, and this. **If a rule matters, something has to be
able to fail on it.**

**Deliberately kept out of `vault_check.py`.** That checker runs over the whole
vault including notes, and prose legitimately contains absolute paths, model
names and people. This one scans *code*, where a literal is a decision rather
than a description.

**What it looks for**, and each one is a real thing that was found:

    absolute path      /Users/... or /home/... — works on exactly one machine
    bare host          a URL or port with no environment override beside it
    model name         a specific model pinned with no way to change it
    account or handle  someone's GitHub login, inbox, or channel id

**False positives are expected and are not silent.** A default is fine when an
override exists; the check is whether the literal is reachable from outside.
Anything you have decided is correct goes in `ALLOW` with the reason, so the
next reader sees a judgement rather than an oversight.
"""
import argparse
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_PATH = HERE.parent          # 05-Orchestrator/

SKIP_DIRS = {"__pycache__", ".venv", "node_modules", ".git", "state", "staged",
             "digests", "runs", "evidence"}

RULES = [
    ("absolute path", re.compile(r"[\"'](/" + "Users/|/home/|/Volumes/)[^\"']*[\"']"),  # split so this file is not itself a finding
     "works on exactly one machine — derive it, or read it from the environment"),
    ("bare host", re.compile(r"[\"']https?://(localhost|127\.0\.0\.1)[:/][^\"']*[\"']"),
     "no override beside it — wrap in os.environ.get(...) with this as the default"),
    ("pinned model", re.compile(r"[\"'][a-z0-9._-]+(?::[0-9]+b|-embed-text)[\"']"),
     "a model somebody else may not have — make it overridable"),
    ("account or handle", re.compile(r"[\"'][A-Za-z0-9_-]+@[a-z0-9.-]+\.(?:to|com|co|io)[\"']"
                                     r"|[\"']UC[A-Za-z0-9_-]{20,}[\"']"),
     "one person's account inside code everybody runs"),
]

# Literals that were checked and kept, with the reason. An entry here is a
# decision on the record — not a way to quiet the checker.
ALLOW = [
    (re.compile(r"os\.environ\.get\("), "has an override beside it"),
    (re.compile(r"#\s*allow-hardcode:"), "explicitly allowed on the line, with a reason"),
    (re.compile(r"^\s*#"), "a comment, not a value"),
    (re.compile(r"\"\"\"|'''"), "inside a docstring — prose, not a decision"),
]


def scan(root: pathlib.Path):
    findings = []
    for p in sorted(root.rglob("*.py")):
        if set(p.parts) & SKIP_DIRS:
            continue
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        in_doc = False
        for n, line in enumerate(lines, 1):
            # Docstrings describe; they do not configure. Track them so a
            # module explaining "we call localhost:11434" is not a finding.
            if line.count('"""') % 2 or line.count("'''") % 2:
                in_doc = not in_doc
                continue
            if in_doc:
                continue
            if any(a.search(line) for a, _ in ALLOW):
                continue
            for name, pat, why in RULES:
                if pat.search(line):
                    findings.append({"file": str(p.relative_to(root)), "line": n,
                                     "kind": name, "why": why,
                                     "text": line.strip()[:96]})
    return findings


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", default=str(DEFAULT_PATH))
    ap.add_argument("--strict", action="store_true", help="exit 1 on any finding")
    a = ap.parse_args()
    root = pathlib.Path(a.path).resolve()

    found = scan(root)
    print(f"no-hardcoding · {root}\n")
    if not found:
        print("  nothing hardcoded that is not overridable.\n")
        print("  The rule has been in CLAUDE.md since 2026-07-16 and was")
        print("  unchecked until 2026-08-23, by which time seven had accumulated.")
        return

    by_kind = {}
    for f in found:
        by_kind.setdefault(f["kind"], []).append(f)
    for kind, rows in by_kind.items():
        print(f"{kind}  ({len(rows)})")
        print(f"  {rows[0]['why']}")
        for r in rows:
            print(f"    {r['file']}:{r['line']}  {r['text']}")
        print()
    print(f"{len(found)} finding(s). Each is either a fix or an ALLOW entry "
          f"with a reason —\nleaving it as neither is how the last seven got in.")
    if a.strict:
        sys.exit(1)


if __name__ == "__main__":
    main()
