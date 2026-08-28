#!/usr/bin/env python3
"""
readme_audit.py — does every public README make sense to someone with no access
to anything private?

    python3 -m jobs.readme_audit            # audit every public repo
    python3 -m jobs.readme_audit --strict   # exit 1 on any finding

**The failure, found by @owner reading his own GitHub on 2026-08-27.** A public
README cited a file inside his private vault and called it *"the source of
truth"*. Another credited an internal agent tag by name. A third compared its
licence choice to a private sibling project the reader has never heard of. None
of it was secret, and all of it was useless to a reader and obviously
machine-written to anyone who noticed.

**The class, which is the point:** *a public document written with private
context still attached.* Nobody decided to leak anything. The prose was written
by something that could see the whole vault and did not model a reader who
cannot.

**Two things this deliberately does NOT flag.**

  A product's own vocabulary. Mikoshi creates `01-Knowledge Base/` in the
  user's vault, so its README naming that folder is documentation, not a leak.
  Repos declare what is theirs in OWN_TERMS.

  A public sibling. Linking one public repo from another is useful. Only a
  PRIVATE sibling is a problem, because the reader cannot go and look.

**Em dashes are counted, not judged.** [@owner · 2026-08-27] The density is the
tell he reads as machine-written; the number is reported and the wording stays
a human call.
"""
import argparse
import base64
import json
import re
import subprocess
import sys

LEAKS = [
    ("agent tag", re.compile(r"@claude-code[/\w-]*|@[a-z]+-code\b|@admin\b")),
    ("owner attribution", re.compile(r"\[@[a-z-]+ ?[·.] ?\d{4}-\d{2}-\d{2}\]")),
    ("credits section", re.compile(r"^#{1,4}\s*(credits|acknowledge?ments|attribution|thanks)",
                                   re.I | re.M)),
    ("internal standard", re.compile(r"(Product )?README Standard|Use Cases\.md", re.I)),
    ("vault-only concept", re.compile(r"\bthe queue\b|\bhandoff\b|Decisions\.jsonl|Learnings\.jsonl",
                                      re.I)),
]

# Folder names a repo legitimately documents because its own product creates them.
OWN_TERMS = {
    # Mikoshi ships the queue and the handoff mechanism, and creates these
    # folders in the reader's own vault. Naming them is documentation.
    "mikoshi": [re.compile(r"0[0-5]-(Knowledge Base|Projects|Orchestrator|Inbox|Archive)"),
                re.compile(r"\bthe vault\b|CLAUDE\.md|PROTOCOL\.md|Live Status\.md", re.I),
                re.compile(r"\bhandoffs?\b|\bthe queue\b|Decisions\.jsonl|Learnings\.jsonl", re.I)],
}
VAULT_PATH = re.compile(r"0[0-5]-(Knowledge Base|Projects|Orchestrator|Inbox|Archive)|"
                        r"Live Status\.md|Open Board")


def repos():
    out = subprocess.run(["gh", "repo", "list", "--limit", "100", "--json",
                          "name,visibility,owner"], capture_output=True, text=True)
    rows = json.loads(out.stdout or "[]")
    pub = [r["name"] for r in rows if r["visibility"] == "PUBLIC"]
    priv = [r["name"].lower() for r in rows if r["visibility"] == "PRIVATE"]
    owner = rows[0]["owner"]["login"] if rows else ""
    return owner, pub, priv


def readme(owner, name):
    out = subprocess.run(["gh", "api", f"repos/{owner}/{name}/readme", "--jq", ".content"],
                         capture_output=True, text=True)
    if out.returncode != 0 or not out.stdout.strip():
        return None
    return base64.b64decode(out.stdout).decode("utf-8", "replace")


def audit(name, text, private_siblings, is_profile=False):
    own = OWN_TERMS.get(name.lower(), [])
    found = []
    for n, line in enumerate(text.splitlines(), 1):
        if any(p.search(line) for p in own):
            continue
        for kind, pat in LEAKS:
            m = pat.search(line)
            if m:
                found.append((n, kind, m.group(0)[:44]))
        if VAULT_PATH.search(line):
            found.append((n, "private vault path", VAULT_PATH.search(line).group(0)[:44]))
        # The profile repo IS a portfolio: naming a product that has not opened
        # yet is the point of it. What matters there is that it does not LINK
        # to a private repo, which is a 404 for every visitor. That is checked
        # by the link pass below, not here.
        if not is_profile:
            for sib in private_siblings:
                if sib != name.lower() and re.search(rf"\b{re.escape(sib)}\b", line, re.I):
                    found.append((n, "names a private repo", sib))
        for sib in private_siblings:
            if re.search(rf"github\.com/[\w-]+/{re.escape(sib)}\b", line, re.I):
                found.append((n, "links a private repo (404 for visitors)", sib))
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strict", action="store_true", help="exit 1 on any finding")
    a = ap.parse_args()

    owner, public, private = repos()
    if not public:
        print("no public repos found (is `gh` signed in?)"); return 2
    print(f"README audit — {len(public)} public repo(s) under {owner}\n")
    total = 0
    for name in sorted(public):
        text = readme(owner, name)
        if text is None:
            print(f"  {name:<20} NO README"); continue
        found = audit(name, text, private, is_profile=(name.lower() == owner.lower()))
        total += len(found)
        dashes = text.count("—")
        flag = "  " if not found else "!!"
        print(f"  {flag} {name:<20} {len(found):>2} finding(s), {dashes:>3} em dash(es)")
        for n, kind, tok in found[:8]:
            print(f"        L{n:<5} {kind:<22} {tok}")
    print(f"\n{total} finding(s). **A public README must stand on its own for a reader")
    print("with no access to anything private.** Every internal path, agent tag,")
    print("private sibling and vault-only word is cut or explained in place.")
    if total and a.strict:
        sys.exit(1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
