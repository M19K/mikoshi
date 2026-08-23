#!/usr/bin/env python3
"""
promote.py — move staged entries into the Knowledge Base.

**The funnel owns `01-Knowledge Base/`.** [@owner · 2026-08-16] The staging step
existed because the old write-scope table gave that folder to `@cowork`, which
belonged to the Recall era and is retired. Staging is now a checkpoint, not a
boundary: `run.py` writes there and then promotes inline, every run. The old
`--merge-kb` flag that gated this is gone; `--no-promote` is what holds
entries in staging now.

Every category has a destination. Nothing is left in staging with nowhere to go:

  tooling            → 01-Knowledge Base/Tooling Sources/<category>.md   (#### block)
  workflow · concept → 01-Knowledge Base/Ingested/<slug>.md              (atomic note)
  industry · news    → never promoted; they live and die in the digest

Dedup follows the rule already written in `Tooling Sources (Pilot).md`: match an
existing `#### Heading`, and on a match add a citation rather than a second entry.
Alphabetical insert otherwise. Never a blind append.

Matching is on `entry_key()`, not on the raw heading. Case-insensitive alone let
the same tool file twice under two spellings — measured 2026-08-20 at 14 duplicate
groups in 736 headings (H-022), across four mechanical variant classes:

  unicode look-alikes   `Qwen\u202f3.8\u202f27B` vs `Qwen 3.8 27B`, `GLM\u20115.3` vs `GLM 5.3`
  spacing and case      `Crew AI` / `CrewAI`, `Opus Clip` / `OpusClip`
  display name vs slug  `UI UX Pro Max` / `ui-ux-pro-max`
  loose qualifier       `Replit Free Mode` vs `Replit Free Mode (GPT-5.6 Luna powered)`

**Version parentheticals are exempt and must stay exempt.** `Claude Opus (4.5)`
and `Claude Opus (4.6)` are different models, not two spellings of one. Matching
is also within-file only — `Figma` in SaaS and `Figma (MCP)` in MCP Servers are
separate entries by design.

    python3 -m funnel.promote            # show the plan, change nothing
    python3 -m funnel.promote --apply
    python3 -m funnel.promote --date 2026-08-15 --apply
"""
import argparse
import datetime as dt
import pathlib
import re
import shutil
import unicodedata

from . import store

KB = store.VAULT / "01-Knowledge Base"
TOOLING = KB / "Tooling Sources"
INGESTED = KB / "Ingested"
STAGED = store.ORCH / "staged"
BACKUP = store.ORCH / "state" / "kb-backups"

HEADING_RE = re.compile(r"^####\s+(.+?)\s*$", re.M)
FM_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.S)
PAREN_RE = re.compile(r"\((.*?)\)")
VERSION_RE = re.compile(r"^v?\d+(?:[.\-]\d+)*$")
DASHES = "\u2010\u2011\u2012\u2013\u2014"


def entry_key(name: str) -> str:
    """The identity of a Knowledge Base entry, for matching only. Never displayed —
    the heading keeps whatever spelling arrived first.

    NFKC folds the invisible spaces the models emit (`\u202f` cost us a
    character-for-character duplicate of `Qwen 3.8 27B`); the dash table folds the
    ones NFKC leaves alone. Non-version parentheticals are dropped as qualifiers,
    then everything that is not a letter or a digit goes, which is what collapses
    `ui-ux-pro-max` onto `UI UX Pro Max`."""
    s = unicodedata.normalize("NFKC", name)
    for d in DASHES:
        s = s.replace(d, "-")
    s = PAREN_RE.sub(
        lambda m: m.group(0) if VERSION_RE.match(m.group(1).strip()) else " ", s)
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def parse(path: pathlib.Path):
    """Frontmatter + body. A deliberately small YAML subset — these files are
    machine-written by `filer.py`, so the shapes are known and fixed."""
    m = FM_RE.match(path.read_text(encoding="utf-8"))
    if not m:
        return None, None
    fm, body, key = {}, m.group(2), None
    for line in m.group(1).splitlines():
        if re.match(r"^\s*-\s", line) or line.startswith("    "):
            if key:
                fm.setdefault(f"{key}_lines", []).append(line.strip())
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            key, val = key.strip(), val.strip()
            if val.startswith("[") and val.endswith("]"):
                fm[key] = [v.strip().strip('"') for v in val[1:-1].split(",") if v.strip()]
            else:
                fm[key] = val.strip('"')
    return fm, body


def kb_block(fm, body) -> str:
    """Rebuild the `#### Name` entry from a staged note."""
    name = body.strip().split("\n")[0].lstrip("# ").strip()
    what = "\n".join(body.strip().split("\n")[1:]).strip().split("\n\n")[0].strip()
    conf = float(fm.get("confidence") or 0.5)
    label = "**High**" if conf >= 0.8 else "**Medium**" if conf >= 0.55 else "**Low**"
    cites = fm.get("citations") or [fm.get("source", "")]
    lines = [f"#### {name}",
             f"- **Category**: {fm.get('category', 'tooling')}",
             f"- **What it is**: {what}",
             f"- **Source**: [{fm.get('source', '')}]({fm.get('source_url', '')})",
             f"- **Confidence**: {label} — funnel, score {fm.get('score', '')}"]
    if len(cites) > 1:
        lines.append(f"- **Adoption**: {len(cites)} independent sources — " + ", ".join(cites))
    if fm.get("supersedes"):
        lines.append(f"- **Supersedes**: {fm['supersedes']}")
    lines.append(f"- *(funnel, {fm.get('source', '')}, retrieved {fm.get('filed', '')})*")
    return "\n".join(lines)


def insert(target: pathlib.Path, block: str, name: str, fm) -> str:
    """Insert alphabetically, or cite an existing entry. Returns the action taken."""
    text = target.read_text(encoding="utf-8")
    heads = [(m.start(), m.group(1).strip()) for m in HEADING_RE.finditer(text)]
    existing = {}
    for pos, h in heads:
        existing.setdefault(entry_key(h), (pos, h))

    key = entry_key(name)
    if key in existing:
        pos, head = existing[key]
        nxt = text.find("\n#### ", pos + 1)
        cut = nxt if nxt != -1 else len(text.rstrip())
        # A variant spelling is the only record that this tool is also called that.
        alias = f" as *{name}*" if name.strip() != head else ""
        note = (f"\n- *(also seen{alias}: {fm.get('source', '')}, {fm.get('filed', '')}, "
                f"[link]({fm.get('source_url', '')}))*")
        if note.strip() in text:
            return "already-cited"
        target.write_text(text[:cut].rstrip() + note + text[cut:], encoding="utf-8")
        return "cite"

    spot = len(text.rstrip())
    for pos, h in heads:
        if entry_key(h) > key:
            spot = pos
            break
    target.write_text(text[:spot].rstrip() + "\n\n" + block + "\n\n" + text[spot:].lstrip(),
                      encoding="utf-8")
    return "insert"


def promote(date: str, apply: bool = False):
    src = STAGED / date
    if not src.exists():
        return [], f"nothing staged for {date}"

    if apply:
        # the vault is not under version control — snapshot before the first
        # write so a bad run is recoverable
        BACKUP.mkdir(parents=True, exist_ok=True)
        snap = BACKUP / f"{date}-{dt.datetime.now().strftime('%H%M%S')}"
        shutil.copytree(TOOLING, snap / "Tooling Sources")
        INGESTED.mkdir(parents=True, exist_ok=True)

    plan = []
    for p in sorted(src.glob("*.md")):
        fm, body = parse(p)
        if not fm:
            plan.append((p.name, "skip", "unparseable"))
            continue
        cat = fm.get("category")
        name = body.strip().split("\n")[0].lstrip("# ").strip()

        if cat == "tooling":
            dest = pathlib.Path(store.VAULT / fm.get("destination", ""))
            if not dest.exists() or TOOLING not in dest.parents:
                dest = TOOLING / "Other and Reference.md"
            plan.append((name, "cite" if name.lower() in
                         {h.lower() for h in HEADING_RE.findall(dest.read_text(encoding='utf-8'))}
                         else "insert", dest.name))
            if apply:
                plan[-1] = (name, insert(dest, kb_block(fm, body), name, fm), dest.name)

        elif cat in ("workflow", "concept"):
            dest = INGESTED / p.name
            plan.append((name, "atomic", f"Ingested/{p.name}"))
            if apply:
                dest.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")

        else:
            plan.append((name, "skip", f"{cat} — digest only, never filed"))
    return plan, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    plan, err = promote(a.date, apply=a.apply)
    if err:
        print(err)
        return
    for name, action, target in plan:
        print(f"  {action:12} {name[:44]:46} → {target}")
    counts = {}
    for _, action, _ in plan:
        counts[action] = counts.get(action, 0) + 1
    print(f"\n{counts}" + ("" if a.apply else "\n(plan only — re-run with --apply)"))


if __name__ == "__main__":
    main()
