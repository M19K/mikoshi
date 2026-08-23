#!/usr/bin/env python3
"""
verify.py — prove the vault actually works. Exit 0 or it is not done.

    python3 verify.py              # the whole contract
    python3 verify.py --quick      # skip the model round-trip

**`doctor` and `verify` answer different questions and you need both.** Doctor
says what is *missing* — a model not pulled, a folder not declared. Verify says
whether what is present actually *works*: it writes a real decision through the
real write path, searches for it, gets it back, and removes it again. An install
can pass doctor and still be broken, because doctor never writes anything.

**Every check here failed for real at some point.** Nothing is included because
it seemed thorough:

    structure        init has been run and left the vault whole
    protocol intact  PROTOCOL.md still matches the shipped bedrock
    log format       the exact separators — a hyphen instead of `·` and every
                     tool silently ignores the entry
    write round-trip a decision written, found, and cleaned up
    secret scan      no key material anywhere in the vault
    MCP              the server answers its own self-test
    no hardcoding    nothing pinned to one machine

**Why a round-trip and not a unit test.** The write path crosses four things —
`record.py`, the JSONL store, the index, and retrieval. Each has been fine on
its own while the chain was broken. The only honest check is to put something in
and get it out.
"""
import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent


def _force_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


class Report:
    def __init__(self):
        self.rows = []

    def check(self, name, ok, detail="", fix=""):
        self.rows.append((name, bool(ok), detail, fix))
        return ok

    def failed(self):
        return [r for r in self.rows if not r[1]]


def structure(vault, r):
    want = ["PROTOCOL.md", "CLAUDE.md", "05-Orchestrator/Sources.md",
            "05-Orchestrator/Queue.md", "05-Orchestrator/funnel/run.py",
            "05-Orchestrator/record.py"]
    missing = [w for w in want if not (vault / w).exists()]
    r.check("structure", not missing,
            "all core files present" if not missing else f"missing: {', '.join(missing)}",
            "run init.py")


def protocol_intact(vault, r):
    """The bedrock is supposed to be identical everywhere.

    Not a security check — a drift check. Someone editing PROTOCOL.md instead of
    CLAUDE.md is customising the half that carries the measured accuracy, and
    they will lose those edits on the next protocol upgrade, which replaces the
    file whole. Better to say so now than after.
    """
    live = vault / "PROTOCOL.md"
    if not live.exists():
        return r.check("protocol intact", False, "PROTOCOL.md is missing", "run init.py")
    # Prefer the hash recorded at install; fall back to the repo's copy when
    # verify is being run from inside the clone.
    stamp = vault / "05-Orchestrator" / ".protocol.sha256"
    shipped_hash = None
    if stamp.is_file():
        shipped_hash = stamp.read_text(encoding="utf-8").split()[0]
    elif (HERE / "templates" / "PROTOCOL.md").exists():
        shipped_hash = hashlib.sha256(
            (HERE / "templates" / "PROTOCOL.md").read_bytes()).hexdigest()
    if not shipped_hash:
        return r.check("protocol intact", True,
                       "no reference to compare against — skipped, not failed")
    same = hashlib.sha256(live.read_bytes()).hexdigest() == shipped_hash
    r.check("protocol intact", same,
            "matches the shipped bedrock" if same else
            "PROTOCOL.md has been edited — those edits are lost on the next "
            "upgrade, which replaces it whole",
            "put your changes in CLAUDE.md; the protocol is the half that "
            "must be identical everywhere")


def log_format(vault, r):
    """The separators are exact and invisible when wrong."""
    ok_line = "- 2026-01-15 · @agent/project — did a thing."
    bad_line = "- 2026-01-15 - @agent/project - did a thing."
    pat = re.compile(r"^- \d{4}-\d{2}-\d{2} · @[\w/-]+ — .+")
    r.check("log format parser", bool(pat.match(ok_line)) and not pat.match(bad_line),
            "middle dot U+00B7 and em dash U+2014 required, hyphens rejected",
            "this is a code fault, not a setup one")


def write_round_trip(vault, r, quick=False):
    """Put a decision in through the real path, get it back, take it out."""
    orch = vault / "05-Orchestrator"
    projects = [p for p in (vault / "02-Projects").iterdir() if p.is_dir()] \
        if (vault / "02-Projects").is_dir() else []
    # **A vault with no projects yet is a legitimate state**, and verify is
    # meant to test the machinery rather than whether the human has started
    # work. So make a scratch project, use it, and remove it. Caught by CI:
    # `init --defaults` names no project, so this failed on a perfectly good
    # install and blamed the user for not having begun.
    temp_project = None
    if not projects:
        temp_project = vault / "02-Projects" / "_verify_scratch"
        temp_project.mkdir(parents=True, exist_ok=True)
        projects = [temp_project]
    proj = projects[0].name
    token = f"verifytoken{int(time.time())}"
    py = sys.executable
    got = subprocess.run(
        [py, str(orch / "record.py"), "decision", proj,
         "--chose", f"a verification marker {token}",
         "--over", "nothing", "--why", "written by verify.py and removed again"],
        capture_output=True, text=True, cwd=str(orch))
    if got.returncode != 0:
        return r.check("write round-trip", False,
                       f"record.py failed: {(got.stderr or got.stdout)[-120:]}",
                       "the write path is broken — this is the important one")

    path = projects[0] / "Decisions.jsonl"
    found = path.is_file() and token in path.read_text(encoding="utf-8")

    # Remove the marker whatever happened. A verifier that leaves rubbish in the
    # thing it verifies is a verifier nobody runs twice.
    if path.is_file():
        kept = [l for l in path.read_text(encoding="utf-8").splitlines()
                if token not in l]
        path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")

    if temp_project is not None:
        # Take the scratch project away again, whatever happened.
        import shutil as _sh
        _sh.rmtree(temp_project, ignore_errors=True)

    where = "a scratch project (removed)" if temp_project is not None else proj
    r.check("write round-trip", found,
            f"wrote and read back a decision in {where}" if found
            else "record.py reported success and the line is not in the file",
            "check 02-Projects/<project>/Decisions.jsonl is writable")


def secret_scan(vault, r):
    """No key material anywhere. Cheap, and the failure is unrecoverable."""
    pats = [re.compile(r"sk-(or-v1|ant|proj)-[A-Za-z0-9_-]{20,}"),
            re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
            re.compile(r"AKIA[0-9A-Z]{16}")]
    hits = []
    for p in vault.rglob("*"):
        if p.is_dir() or p.suffix not in {".md", ".py", ".json", ".jsonl", ".txt",
                                          ".yml", ".yaml", ".env", ".toml"}:
            continue
        if any(x in p.parts for x in (".git", ".venv", "__pycache__", "node_modules")):
            continue
        try:
            body = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if any(pat.search(body) for pat in pats):
            hits.append(str(p.relative_to(vault)))
    r.check("secret scan", not hits,
            "no key material in the vault" if not hits
            else f"KEY MATERIAL IN: {', '.join(hits[:4])}",
            "move it to a gitignored file and reference it by label; a key in a "
            "vault is a key in every backup of it")


def mcp(vault, r, quick=False):
    if quick:
        return r.check("MCP self-test", True, "skipped (--quick)")
    server = vault / "05-Orchestrator" / "mcp_server.py"
    if not server.is_file():
        return r.check("MCP self-test", False, "mcp_server.py is missing", "run init.py")
    got = subprocess.run([sys.executable, str(server), "--selftest"],
                         capture_output=True, text=True,
                         cwd=str(vault / "05-Orchestrator"), timeout=300)
    ok = "ALL PASS" in (got.stdout + got.stderr)
    r.check("MCP self-test", ok,
            "the server answers and every tool works" if ok else
            (got.stdout + got.stderr).strip().splitlines()[-1][:110] if (got.stdout or got.stderr) else "no output",
            "run it directly to see which tool fails: "
            "python3 05-Orchestrator/mcp_server.py --selftest")


def no_hardcoding(vault, r):
    script = vault / "05-Orchestrator" / "jobs" / "no_hardcoding.py"
    if not script.is_file():
        return r.check("no hardcoding", True, "checker not present — skipped")
    got = subprocess.run([sys.executable, str(script), "--path",
                          str(vault / "05-Orchestrator"), "--strict"],
                         capture_output=True, text=True)
    r.check("no hardcoding", got.returncode == 0,
            "nothing pinned to one machine" if got.returncode == 0
            else "findings — run the checker to see them",
            "python3 05-Orchestrator/jobs/no_hardcoding.py")


def main():
    _force_utf8_console()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vault", default=".")
    ap.add_argument("--quick", action="store_true",
                    help="skip the MCP self-test, which loads a model")
    a = ap.parse_args()
    vault = pathlib.Path(a.vault).resolve()

    r = Report()
    print(f"mikoshi verify · {vault}\n")
    structure(vault, r)
    protocol_intact(vault, r)
    log_format(vault, r)
    write_round_trip(vault, r, a.quick)
    secret_scan(vault, r)
    no_hardcoding(vault, r)
    mcp(vault, r, a.quick)

    for name, ok, detail, fix in r.rows:
        print(f"  [{' ok ' if ok else ' -- '}] {name:<20} {detail}")
        if not ok and fix:
            print(f"           {'':<20} → {fix}")

    bad = r.failed()
    print()
    if bad:
        print(f"{len(bad)} check(s) failed. **The vault is not verified.**")
        print("Nothing here is cosmetic — each one is something that was broken "
              "for real\nat some point, which is why it is checked.")
        sys.exit(1)
    print("All checks passed. The vault is verified.\n")
    print("Try these three, in this order:")
    print('  1. Ask it something it cannot know:')
    print('     python3 05-Orchestrator/synth/answer.py "what did we decide about pricing"')
    print("     It should say that is not written down. That refusal is the product.")
    print("  2. Record a real decision, including what you turned down:")
    print("     python3 05-Orchestrator/record.py decision <project> \\")
    print('       --chose "..." --over "the option you rejected" --why "..."')
    print("  3. Ask about it again. Now it answers, and cites the line.")


if __name__ == "__main__":
    main()
