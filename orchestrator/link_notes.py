#!/usr/bin/env python3
"""
link_notes.py — find each note's nearest neighbours by meaning and propose links.

Two halves of one problem, now sharing one index:
  · this script repairs the ~800 orphans already in the vault
  · `funnel/link.py` stops new entries ever becoming orphans

Both read `funnel.store`, so a link proposed here and a link written at file time
are computed the same way — same model, same prefixes, same cosine metric. The
index is incremental: unchanged notes are not re-embedded.

  --apply         write a Related section into orphan notes  (default: propose only)
  --max-distance  cosine distance ceiling, default 0.45      (lower = stricter)
  --top           max links per note, default 3
  --min-links     only propose for notes with fewer than this many links

NOTE: the old `--threshold` flag was a cosine *similarity* floor. This takes a
cosine *distance* ceiling, which is the opposite direction. The flag was renamed
rather than reused so a stale command fails loudly instead of inverting.

Nothing leaves the machine.
"""
import argparse
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from funnel import embed_corpus, store  # noqa: E402

# **Windows consoles default to cp1252 and cannot encode the characters this
# codebase prints** — the log separator `·`, the em dash, and the `→` in every
# "here is the fix" line. On 2026-08-23 CI showed `record.py` dying on its own
# arrow, which meant the WRITE PATH was broken on Windows while every other
# check passed. Done at package import so a new script in here inherits it
# rather than having to remember; fixing twenty entry points one at a time is
# how the twenty-first gets missed.
import sys as _sys

for _s in (_sys.stdout, _sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


VAULT = store.VAULT
LINK_RE = re.compile(r"\[\[([^\]|#]+)")
# scaffolding, not knowledge — correctly standalone, never linked
SKIP_STEMS = {"CLAUDE", "README", "AGENTS", "GEMINI", "Agent Briefing",
              "Board", "Home", "Vault Origin", "_Inbox"}


def qualify(path: str, title: str) -> str:
    """Vault-relative link. Stems collide badly here — 16 notes are named _index."""
    return f"{path[:-3] if path.endswith('.md') else path}|{title}"


def degrees(paths):
    """Link degree per note: its own outbound links plus anything pointing at it."""
    inbound, outbound = {}, {}
    for p in paths:
        text = (VAULT / p).read_text(encoding="utf-8", errors="ignore")
        targets = {m.split("/")[-1].strip() for m in LINK_RE.findall(text)}
        outbound[p] = targets
        for t in targets:
            inbound.setdefault(t, set()).add(p)
    return {p: len(outbound[p]) + len(inbound.get(pathlib.Path(p).stem, set()))
            for p in paths}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="default; accepted for clarity")
    ap.add_argument("--max-distance", type=float, default=0.45)
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--min-links", type=int, default=1)
    # Fix one orphan without touching the others. vault_check reports orphans one
    # note at a time, but --apply wrote to every under-linked note at once — so
    # repairing a single 00-Inbox capture also appended a Related section to
    # `the owner Profile.md`, a curated file no routine should be editing. Substring
    # match on the note path. [@claude-code/maintenance · 2026-08-16]
    ap.add_argument("--only", help="restrict to note paths containing this substring")
    a = ap.parse_args()

    db = store.connect()
    if not db.vec:
        # sqlite-vec is not missing, it is installed in the funnel's venv and this
        # script was almost certainly started with the system python3. "pip install
        # sqlite-vec" sent readers off to reinstall a package they already had.
        # Name the interpreter that works. [@claude-code/maintenance · 2026-08-16]
        venv = VAULT / "02-Projects/project-four/code/tools/.venv/bin/python3"
        if venv.exists():
            sys.exit(f"sqlite-vec is not loaded — this needs the funnel's venv:\n"
                     f"  {venv} {' '.join(sys.argv)}")
        sys.exit("sqlite-vec is not loaded — run: pip install sqlite-vec")

    stats = embed_corpus.run(db, verbose=False)
    print(f"index: {stats}", file=sys.stderr)

    rows = db.execute("SELECT id, path, title FROM notes ORDER BY path").fetchall()
    rows = [r for r in rows if pathlib.Path(r["path"]).stem not in SKIP_STEMS]
    # degrees() derives inbound counts by scanning the paths it is handed, so it
    # must see the whole vault. Narrowing `rows` first would hide the links that
    # point AT the note and make it look like an orphan.
    deg = degrees([r["path"] for r in rows])
    if a.only:
        rows = [r for r in rows if a.only.lower() in r["path"].lower()]
        if not rows:
            sys.exit(f"--only {a.only!r} matched no note")

    proposals = {}
    for r in rows:
        if deg[r["path"]] >= a.min_links:
            continue
        text = (VAULT / r["path"]).read_text(encoding="utf-8", errors="ignore")
        vec = store.embed(f"{r['title']}\n\n{text}", kind="query")
        picks = [(p, t, d) for p, t, d in store.nearest_notes(db, vec, k=a.top + 3)
                 if p != r["path"] and d <= a.max_distance][:a.top]
        if picks:
            proposals[r["path"]] = picks

    if not proposals:
        print("nothing to propose — every note already meets the link threshold")
        return

    for path, picks in proposals.items():
        print(f"\n{path}")
        for p, t, d in picks:
            print(f"   {d:.3f}  →  [[{qualify(p, t)}]]")

    if not a.apply:
        print(f"\n{len(proposals)} notes would gain links. Re-run with --apply to write.")
        return

    for path, picks in proposals.items():
        f = VAULT / path
        body = f.read_text(encoding="utf-8").rstrip()
        links = "\n".join(f"- [[{qualify(p, t)}]]" for p, t, _ in picks)
        f.write_text(f"{body}\n\n## Related\n\n{links}\n", encoding="utf-8")
    print(f"\n✅ wrote a Related section into {len(proposals)} notes")


if __name__ == "__main__":
    main()
