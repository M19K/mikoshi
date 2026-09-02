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

**It scans shell and JavaScript as well as Python, since 2026-08-30.** For its
first week it globbed `*.py` only, so it returned a clean bill of health on any
repo written in anything else. `alpha` found two hardcoded home-directory
node paths in its own `.sh` files by hand on 2026-08-30 — the exact class this
exists to catch, in files it could not open. Shell is the worst case for the
rule, because a path there is usually written bare rather than quoted, so the
quoted-literal patterns miss it; hence the unquoted variant below, applied to
shell only, where a bare `/Users/...` token is unambiguously a path.

**What it looks for**, and each one is a real thing that was found:

    absolute path      /Users/... or /home/... — works on exactly one machine
    bare host          a URL or port with no environment override beside it
    model name         a specific model pinned with no way to change it
    account or handle  someone's GitHub login, inbox, or channel id

**Inside a test file only the first two fire.** A fixture address and a request
base of `http://localhost/api/...` are neither a decision nor a person, and
firing everything in tests produced 132 of them across two repos on the first
run. Absolute paths and pinned models still fire there, because two of the
original seven violations were tests written against one particular vault.

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
             "digests", "runs", "evidence", "dist", "build", "out", ".next",
             "coverage", ".turbo", "vendor", ".resets"}

# Extensions that carry decisions. Grouped by comment syntax rather than by
# language, because that is the only difference the scanner cares about.
# A test file is code, but it is not a *configuration surface*. Two of the
# original seven violations were tests written against one particular vault, so
# tests are still scanned — but only for the two kinds that make a test
# machine-specific. A request base of `http://localhost/api/...` and a fixture
# address of `jane@acme.io` are neither a decision nor a person: measured
# 2026-08-30, firing all four rules inside tests produced 70 findings in
# alpha and 62 in beta, essentially all of them fixtures.
# A checker that cries wolf on its first run teaches everyone to pass `|| true`.
TEST_DIRS = {"tests", "test", "__tests__", "spec", "fixtures", "e2e", "golden"}
TEST_FILE = re.compile(r"(^test[-_]|[._-](test|tests|spec)\.[a-z]+$|^golden)")
TEST_ONLY_KINDS = {"absolute path", "pinned model"}


def _is_test(p: pathlib.Path) -> bool:
    return bool(set(p.parts) & TEST_DIRS or TEST_FILE.search(p.name))


PY_EXT = {".py"}
SH_EXT = {".sh", ".bash", ".zsh"}
JS_EXT = {".mjs", ".cjs", ".js", ".ts", ".tsx"}
CODE_EXT = PY_EXT | SH_EXT | JS_EXT

# A bare path in a shell script. Shell rarely quotes, so the quoted rule below
# cannot see `NODE=/Users/someone/.local/bin/node` — which is precisely the
# shape found by hand in alpha on 2026-08-30. Shell only: an unquoted
# `/Users/...` in Python or JavaScript is almost always prose in a comment.
BARE_PATH = re.compile(r"(?<![\w\"'/])(/" + r"Users/|/home/)[A-Za-z0-9._-]+/[^\s\"';)]*")

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
    (re.compile(r"process\.env\."), "has an override beside it — JavaScript"),
    (re.compile(r"\$\{?[A-Za-z_][A-Za-z0-9_]*(:-|:=|\})"), "has an override beside it — shell parameter expansion"),
    (re.compile(r"#\s*allow-hardcode:"), "explicitly allowed on the line, with a reason"),
    (re.compile(r"//\s*allow-hardcode:"), "explicitly allowed on the line, with a reason"),
    (re.compile(r"^\s*#"), "a comment, not a value"),
    (re.compile(r"^\s*(//|\*|/\*)"), "a comment, not a value — JavaScript"),
    (re.compile(r"\"\"\"|'''"), "inside a docstring — prose, not a decision"),
]

# An override does not have to sit on the same line as its default. A wrapped
# `os.environ.get("X",\n    "http://localhost:11434")` reads as a bare host to a
# line-at-a-time scanner, and did: delta's public `providers.py` was
# flagged for a literal that has an override one line above it. So the override
# allowances — and only those — are also tested against the previous line
# joined to this one. The comment and docstring allowances stay single-line,
# because a comment above a value does not make the value a comment.
CONTINUATION = [a for a, why in ALLOW if "override" in why or "allowed on the line" in why]


def scan(root: pathlib.Path):
    findings = []
    for p in sorted(root.rglob("*")):
        if p.suffix not in CODE_EXT or set(p.parts) & SKIP_DIRS:
            continue
        try:
            # `errors="replace"` rather than a bare read: a source file with a
            # stray NUL byte in it is still a file with decisions in it, and a
            # decoding error here would read as a clean tree.
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        is_test = _is_test(p.relative_to(root))
        in_doc = False
        prev = ""
        for n, line in enumerate(lines, 1):
            # Docstrings describe; they do not configure. Track them so a
            # module explaining "we call localhost:11434" is not a finding.
            # Python only — a triple quote in shell or JavaScript is not one.
            if p.suffix in PY_EXT:
                if line.count('"""') % 2 or line.count("'''") % 2:
                    in_doc = not in_doc
                    continue
                if in_doc:
                    continue
            if any(a.search(line) for a, _ in ALLOW):
                prev = line
                continue
            joined = prev.rstrip() + " " + line.lstrip()
            if any(a.search(joined) for a in CONTINUATION):
                prev = line
                continue
            rules = RULES
            if p.suffix in SH_EXT:
                rules = RULES + [("absolute path", BARE_PATH, RULES[0][2])]
            if is_test:
                rules = [r for r in rules if r[0] in TEST_ONLY_KINDS]
            for name, pat, why in rules:
                if pat.search(line):
                    findings.append({"file": str(p.relative_to(root)), "line": n,
                                     "kind": name, "why": why,
                                     "text": line.strip()[:96]})
                    break
            prev = line
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
