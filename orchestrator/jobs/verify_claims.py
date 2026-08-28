#!/usr/bin/env python3
"""
verify_claims.py — does the commit an answered handoff cites actually exist?

    python3 -m jobs.verify_claims          # check every answered handoff
    python3 -m jobs.verify_claims --strict # exit 1 if any cited commit is missing

**Why this is a separate script and not part of `vault_check.py`.** The daily
checker is deterministic and offline by design — every finding is computable
from the files on disk, which is what makes it safe to run anywhere and safe to
trust. This one asks GitHub, so it can be slow, can fail for reasons that are
nobody's fault, and must never be able to turn the offline checker red. Keeping
them apart is deliberate; do not fold this in.

**The failure it exists for, 2026-08-22.** A handoff was closed with *"README
rewritten to all eight sections at `<owner>/<repo>` commit `69f289d`"*. The commit
does not exist — the API returns 422 — and the file had not been touched in six
days. Another project's identically-shaped claim, checked the same minute, was
real and merged. So the problem is not that agents lie; it is that **a status
cell naming a SHA looks more verified than one saying "done", while nothing was
verifying it.** A citation nobody checks is decoration.

It surfaced only because the owner opened the live page and asked. That is the exact
class of thing this vault exists to catch before he has to.

**What it does not do.** It does not judge whether the work was any good, or
whether the commit does what the cell says. It answers one question — *does this
SHA resolve in that repo* — because that is the question that is cheap, exact,
and was going unasked.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import pathlib

QUEUE = pathlib.Path(__file__).resolve().parent.parent / "Queue.md"

# **Whose repositories this vault cites.** Read from the environment, then from
# the account `gh` is signed in as, and only then from nothing — because a
# checker that resolves an unqualified repo name against somebody else's
# account will report a stranger's commits as missing, which reads as an
# accusation of fabricated work. Hardcoding one account here made the tool
# correct on exactly one machine. [no-hardcoding rule]
def _owner() -> str:
    v = os.environ.get("MIKOSHI_GITHUB_OWNER", "").strip()
    if v:
        return v
    try:
        got = subprocess.run(["gh", "api", "user", "--jq", ".login"],
                             capture_output=True, text=True, timeout=10)
        if got.returncode == 0 and got.stdout.strip():
            return got.stdout.strip()
    except Exception:
        pass
    return ""


OWNER = _owner()

# A SHA cited alongside a repo. Both forms agents actually write:
#   `<owner>/<repo>` commit `69f289d`      ·      PR #13, `19b7de4`
SHA = re.compile(r"`([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)`[^|]{0,40}?`([0-9a-f]{7,40})`")
# A bare hex string is NOT a commit. This vault is full of hex fingerprints by
# design — API-key fingerprints, golden-set exam fingerprints — and the first
# version of this checker reported three of them as missing commits, which is
# an accusation of fabricated work against agents who had done nothing wrong.
# So a bare SHA counts only when a commit word sits right next to it. Same
# lesson the funnel's contradiction check learned on 2026-08-18: match in a
# scope you can defend, not anywhere in the text.
BARE_SHA = re.compile(
    r"(?:commit|sha|pull request|PR\s*#\d+|merged|pushed)[^`|]{0,40}`([0-9a-f]{7,40})`"
    r"|`([0-9a-f]{7,40})`[^`|]{0,24}?(?:is merged|was merged|, merged|commit)",
    re.I)
ROW = re.compile(r"^\|\s*(H-\d+)\s*\|([^|]*)\|([^|]*)\|(.*)\|([^|]*)\|\s*$")

# Words that mean "this hex string identifies something that is not a commit".
NOT_A_COMMIT = re.compile(
    r"fingerprint|case set|question set|exam|golden|key\b|digest|sha256", re.I)


def _around(text: str, needle: str, span: int = 90) -> str:
    """The text either side of a hex string, which is the only thing that says
    what the hex string IS. Read it before calling anything a missing commit."""
    i = text.find(needle)
    return text[max(0, i - span): i + len(needle) + 30] if i >= 0 else ""


def answered_rows():
    if not QUEUE.is_file():
        return []
    out = []
    for line in QUEUE.read_text(encoding="utf-8").splitlines():
        m = ROW.match(line)
        if not m:
            continue
        num, frm, to, body, status = m.groups()
        if "answer" not in status.lower():
            continue
        out.append({"id": num, "to": to.strip(), "body": body, "status": status.strip()})
    return out


def commit_exists(repo: str, sha: str):
    """(True, subject) · (False, reason) · (None, reason) when we could not tell.

    None is not False. A network failure or a repo we cannot see is not evidence
    that a claim was wrong, and reporting it as one would make this checker the
    liar instead.
    """
    try:
        r = subprocess.run(
            ["gh", "api", f"repos/{repo}/commits/{sha}",
             "--jq", ".commit.message | split(\"\\n\")[0]"],
            capture_output=True, text=True, timeout=25)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return None, f"could not ask GitHub ({type(e).__name__})"
    if r.returncode == 0:
        return True, r.stdout.strip()[:70]
    err = (r.stderr or "").strip()
    if "No commit found" in err or "422" in err:
        return False, "no such commit in that repo"
    if "404" in err:
        return None, "repo not visible from here"
    return None, err.split("\n")[0][:70]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 if any cited commit does not exist")
    a = ap.parse_args()

    rows = answered_rows()
    checked = missing = unknown = 0
    print(f"{len(rows)} answered handoff(s) in the queue\n")

    for r in rows:
        text = r["body"] + " " + r["status"]
        pairs = SHA.findall(text)
        if not pairs:
            # **The common shape, and the first pattern missed all of it.**
            # Real cells write "merged (PR #13, `19b7de4`)" — the SHA with no
            # repo beside it, because the repo is obvious to the writer from
            # who the handoff was addressed to. So infer it the same way: the
            # target tag `@claude-code/beta` names the project, and the
            # project folder name is the repo name. Written after this checker
            # reported a clean 0/0 on a queue that contained a real SHA and a
            # known-bad one — a checker that matches nothing always passes.
            proj = r["to"].strip("`").split("/")[-1]
            found = {g for tup in BARE_SHA.findall(text) for g in tup if g}
            # "a sha256 of its case set — current set is `bf02b3bcba`" matched
            # on the word `sha`. A digest of a QUESTION SET is not a commit, and
            # neither is a key fingerprint. Drop any hex string whose immediate
            # neighbourhood says it identifies something other than a commit.
            found = {h for h in found
                     if not NOT_A_COMMIT.search(_around(text, h))}
            # With no account resolved, an unqualified name cannot be turned
            # into a repo. Skip rather than guess — checking the wrong
            # account's history reports a stranger's commits as missing.
            pairs = ([(f"{OWNER}/{proj}", sha) for sha in sorted(found)]
                     if OWNER else [])
        if not pairs:
            continue
        for repo, sha in pairs:
            checked += 1
            ok, note = commit_exists(repo, sha)
            if ok is True:
                print(f"  ok       {r['id']}  {repo}@{sha}  {note}")
            elif ok is False:
                missing += 1
                print(f"  MISSING  {r['id']}  {repo}@{sha}  — {note}")
                print(f"           closed against {r['to']}; the work it names "
                      f"is not in that repo")
            else:
                unknown += 1
                print(f"  ?        {r['id']}  {repo}@{sha}  — {note}")

    print(f"\n{checked} cited commit(s) · {missing} missing · {unknown} unknown")
    if not checked:
        print("No answered handoff cites a repo and a SHA together.\n"
              "**That is the weaker failure, not a clean result:** a cell that "
              "says 'done' cannot be checked at all. Ask for the SHA.")
    if missing:
        print("\nA handoff closed against work that is not there is worse than "
              "an open one — it stops anyone looking.")
        if a.strict:
            sys.exit(1)


if __name__ == "__main__":
    main()
