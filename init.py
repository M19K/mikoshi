#!/usr/bin/env python3
"""
init.py — set up a vault by asking, not by making you fill in a template.

    python3 init.py                 # the interview
    python3 init.py --defaults      # every answer defaulted, nothing asked
    python3 init.py --show          # what it would write, without writing

**Why an interview and not a template file.** A template with `<your area here>`
placeholders gets half-filled and then lied to for months: the file says one
thing, the person works another way, and every agent reads the file. Asking
produces answers, and an answer that was never given is visibly absent rather
than silently wrong.

**What it will not ask you.** Anything the protocol fixes. The log format, who
may write what, the rule that an unpinnable sentence gets deleted — those are in
`PROTOCOL.md`, they are identical in every vault, and they are the reason this
thing works. Measured on the vault it came from: of 27 answerable questions, 24
were answered out of artifacts those rules create. **A vault that customises the
bedrock is a vault that customises away its own accuracy.**

**What it writes**

    PROTOCOL.md      copied unchanged — the bedrock, never edited, replaced whole on upgrade
    CLAUDE.md        your answers, and a pointer to the protocol
    Home.md          navigation for a human
    05-Orchestrator/Queue.md
    01-Knowledge Base/ 02-Projects/ 03-Archive/ 00-Inbox/

**Your first day will feel empty, and that is correct.** With nothing written
down, the answer layer answers almost nothing — measured at 0 of 27 on an empty
store. It is not broken; it has nothing to cite and refuses to invent. The value
arrives in proportion to what you put in, and the notes are two thirds of it.
"""
import argparse
import datetime as dt
import os
import pathlib
import shutil
import sys

def venv_python(venv: pathlib.Path) -> pathlib.Path:
    """Windows puts it somewhere else, and `bin/python` was hardcoded.

    `.venv/bin/python` is POSIX only — on Windows it is `.venv/Scripts/python.exe`.
    So `init` failed to install the one dependency there, and the README and
    both routines told people to run a path that does not exist on their
    machine. Nothing had ever been run on Windows."""
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


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
TEMPLATES = HERE / "templates"
TODAY = dt.date.today().isoformat()

# Each question earns its place by being something the protocol cannot decide
# and the vault cannot infer. Anything answerable from the files is not asked.
QUESTIONS = [
    ("owner_tag", "What should agents call you in the log?",
     "One word, no @. It appears on every decision recorded as yours.", "owner"),
    ("areas", "What are your areas of focus?",
     "Comma-separated. These become the tags every project is filed under, and "
     "the health check validates against them. Three to six works; more and "
     "nothing is really a focus.", "Work, Learning"),
    ("projects", "What are you working on right now?",
     "Comma-separated. A folder gets created for each, with the three files. "
     "Leave blank to add them later.", ""),
    ("reply_len", "How long should a reply to you be?",
     "The single most-ignored preference there is, so it is written into the "
     "protocol your agents read. e.g. 'five points maximum' or 'a short "
     "paragraph'.", "five single-line points maximum"),
    ("jargon", "Technical language: fine, or explain everything?",
     "'fine' or 'plain'.", "plain"),
    ("stops", "What must never happen without asking you?",
     "The defaults are money, anything the outside world sees, anything "
     "irreversible, and anything that is a matter of your taste. Add to them or "
     "press enter.", "money, anything public, anything irreversible, anything that is my taste"),
    ("signal", "What makes something worth keeping?",
     "One sentence, in your words. It seeds the filter — and it is only a seed: "
     "the system learns your real taste from you marking things, and until it "
     "has 30 marks it changes nothing.", ""),
]


def ask(interactive: bool) -> dict:
    out = {}
    if not interactive:
        return {k: d for k, _, _, d in QUESTIONS}
    print(__doc__.split("**What it writes**")[0].strip() + "\n")
    print("=" * 72)
    for key, question, why, default in QUESTIONS:
        print(f"\n{question}")
        print(f"  {why}")
        if default:
            print(f"  [enter = {default}]")
        try:
            got = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nStopped. Nothing was written.")
            sys.exit(1)
        out[key] = got or default

    # **Read it back and make them confirm.** Seven questions answered one at a
    # time are seven things nobody has seen together, and these become rules
    # every future agent reads as theirs. So the whole set is printed back and
    # confirming it is an explicit yes — an agent must not be able to accept a
    # set the person never saw.
    print("\n" + "=" * 72)
    print("\nThis is what your agents will read. Nothing else is asked of you.\n")
    for key, question, _why, _d in QUESTIONS:
        val = out[key] or "(not answered)"
        print(f"  {question}\n      {val}\n")
    print("=" * 72)
    try:
        yes = input("\nIs this how you want your agents to work? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nStopped. Nothing was written.")
        sys.exit(1)
    if yes not in ("y", "yes"):
        print("\nNothing was written. Run init.py again — it takes two minutes,")
        print("and a wrong answer here is a rule every agent follows.")
        sys.exit(1)
    return out


def claude_md(a: dict) -> str:
    areas = [x.strip() for x in a["areas"].split(",") if x.strip()]
    rows = "\n".join(f"| `{x}` | |" for x in areas) or "| | |"
    return f"""# This Vault — read `PROTOCOL.md` first

**The protocol is the bedrock and is not in this file.** [`PROTOCOL.md`](PROTOCOL.md)
holds the rules that are identical in every Mikoshi vault — who may write what,
the log format, how decisions are recorded, why an unpinnable sentence is
deleted. Read it before acting. It is not edited; a new protocol version
replaces it whole, and this file survives because nothing of yours is in there.

**This file is the other half: how *this* owner works.** Written by `init.py`
on {TODAY} from answers given, and edited freely thereafter.

---

## Who this is for

The owner's tag is **`@{a['owner_tag']}`**. A line tagged `[@{a['owner_tag']} · date]`
is a claim that they decided it — see PROTOCOL §2.

## Areas of focus

Tags, not folders. Every project's `_index.md` carries a `domains:` list drawn
from this table, and the health check validates against it. **Add a column entry
saying what each one covers and whether it ever ends.**

| Tag | What it covers |
|---|---|
{rows}

## How to talk to the owner

- **Length: {a['reply_len']}.**
- **Language: {'technical terms are fine' if a['jargon'].lower().startswith('f') else 'plain language — no jargon, and if a term is unavoidable, say what it means in the same breath'}.**
- Order by: was the thing accomplished · is there a blocker · what changes what they do next.
- Single-line bullets. Tables are fine. Flowing prose is not.

## What stops at the owner

{a['stops']}.

Everything else is the agent's to decide and log — PROTOCOL §9 rule 1.

## What counts as signal

{a['signal'] or '_Not yet stated. The system has no opinion until you give it one._'}

**This is a seed, not the answer.** The real filter is learned from you marking
filed items signal or noise (`funnel.learn mark`), and it deliberately changes
nothing until there are 30 marks with both sides represented — a taste inferred
from three clicks is a guess with a mechanism attached.

## Sources

Nothing is ingested until you say what to read. See `05-Orchestrator/Sources.md`.
**Verify a feed by fetching it and counting items, never by getting a 200** — a
domain that looks right can serve an entirely different publication.
"""


def main():
    _force_utf8_console()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--defaults", action="store_true", help="ask nothing")
    ap.add_argument("--show", action="store_true", help="print, write nothing")
    ap.add_argument("--root", default=".", help="where to build the vault")
    a = ap.parse_args()

    root = pathlib.Path(a.root).resolve()
    answers = ask(interactive=not (a.defaults or a.show))

    if a.show:
        print(claude_md(answers))
        return

    existing = root / "CLAUDE.md"
    if existing.exists():
        # Never overwrite an answered interview. Someone re-running this to add
        # a project should not silently lose how they said they want to be
        # spoken to.
        raise SystemExit(
            f"{existing} already exists — this vault is already initialised.\n"
            f"Edit it directly, or move it aside first. Nothing was written.")

    for d in ("00-Inbox", "01-Knowledge Base", "02-Projects", "03-Archive",
              "05-Orchestrator"):
        (root / d).mkdir(parents=True, exist_ok=True)

    # **The code has to live inside the vault, and this is not a preference.**
    # Every module finds the vault by walking up from its own file — `store.py`
    # is `<vault>/05-Orchestrator/funnel/store.py` and takes the grandparent.
    # In the repo the same code sits at `<repo>/orchestrator/`, so a vault
    # created somewhere else would have `record.py` writing decisions into the
    # clone instead of into the vault, silently and with no error. Caught by
    # running it rather than reading it, 2026-08-23.
    #
    # So `init` installs the machinery where the machinery expects to be. If
    # the vault IS the clone — the normal case — this is a move, and `git pull`
    # still upgrades it.
    src_code = HERE / "orchestrator"
    dst_code = root / "05-Orchestrator"
    if src_code.exists() and not (dst_code / "funnel").exists():
        for child in src_code.iterdir():
            target = dst_code / child.name
            if child.is_dir():
                shutil.copytree(child, target, dirs_exist_ok=True)
            else:
                shutil.copy2(child, target)

    # The routines are the difference between a vault that answers questions
    # and one that maintains itself. They were not shipped at all until
    # 2026-08-23, so "it repairs itself daily" was true on one machine only.
    routines_src = TEMPLATES / "routines"
    if routines_src.is_dir():
        shutil.copytree(routines_src, root / "05-Orchestrator" / "routines",
                        dirs_exist_ok=True)

    shutil.copy2(TEMPLATES / "PROTOCOL.md", root / "PROTOCOL.md")
    # Record what the bedrock hashed to at install. `verify` compares against
    # this rather than against the repo, because the vault is often nowhere
    # near the clone — and a drift check that cannot find its reference reports
    # a failure that is really its own missing file.
    import hashlib
    (root / "05-Orchestrator" / ".protocol.sha256").write_text(
        hashlib.sha256((TEMPLATES / "PROTOCOL.md").read_bytes()).hexdigest()
        + "  PROTOCOL.md at install\n", encoding="utf-8")
    for tool in ("doctor.py", "verify.py"):
        if (HERE / tool).is_file():
            shutil.copy2(HERE / tool, root / tool)
    (root / "CLAUDE.md").write_text(claude_md(answers), encoding="utf-8")
    # Sources.md must exist even though it is empty. Without it the funnel dies
    # on a FileNotFoundError against the README's own quickstart command, which
    # is the first thing a new user runs. Reproduced on a fresh vault 2026-08-23.
    for name, dest in (("Home.md", "."), ("Queue.md", "05-Orchestrator"),
                       ("Sources.md", "05-Orchestrator")):
        src = TEMPLATES / name
        if src.exists():
            (root / dest / name).write_text(
                src.read_text(encoding="utf-8").replace("<YYYY-MM-DD>", TODAY),
                encoding="utf-8")

    # **A virtual environment, because the alternative does not work.** The
    # README used to say `pip install -e .`; on any Python new enough to
    # implement PEP 668 that fails outright with "externally-managed
    # environment", which is most machines now. The shipped routines also call
    # `.venv/bin/python` by name, so without this they fail on their first
    # command. Best effort — if it cannot be built, say so and carry on rather
    # than abandoning a vault that is otherwise complete.
    venv = root / ".venv"
    if not venv.exists():
        import subprocess
        try:
            subprocess.run([sys.executable, "-m", "venv", str(venv)],
                           check=True, capture_output=True, timeout=180)
            subprocess.run([str(venv_python(venv)), "-m", "pip",
                            "install", "-q", "sqlite-vec"],
                           check=True, capture_output=True, timeout=300)
            venv_ok = True
        except Exception as e:
            venv_ok = False
            venv_err = f"{type(e).__name__}"
    else:
        venv_ok = True

    made = []
    for proj in [p.strip() for p in answers["projects"].split(",") if p.strip()]:
        slug = proj.lower().replace(" ", "-")
        pd = root / "02-Projects" / slug
        pd.mkdir(parents=True, exist_ok=True)
        for f in ("_index.md", "Reference.md", "Live Status.md"):
            src = TEMPLATES / "project" / f
            if src.exists():
                pd.joinpath(f).write_text(
                    src.read_text(encoding="utf-8")
                       .replace("<Project Name>", proj)
                       .replace("<YYYY-MM-DD>", TODAY), encoding="utf-8")
        made.append(slug)

    ran_here = (dst_code / "funnel").exists()
    print(f"\nVault created at {root}")
    print(f"  PROTOCOL.md          the bedrock — do not edit it")
    print(f"  CLAUDE.md            your answers — edit this freely")
    print(f"  05-Orchestrator/     the machinery"
          f"{'' if ran_here else '  ** MISSING — see below **'}")
    vpy = venv_python(venv)
    rel = vpy.relative_to(root) if str(vpy).startswith(str(root)) else vpy
    print(f"  .venv/               python + sqlite-vec  ({rel})"
          f"{'' if venv_ok else '  ** NOT BUILT — see below **'}")
    print(f"  {len(made)} project(s): {', '.join(made) or 'none yet'}")
    if not venv_ok:
        print(f"\n  The virtual environment could not be built ({venv_err}).")
        print(f"  Everything else is in place. Build it by hand — the routines")
        print(f"  call `.venv/bin/python` by name:")
        print(f"    python3 -m venv .venv && {rel} -m pip install sqlite-vec")
    if not ran_here:
        print("\n  The code did not install. Every module finds the vault by")
        print("  walking up from its own file, so it must sit at")
        print("  <vault>/05-Orchestrator/. Copy it there before running anything.")
    print("\nNext: `python3 doctor.py` — it lists what is missing rather than")
    print("letting you find out one failure at a time. Then schedule the three")
    print("prompts in 05-Orchestrator/routines/, or nothing runs unattended.")
    print("\nIt is empty, so it will answer almost nothing today. That is the")
    print("system refusing to invent, not a broken install. Write notes, record")
    print("decisions as you make them, and it becomes useful in proportion.")


if __name__ == "__main__":
    main()
