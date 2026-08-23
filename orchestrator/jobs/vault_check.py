#!/usr/bin/env python3
"""
vault_check.py — deterministic vault health check.

No LLM. Everything here is a fact that can be computed, so it should be.
An agent reads this output and decides what to do about it.

Usage:  python3 vault_check.py [--vault PATH] [--json]
Exit:   0 = clean, 1 = findings
"""
import argparse, json, re, sys
from datetime import date, datetime, timedelta
from pathlib import Path

STALE_CLAIM_DAYS = 5    # an Active row nobody has touched
QUIET_PROJECT_DAYS = 14  # a project with no log entry

# Classes that are DASHBOARD STATE, not work. They are computed and printed —
# the information is worth having — but they never set the exit code and are
# never counted as findings.
#
# `quiet-project` is here because the vault's own rule says so: Queue.md,
# [@owner · 2026-08-15] — "Project activity is state, not a question: a project
# untouched for weeks means the owner is working elsewhere, which is normal."
# Emitting it as a finding meant a vault with nothing whatsoever to act on
# still exited 1 every morning, so the exit code stopped meaning anything and
# a real finding read the same as four projects the owner simply is not working on
# this month. A daily check that always fails is a check nobody reads.
# [@claude-code/maintenance · 2026-08-19]
TELEMETRY = {"quiet-project"}

# Log-entry grammar. MUST stay identical to ENTRY in vault-status.py and the
# `entry` pattern in vault-lint.py — a looser pattern here passes entries that
# vault-status.py silently drops from its roll-up. [@claude-code/maintenance · 2026-08-18]
LOG_ENTRY = re.compile(r"^- (\d{4}-\d{2}-\d{2}) · (@[a-z0-9-]+(?:/[a-z0-9-]+)?)(?: ?\([^)]*\))? — (.*)$")
# @cowork retired 2026-08-16 but stays valid: entries it signed are history,
# and history must not start reporting as an unknown harness.
VALID_HARNESS = {"@owner", "@admin", "@claude-code", "@hermes", "@cowork", "@unknown-agent"}
OWNER_TAG = re.compile(r"^(@[a-z0-9-]+)(?:/([a-z0-9-]+))?$")
# Scopes that name a ROLE, not a project. One per scheduled routine, and this
# list is the only place they are enumerated — check 7 derives from it rather
# than keeping its own copy, which is how `project-four` ended up in one
# list and not the other.
#
# `ingestion` was missing until 2026-08-19, so the first log entry signed
# `@claude-code/ingestion` reported as two findings — an unknown project in the
# log and a stale reference on the Open Board — when the tag was correct and the
# checker was out of date. `mikoshi-daily-ingestion` is a real routine alongside
# `mikoshi-daily-vault-check` and `mikoshi-weekly-reconciliation`.
# **Adding a routine means adding its scope here.** [@claude-code/maintenance · 2026-08-19]
ROUTINE_SCOPES = {"maintenance", "reconciliation", "ingestion"}
MULTI_SESSION = {"@claude-code"}                     # harnesses running several sessions at once
SCOPE_REQUIRED_FROM = "2026-08-11"                   # date the session-scope rule took effect

def notes(vault):
    """Every real vault note. Excludes code/ and .obsidian — code is not context.

    Also excludes the funnel's own directories. These are machine output, not
    notes, and every one of them would report as an orphan forever:
      state/    the SQLite store and the pre-promotion KB snapshots
      staged/   entries between filing and promotion, carrying their own schema
      digests/  one generated note per run
      funnel/   source code, and a README that documents it
      jobs/     the maintenance scripts, likewise
    Promoted entries land in `01-Knowledge Base/`, which is scanned normally —
    that is where an orphan would be a real finding. [@claude-code · 2026-08-16]
    """
    skip = {"code", "03-Archive", "state", "staged", "digests", "funnel", "jobs"}
    # NEVER READ THE KEY FILE. It is a .md holding live API keys, so it would
    # otherwise be parsed like a note, held in memory, and reported as an orphan
    # forever — and a checker that quotes a line to explain a finding would quote
    # a secret. The correct number of tools that open this file is one, and it is
    # `ledger/keys.py`. [@claude-code/project-four · 2026-08-20]
    secret = {"keys.md", "keys.local.md"}
    return [p for p in vault.rglob("*.md")
            if p.name not in secret
            and not any(x.startswith(".") or x in skip for x in p.parts)]

def link_index(vault):
    """Map every form a link may be written in → the note it resolves to.

    Two populations, deliberately kept apart:

      scanned notes  what `notes()` returns. Resolvable by bare stem *and* by
                     full path, and subject to the orphan check.
      generated .md  digests, staged entries, funnel/jobs docs, the archive.
                     Resolvable by FULL PATH ONLY, never by bare stem.

    The second half is why this function exists. A KB entity that cites its
    source writes `[[05-Orchestrator/digests/2026-08-15|…]]`, and the digest is
    real — but `notes()` excludes digests, so every one of those citations
    reported as a broken link. Resolution and scanning are different questions:
    a file can be a legitimate link target without being a note we audit.

    Bare stems are withheld from the generated half on purpose. `staged/` and
    `state/kb-backups/` hold pre-promotion COPIES of KB entities, so registering
    their stems would let a backup shadow the live note — and the orphan check
    resolves through this same map, so a shadowed note would report as an
    orphan. Full paths are unique; stems are not. [@claude-code/maintenance · 2026-08-16]
    """
    ns = notes(vault)
    known = {}
    for p in vault.rglob("*.md"):
        if any(x.startswith(".") or x == "code" for x in p.parts):
            continue
        rel = str(p.relative_to(vault).with_suffix(""))
        known.setdefault(rel.lower(), rel)
    for d in vault.rglob("*"):
        if d.is_dir() and not any(x.startswith(".") or x == "code" for x in d.parts):
            rel = str(d.relative_to(vault))
            known[rel.lower()] = rel
            known[d.name.lower()] = rel
    for p in ns:
        rel = str(p.relative_to(vault).with_suffix(""))
        known[rel.lower()] = rel
        known[p.stem.lower()] = rel

    # Obsidian resolves PARTIAL paths — `[[Recall Pipeline/AI Knowledge Tracking]]`
    # finds `01-Knowledge Base/Recall Pipeline/AI Knowledge Tracking.md`. Matching
    # only the full path or the bare stem missed everything in between, so links
    # that open correctly in the vault reported broken. Kept in its own map: where
    # two notes share a suffix it is genuinely ambiguous, and `None` records that
    # — enough to prove the link resolves, not enough to credit one note with the
    # inbound edge. Sorted so a collision resolves the same way every run.
    # [@claude-code/maintenance · 2026-08-16]
    suffix = {}
    for p in sorted(ns):
        parts = p.relative_to(vault).with_suffix("").parts
        rel = str(p.relative_to(vault).with_suffix(""))
        for i in range(1, len(parts)):
            k = "/".join(parts[i:]).lower()
            if k in known:
                continue
            suffix[k] = None if k in suffix and suffix[k] != rel else rel
    return ns, known, suffix

def last_log_date(p):
    """Newest ISO date in a Live Status log."""
    if not p.exists():
        return None
    ds = re.findall(r"^- (\d{4}-\d{2}-\d{2})", p.read_text(encoding="utf-8"), re.M)
    return max(ds) if ds else None

def frontmatter(p):
    if not p.exists():
        return {}
    t = p.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---", t, re.S)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
    return out

# `updated:`/`last_write:` drifting behind the newest log entry was found and
# hand-fixed on 2026-08-17, again on 2026-08-20, and again on 2026-08-22 — four
# projects each time. It recurs because appending a log line and stamping the
# frontmatter are two actions and only the first one is the agent's purpose, so
# the second is the one that gets dropped. A finding that returns every few days
# is not a finding, it is a missing repair: the log PROVES the write date, the
# check only ever flags one direction, and stamping forward therefore cannot
# lose information. Deliberately narrow — it repairs this one class and touches
# nothing else. [@claude-code/maintenance · 2026-08-22]
def stamp(vault):
    """Repair frontmatter dates that trail the newest log entry. Returns changes."""
    changed = []
    for pd in sorted((vault / "02-Projects").iterdir()):
        ls = pd / "Live Status.md"
        if not pd.is_dir() or pd.name.startswith(".") or not ls.exists():
            continue
        logged = last_log_date(ls)
        if not logged:
            continue
        txt = ls.read_text(encoding="utf-8")
        m = re.match(r"^---\n(.*?)\n---", txt, re.S)
        if not m:
            continue
        fm, rest = m.group(1), txt[m.end():]
        new = fm
        for field in ("updated", "last_write"):
            cur = frontmatter(ls).get(field, "").strip("\"' ")
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", cur) and cur < logged:
                new = re.sub(rf"^({field}:).*$", rf"\g<1> {logged}", new, flags=re.M)
                changed.append((pd.name, field, cur, logged))
        if new != fm:
            ls.write_text(f"---\n{new}\n---{rest}", encoding="utf-8")
    return changed


def check(vault):
    f = []
    today = date.today()
    ns, known, suffix = link_index(vault)

    # 1. broken wikilinks — markdown only. Scanning .py flags f-strings that
    #    *generate* links, which is what produced the false positives on 2026-08-13.
    for p in ns:
        txt = p.read_text(encoding="utf-8")
        for m in re.finditer(r"\[\[([^\]]+)\]\]", txt):
            raw = m.group(1)
            if "{" in raw:      # `[[{qualify(q)}]]` — a template that *generates* links
                continue
            # Inside an inline code span? Count backticks between line start and the
            # link: odd means we are inside a span, even means a span opened and
            # closed before it. Testing `"`" in line` instead treated ANY earlier
            # inline code as cover, which silently hid real broken links — a line
            # like "imported from `~/x.md` as [[Gone]]" never reported.
            # [@claude-code/maintenance · 2026-08-16]
            start = txt.rfind("\n", 0, m.start()) + 1
            if txt[start:m.start()].count("`") % 2:
                continue
            tgt = raw.split("|")[0].split("#")[0].strip().replace("\\", "")
            if not tgt:      # [[#Heading]] — anchor within this same note
                continue
            if tgt.lower() not in known and tgt.lower() not in suffix:
                ln = txt[:m.start()].count("\n") + 1
                f.append(("broken-link", f"{p.relative_to(vault)}:{ln}", f"[[{raw}]] does not resolve"))

    # 2. stale claims — someone took a project and walked away
    q = vault / "05-Orchestrator" / "Queue.md"
    if q.exists():
        qt = q.read_text(encoding="utf-8")
        active = qt.split("## Active")[1].split("##")[0] if "## Active" in qt else ""
        for row in re.findall(r"^\|\s*([\w-]+)\s*\|\s*`?([@\w/-]+)`?\s*\|[^|]*\|\s*(\d{4}-\d{2}-\d{2})", active, re.M):
            proj, agent, since = row
            age = (today - datetime.strptime(since, "%Y-%m-%d").date()).days
            ls = vault / "02-Projects" / proj / "Live Status.md"
            logged = last_log_date(ls)
            log_age = (today - datetime.strptime(logged, "%Y-%m-%d").date()).days if logged else 999
            if age >= STALE_CLAIM_DAYS and log_age >= STALE_CLAIM_DAYS:
                f.append(("stale-claim", proj,
                          f"{agent} claimed it {age}d ago; last log {logged or 'never'}. Released, or forgotten?"))

    # 3. owner mismatch — Queue says one thing, the project says another
    if q.exists():
        qt = q.read_text(encoding="utf-8")
        claimed = set(re.findall(r"^\|\s*([\w-]+)\s*\|\s*`@", qt, re.M))
        for pd in sorted((vault / "02-Projects").iterdir()):
            if not pd.is_dir():
                continue
            own = frontmatter(pd / "Live Status.md").get("owner") or frontmatter(pd / "_index.md").get("owner")
            has_owner = bool(own) and own not in ("none", "—", "")
            if has_owner and pd.name not in claimed:
                f.append(("owner-mismatch", pd.name, f"owner: {own} but not in the Queue's Active table"))
            if not has_owner and pd.name in claimed:
                f.append(("owner-mismatch", pd.name, "in Queue Active but owner: is none"))

    # 4. quiet projects — active status, nothing logged in a fortnight
    for pd in sorted((vault / "02-Projects").iterdir()):
        if not pd.is_dir() or pd.name.startswith("."):
            continue
        if frontmatter(pd / "_index.md").get("status") != "active":
            continue
        logged = last_log_date(pd / "Live Status.md")
        if logged:
            age = (today - datetime.strptime(logged, "%Y-%m-%d").date()).days
            if age >= QUIET_PROJECT_DAYS:
                f.append(("quiet-project", pd.name, f"status: active but nothing logged for {age}d"))

    # 5. schema drift
    for pd in sorted((vault / "02-Projects").iterdir()):
        if not pd.is_dir() or pd.name.startswith("."):
            continue
        for req in ("_index.md", "Reference.md", "Live Status.md"):
            if not (pd / req).exists():
                f.append(("schema-drift", pd.name, f"missing {req}"))
        fm = frontmatter(pd / "_index.md")
        for key in ("type", "status", "domains"):
            if key not in fm:
                f.append(("schema-drift", pd.name, f"_index.md has no `{key}:`"))

        # `last_write:` older than the newest log entry. The log PROVES a write
        # happened on that date, so a lower `last_write` is always wrong — and
        # the stale-claim check reads log dates while a human reads this field,
        # so drift makes the two disagree about when a project was last touched.
        # Only ever flagged in this one direction: `last_write` AHEAD of the log
        # is legitimate, since state prose can be corrected without a log entry.
        # Found drifting in four projects on 2026-08-17, uncaught by any check.
        # [@claude-code/maintenance · 2026-08-17]
        lw = frontmatter(pd / "Live Status.md").get("last_write", "").strip('"\' ')
        logged = last_log_date(pd / "Live Status.md")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", lw) and logged and lw < logged:
            f.append(("schema-drift", pd.name,
                      f"Live Status.md `last_write: {lw}` predates its newest log entry ({logged})"))

        # Same rule for `updated:`, and the same one direction only. Every other
        # project carries it beside `last_write:`; `project-two` and `project-three` carried
        # neither the key nor any complaint about its absence, and two more files
        # outside 02-Projects had simply stopped being touched — the Open Board
        # said 2026-08-19 while holding a 2026-08-20 entry of its own. Nothing
        # checked this field at all, which is why all four drifted unseen.
        # [@claude-code/maintenance · 2026-08-20]
        lsfm = frontmatter(pd / "Live Status.md")
        if (pd / "Live Status.md").exists() and "updated" not in lsfm:
            f.append(("schema-drift", pd.name, "Live Status.md has no `updated:`"))
        up = lsfm.get("updated", "").strip('"\' ')
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", up) and logged and up < logged:
            f.append(("schema-drift", pd.name,
                      f"Live Status.md `updated: {up}` predates its newest log entry ({logged})"))

    # 6. orphans — no links out, and nothing links in. Invisible to traversal.
    inbound = set()
    for p in ns:
        for m in re.finditer(r"\[\[([^\]]+)\]\]", p.read_text(encoding="utf-8")):
            t = m.group(1).split("|")[0].split("#")[0].strip().replace("\\", "")
            tgt = known.get(t.lower()) or suffix.get(t.lower())
            if tgt:
                inbound.add(tgt.lower())
    # Scaffolding files are standalone by design, not orphans — and this set is
    # deliberately the same one `link_notes.py` carries as SKIP_STEMS, where the
    # rule is stated outright: "scaffolding, not knowledge — correctly standalone,
    # never linked".
    #
    # It used to apply only at the vault ROOT, and the two tools therefore
    # disagreed about the same file. On 2026-08-19 `QA/features/README.md`
    # reported as an orphan here while `link_notes.py --only` — the remedy this
    # protocol names — answered "matched no note" and refused to touch it. A
    # finding the prescribed fix cannot act on is a checker defect, not a vault
    # defect. A README describes the directory it sits in and is found by being
    # there, at any depth. [@claude-code/maintenance · 2026-08-19]
    ENTRY = {"readme", "agents", "gemini", "claude", "agent briefing",
             "board", "home", "vault origin", "_inbox"}
    # A QA run folder is dated EVIDENCE, not a note. Some of it is output the
    # product under test wrote to disk and the run captured verbatim — on
    # 2026-08-19 a project-three memory file, frontmatter and all, reported as an orphan
    # because it happened to be markdown. Evidence is located by its run folder,
    # never by traversal, so nothing will ever link to it and the finding can
    # only ever be noise. Exempted from the ORPHAN check only: these files are
    # still scanned for broken links, which is a real defect wherever it occurs.
    # [@claude-code/maintenance · 2026-08-19]
    for p in ns:
        rel = str(p.relative_to(vault).with_suffix(""))
        parts = p.relative_to(vault).parts
        if any(a == "QA" and b == "runs" for a, b in zip(parts, parts[1:])):
            continue
        if p.stem.lower() in ENTRY:
            continue
        if rel.lower() in inbound:
            continue
        if re.search(r"\[\[[^\]]+\]\]", p.read_text(encoding="utf-8")):
            continue
        f.append(("orphan", str(p.relative_to(vault)), "no links in or out"))

    # 7. stale references — the rename-propagation class.
    #
    # Every cross-reference in this vault is a string typed into prose, so
    # renaming a project silently rots N places. `agentops` → `project-three` on
    # 2026-08-16 broke a Queue row, an ownership claim, three wikilinks, two
    # Knowledge Base entries and a ledger label — one rename, eight findings,
    # none of which announced itself.
    #
    # A dated log line is HISTORY and must never be flagged: "@cowork's lane"
    # written on 2026-08-10 was true on 2026-08-10. Same for a sentence that
    # is explicitly about the retirement or rename. [@claude-code · 2026-08-16]
    projects = {d.name for d in (vault / "02-Projects").iterdir()
                if d.is_dir() and not d.name.startswith(".")}
    roles = ROUTINE_SCOPES   # single source; a real project is already in `projects`
    logline = re.compile(r"^-\s+\d{4}-\d{2}-\d{2}\s+·")
    # A line documenting a change may name the old thing — that is the record
    # working, not a stale reference. `is now` was added 2026-08-19: the Notice
    # announcing `model-routing` → `project-four` explains that project-three's log no
    # longer carries `@claude-code/agentops`, and got flagged for naming the tag
    # it was reporting the removal of. Every phrase here is one a rename or
    # retirement notice actually used.
    about_change = re.compile(
        r"retired|renamed|superseded|was folded|archived|is now|no longer", re.I)
    seen = {}
    for p in ns:
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            s = line.strip()
            if logline.match(s) or about_change.search(s):
                continue
            for m in re.finditer(r"02-Projects/([\w-]+)", line):
                if m.group(1) not in projects:
                    seen.setdefault(m.group(1), []).append(f"{p.relative_to(vault)}:{i}")
            for m in re.finditer(r"@claude-code/([\w-]+)", line):
                if m.group(1) not in projects and m.group(1) not in roles:
                    seen.setdefault(m.group(1), []).append(f"{p.relative_to(vault)}:{i}")
    for name, places in sorted(seen.items(), key=lambda kv: -len(kv[1])):
        f.append(("stale-reference", name,
                  f"names a project that does not exist, in {len(places)} live place(s): "
                  + ", ".join(places[:4]) + (" …" if len(places) > 4 else "")))

    # 8. log hygiene — ported from vault-lint.py, which was never scheduled.
    #
    # This was the supplement's unique value and the daily run was blind to it:
    # on 2026-08-18 a hand-run of vault-lint found four findings this canonical
    # check could not see, the oldest dating to 2026-08-16. A check that only
    # runs when someone remembers it is not a check. The jobs README carried
    # "folding them in is the open task" — this is that.
    # [@claude-code/maintenance · 2026-08-18]
    iso_today = today.isoformat()
    for pd in sorted((vault / "02-Projects").iterdir()):
        if not pd.is_dir() or pd.name.startswith("."):
            continue
        ls = pd / "Live Status.md"
        if not ls.exists():
            continue           # already reported by the schema-drift check
        body = ls.read_text(encoding="utf-8")

        # frontmatter values are bare by convention; quoting one makes the same
        # owner sort and compare as two different strings across tools
        fm = re.match(r"^---\n(.*?)\n---", body, re.S)
        if fm:
            for field in ("owner", "last_write"):
                if not re.search(rf"^{field}:", fm.group(1), re.M):
                    f.append(("log-hygiene", pd.name,
                              f"Live Status.md has no `{field}:` (the ownership lock lives here)"))
            for m in re.finditer(r"^(owner|last_write|status|type):\s*(.+)$", fm.group(1), re.M):
                if m.group(2).strip().startswith('"'):
                    f.append(("log-hygiene", pd.name,
                              f"`{m.group(1)}:` value is quoted; convention is a bare value"))
                # the lock is compared as a plain string against the Queue's Active
                # table, so a tag missing its `@` is a lock that silently matches
                # nothing — present here, invisible to every tool that reads it
                if m.group(1) == "owner":
                    own = m.group(2).strip()
                    if own not in ("none", "-", "\u2014", ""):
                        t = OWNER_TAG.match(own)
                        if not t:
                            f.append(("log-hygiene", pd.name,
                                      f"`owner: {own}` is not a roster tag "
                                      f"(expected @harness/scope, e.g. @claude-code/{pd.name})"))
                        elif t.group(1) not in VALID_HARNESS:
                            f.append(("log-hygiene", pd.name,
                                      f"`owner: {own}` names an unknown harness {t.group(1)}"))

        if "## Log" not in body:
            f.append(("log-hygiene", pd.name, "Live Status.md has no `## Log` section"))
            continue
        log_lines = [l.strip() for l in body.split("## Log", 1)[1].splitlines()]
        dates = []
        for line in log_lines:
            m = LOG_ENTRY.match(line)
            if not m:
                continue
            d, tag = m.group(1), m.group(2)
            dates.append(d)
            harness, _, scope = tag.partition("/")
            if harness not in VALID_HARNESS:
                f.append(("log-hygiene", pd.name, f"unknown agent tag {tag}"))
            elif scope and scope not in projects and scope not in ROUTINE_SCOPES:
                f.append(("log-hygiene", pd.name, f"tag {tag} names a project that does not exist"))
            elif harness in MULTI_SESSION and not scope and d >= SCOPE_REQUIRED_FROM:
                f.append(("log-hygiene", pd.name,
                          f"{tag} on {d} needs a session scope (e.g. {harness}/{pd.name})"))
            if d > iso_today:
                f.append(("log-hygiene", pd.name, f"log entry dated in the future ({d})"))
        # Out-of-order dates have TWO causes and the remedy differs, so the
        # message must not assert one. A genuinely late-appended entry is an
        # append-only violation; an entry sitting in the right place with a
        # mistyped date is not. project-three was the second on 2026-08-18 — its
        # "2026-08-13" Grok-docs line was committed 2026-08-15 and logged among
        # the 2026-08-16 block. Never reorder to silence this.
        #
        # Which is exactly why the finding has to be CLEARABLE. The log is
        # append-only, so an out-of-order date is permanent, and a check that
        # fires forever on something no one is allowed to fix trains everyone to
        # skip the report. So: a later `CORRECTION` entry citing the offending
        # date in backticks retires it. The vault's own correction mechanism —
        # a new line referencing the old — becomes the thing that clears the
        # finding, and reordering still never does. Known narrow cost: this
        # exempts every entry sharing that date, so a genuine late append on an
        # already-corrected date would hide. [@claude-code/maintenance · 2026-08-18]
        corrected = set()
        for line in log_lines:
            m = LOG_ENTRY.match(line)
            if m and "CORRECTION" in m.group(3):
                corrected.update(re.findall(r"`(\d{4}-\d{2}-\d{2})`", m.group(3)))
        ordered = [d for d in dates if d not in corrected]
        if ordered != sorted(ordered):
            bad = [d for a, d in zip(sorted(ordered), ordered) if a != d]
            f.append(("log-hygiene", pd.name,
                      f"log dates are not ascending (first divergence {bad[0] if bad else '?'}) — "
                      "either an entry was appended late, or its date is mistyped. Check which, then "
                      "correct with a NEW line citing the bad date in `backticks`; never reorder."))
        for st in [l for l in log_lines if l.startswith("- ") and not LOG_ENTRY.match(l)][:3]:
            f.append(("log-hygiene", pd.name, f"malformed log line → {st[:70]}"))

    # 9. domain tag drift — `domains:` validated against CLAUDE.md's canonical table.
    #    Also ported from vault-lint.py. If the table itself stops parsing, that is
    #    the finding: silently validating against an empty set passes everything.
    canonical = set()
    cmd = vault / "CLAUDE.md"
    if cmd.exists():
        canonical = {m.group(1).strip()
                     for m in re.finditer(r"^\| `([^`]+)` \|", cmd.read_text(encoding="utf-8"), re.M)}
    if not canonical:
        f.append(("domain-drift", "CLAUDE.md", "could not parse the canonical domain tag table"))
    else:
        for pd in sorted((vault / "02-Projects").iterdir()):
            if not pd.is_dir() or pd.name.startswith("."):
                continue
            raw = frontmatter(pd / "_index.md").get("domains", "").strip()
            tags = [t.strip() for t in raw.strip("[]").split(",") if t.strip()] if raw else []
            for tag in tags:
                if tag not in canonical:
                    f.append(("domain-drift", pd.name,
                              f"domain tag '{tag}' is not in CLAUDE.md's canonical table"))

    # 11b. ambiguous links — a bare `[[Name]]` that more than one file answers to.
    #
    #      Folded in from `vault-lint.py` on 2026-08-19, which was the last check
    #      that lived only there. Its own README had called it "largely
    #      superseded" since 2026-08-18, so it was scaffolding from an earlier
    #      attempt kept alive by one unique check. Folding it in retires the
    #      script rather than leaving two overlapping linters nobody schedules.
    #
    #      A link resolving to two files is not broken — it silently picks one,
    #      which is worse, because nothing reports it and the reader never learns
    #      they were sent to the wrong note.
    from collections import defaultdict as _dd
    by_base = _dd(list)
    for p in ns:
        by_base[p.stem].append(p)
    for p in ns:
        txt = p.read_text(encoding="utf-8")
        for m in re.finditer(r"\[\[([^\]|#]+)", txt):
            raw = m.group(1).strip()
            if "{" in raw or not raw:
                continue
            start = txt.rfind("\n", 0, m.start()) + 1
            if txt[start:m.start()].count("`") % 2:
                continue
            hits = by_base.get(raw.split("/")[-1], [])
            if len(hits) > 1 and "/" not in raw:
                f.append(("ambiguous-link", f"{p.relative_to(vault)}",
                          f"[[{raw}]] matches {len(hits)} files — "
                          + ", ".join(str(h.relative_to(vault)) for h in hits[:3])
                          + ". It resolves to one of them silently. Qualify it with a path."))

    # 11c. restated rules — a duty stated in two places will drift.
    #
    #      `CLAUDE.md` carries the closing duties once, under an anchor. Anything
    #      that RESTATES them rather than pointing at them is a second copy, and
    #      the second copy is what goes stale: the first time someone edits the
    #      one in front of them, the others keep saying the old thing and nothing
    #      notices. That has already cost two days here — `CLAUDE.md` documented
    #      two scripts at a path that had been wrong since 2026-08-17, with a
    #      Notice about it sitting unread the whole time.
    #
    #      Detection is deliberately crude: a file that contains several of the
    #      duty's distinctive phrases without linking to `CLAUDE.md` is
    #      restating. Crude is right — the failure is a copy existing at all, not
    #      how faithfully it was copied. [@owner · 2026-08-19]
    #      Extended 2026-08-20 to the REPORTING format, which had exactly this
    #      failure and was not caught: @owner replaced the four-lists format with a
    #      five-point ceiling, and the superseded wording survived in three
    #      scheduled routines and one project's Reference.md — each a copy made
    #      when that thing adopted the rule. An agent reads the file in front of
    #      it, so those copies would have outlived the replacement indefinitely.
    RULE_SETS = {
        "the closing duties": ("Open Board", "sweep", "outside dependenc"),
        "the reporting format": ("four lists", "what was accomplished",
                                 "what was not accomplished", "five points"),
    }
    watched = list((Path.home() / ".claude" / "scheduled-tasks").glob("*/SKILL.md"))
    watched += list((Path.home() / ".claude" / "skills").glob("mikoshi-*/SKILL.md"))
    watched += list((vault / "02-Projects").glob("*/Reference.md"))
    for w in watched:
        try:
            body = w.read_text(encoding="utf-8")
        except OSError:
            continue
        for rule, markers in RULE_SETS.items():
            hits = sum(1 for m in markers if m.lower() in body.lower())
            if hits >= 2 and "CLAUDE.md" not in body:
                label = w.parent.name if w.name == "SKILL.md" else f"{w.parent.name}/{w.name}"
                f.append(("restated-rule", label,
                          f"restates {rule} ({hits} of {len(markers)} markers) "
                          f"without pointing at CLAUDE.md. A second copy of a rule is the "
                          f"copy that goes stale — replace it with a pointer."))

    # 11d. board drift — the Open Board disagreeing with the Queue.
    #
    #      The board is the page the owner keeps open; the Queue is where handoffs
    #      actually live. Nothing kept them equal, and on 2026-08-20 the board
    #      showed a handoff that had been closed and was missing two that were
    #      open — caught only because he asked, not by any check. A dashboard
    #      that is wrong is worse than no dashboard, because it is trusted.
    board = vault / "05-Orchestrator" / "Open Board.html"
    queue = vault / "05-Orchestrator" / "Queue.md"
    if board.is_file() and queue.is_file():
        try:
            b, q = board.read_text(encoding="utf-8"), queue.read_text(encoding="utf-8")
        except OSError:
            b = q = ""
        if b and q:
            import re as _re
            import os as _os
            import sys as _sys
            _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
            from handoff_status import is_open as _handoff_is_open
            on_board = set(_re.findall(r"<h3>(H-\d+) →", b))
            open_in_queue = set()
            for line in q.splitlines():
                m = _re.match(r"\|\s*(H-\d+)\s*\|", line)
                if not m:
                    continue
                last = line.rstrip().rstrip("|").split(" | ")[-1].strip()
                # One definition, in handoff_status.py. This test used to be a
                # third private copy and had not learned that "Reopened" is
                # open, so it reported a genuinely open handoff as stale while
                # sync_board.py correctly kept it on the board.
                if _handoff_is_open(last):
                    open_in_queue.add(m.group(1))
            missing = sorted(open_in_queue - on_board)
            stale = sorted(on_board - open_in_queue)
            if missing:
                f.append(("board-drift", "Open Board",
                          f"{len(missing)} handoff(s) are OPEN in the Queue and absent from "
                          f"the board: {', '.join(missing)}. The board is what he reads."))
            if stale:
                f.append(("board-drift", "Open Board",
                          f"{len(stale)} handoff(s) are on the board but no longer open in "
                          f"the Queue: {', '.join(stale)}. Completed work never appears here."))

    # 12. ledger drift — a cost a project recorded that the ledger never gained.
    #
    #     The write cannot be automated: no agent has billing access on any
    #     platform, so the ledger is a human-verified statement, not a generated
    #     one. The *catch* can be, and this is it. `record.py cost` appends to a
    #     project's Costs.jsonl the moment money moves; if the matching ledger
    #     row never appears, it shows up here the next morning.
    #
    #     This is the mechanism the Hume row needed. It read "unsubscribed ·
    #     $0.00" for three days because the note that would have fixed it was
    #     addressed to an agent that had been retired — nothing was watching.
    #     [@claude-code/project-four · 2026-08-18]
    ledger = vault / "01-Knowledge Base" / "Infrastructure Ledger.md"
    ledger_txt = ledger.read_text(encoding="utf-8").lower() if ledger.exists() else ""
    for costs in sorted((vault / "02-Projects").glob("*/Costs.jsonl")):
        for line in costs.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            svc = str(row.get("service") or "").strip()
            if svc and svc.lower() not in ledger_txt:
                f.append(("ledger-drift", f"{costs.parent.name}/Costs.jsonl",
                          f"{svc} · {row.get('plan')} · ${row.get('monthly_usd')}/mo "
                          f"recorded {str(row.get('ts'))[:10]}, but '{svc}' does not "
                          f"appear in the Infrastructure Ledger. Add the row, or say "
                          f"why it does not belong."))

    # 'updated:' on the Infrastructure Ledger said 29 July while its own rows
    # said 19 August, and nothing noticed for three weeks. Part 2 is generated
    # now, so staleness is computable: if the registry or any project's costs
    # changed after the block was last rendered, the prose is behind.
    ledger_md = vault / "01-Knowledge Base" / "Infrastructure Ledger.md"
    registry = vault / "05-Orchestrator" / "ledger" / "products.json"
    if ledger_md.exists() and registry.exists():
        txt = ledger_md.read_text(encoding="utf-8")
        m = re.search(r"\*Generated (\d{4}-\d{2}-\d{2}) by", txt)
        if not m:
            f.append(("ledger-drift", "Infrastructure Ledger.md",
                      "Part 2 carries no generated stamp. Run "
                      "`python3 -m ledger.ledger render` from 05-Orchestrator/."))
        else:
            rendered = date.fromisoformat(m.group(1))
            inputs = [registry] + list((vault / "02-Projects").glob("*/Costs.jsonl"))
            newer = [q for q in inputs
                     if date.fromtimestamp(q.stat().st_mtime) > rendered]
            if newer:
                f.append(("ledger-drift", "Infrastructure Ledger.md",
                          f"Part 2 was rendered {rendered} but "
                          f"{', '.join(q.name if q.name != 'Costs.jsonl' else q.parent.name + '/Costs.jsonl' for q in newer[:4])} "
                          f"changed after that. Re-run "
                          f"`python3 -m ledger.ledger render` from 05-Orchestrator/."))

    # A product that draws on a key-attributed pool, has recorded spend against
    # it, and declares no key LABEL is invisible to `ledger.py attributed()` —
    # its money lands in the non-product bucket, the totals still close, and
    # `reconcile` reports every dollar accounted for while never naming it.
    # Found 2026-08-21: project-one had drawn $0.36 through its own key and read
    # as "nothing recorded" for a day. A totals check cannot see a
    # misclassification that conserves the total, so this is checked structurally.
    # Deliberately narrow: only fires when the project HAS recorded spend on
    # that pool, so a product that draws on a pool without its own key yet is
    # not nagged. Never opens keys.md — labels only, from the registry.
    if registry.exists():
        try:
            reg = json.loads(registry.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            reg = None
        if reg:
            by_key = {name for name, meta in (reg.get("pools") or {}).items()
                      if meta.get("attribution") == "api_key"}
            for name, prod in (reg.get("products") or {}).items():
                labels = prod.get("keys") or {}
                costs = vault / "02-Projects" / name / "Costs.jsonl"
                if not costs.exists():
                    continue
                spent = set()
                for line in costs.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    svc = (row.get("service") or "").strip().lower()
                    if svc:
                        spent.add(svc)
                for pool in prod.get("pools") or []:
                    if pool not in by_key or labels.get(pool):
                        continue
                    label = ((reg["pools"][pool].get("label")) or pool).lower()
                    if label in spent:
                        f.append(("ledger-drift", "ledger/products.json",
                                  f"`{name}` records {pool} spend but declares no "
                                  f"`keys.{pool}` label, so `ledger.py attributed()` "
                                  f"cannot see it and reconcile counts it as a "
                                  f"non-product key. Add the label."))

    # ---- prior art: a project with code must say what it was built from ----
    #
    # [@owner · 2026-08-23] The standing rule is *take the existing thing
    # wholesale, read all of it, then improve it*. It stood from 2026-08-19 with
    # nothing able to fail on it, and was followed loosely twice — most plainly
    # on 2026-08-23, when a thread reported on an upstream project's
    # architecture and its gaps having read 4 documents of 41 and none of its
    # 979 source files.
    #
    # **This checks the record, not the reading**, and the difference is stated
    # rather than hidden: nothing here can tell whether someone actually read a
    # codebase. What it can tell is whether they wrote down what they took, how
    # much of it they read, and what they deliberately left — which is the
    # artefact the rule demands and the thing a reviewer can argue with.
    #
    # "Nothing existed, and here is what I searched" is a complete and valid
    # answer. A missing file is not.
    PRIOR_ART_SECTIONS = ("what we took", "how much we read",
                          "what we are not taking", "what we do better")
    for pd in sorted((vault / "02-Projects").iterdir()):
        if not pd.is_dir() or pd.name.startswith("."):
            continue
        if not (pd / "code").is_dir():
            continue
        # **A project may declare itself standalone**, with `prior_art: standalone`
        # in its `_index.md`. That is a statement on the record — the project was
        # built independently — and it is read from the project rather than
        # listed here, so exempting one does not mean editing this file.
        idx = frontmatter(pd / "_index.md") or {}
        if str(idx.get("prior_art", "")).strip().lower() in ("standalone", "none"):
            continue
        pa = pd / "Prior Art.md"
        if not pa.exists():
            f.append(("prior-art-missing", pd.name,
                      "has `code/` and no `Prior Art.md`. The rule is to take an "
                      "existing implementation wholesale, read all of it, and "
                      "improve it — so what it was built from, its licence, how "
                      "much of it was actually read, and what was deliberately "
                      "left behind are part of the record. "
                      "\"Nothing existed, and here is what I searched\" is a "
                      "complete answer; silence is not."))
            continue
        body = pa.read_text(encoding="utf-8", errors="replace").lower()
        missing = [h for h in PRIOR_ART_SECTIONS if h not in body]
        if missing:
            f.append(("prior-art-thin", pd.name,
                      f"`Prior Art.md` does not answer: {', '.join(missing)}. "
                      f"The rejected half is the part a later thread needs — it "
                      f"is what stops someone re-adopting what was already "
                      f"looked at and turned down."))

    return f

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", default=str(Path.home() / "Documents" / "Mikoshi"))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--stamp", action="store_true",
                    help="repair frontmatter dates trailing the newest log entry, then check")
    a = ap.parse_args()
    vault = Path(a.vault)
    if not vault.exists():
        print(f"vault not found: {vault}"); sys.exit(2)

    stamped = stamp(vault) if a.stamp else []
    for proj, field, was, now in stamped:
        print(f"stamped {proj}: {field} {was} -> {now}")

    allf = check(vault)
    f = [x for x in allf if x[0] not in TELEMETRY]
    tel = [x for x in allf if x[0] in TELEMETRY]
    if a.json:
        print(json.dumps([{"kind": k, "where": w, "detail": d,
                           "telemetry": k in TELEMETRY} for k, w, d in allf], indent=2))
        sys.exit(1 if f else 0)

    print(f"vault check — {date.today()} — {vault}")

    def show(rows, kinds):
        for kind in dict.fromkeys(kinds):
            sel = [x for x in rows if x[0] == kind]
            if not sel:
                continue
            print(f"\n{kind}  ({len(sel)})")
            for _, w, d in sel[:12]:
                print(f"  {w}\n      {d}")
            if len(sel) > 12:
                print(f"  … and {len(sel)-12} more")

    if not f:
        print("\n✅ clean")
        if tel:
            print("\n— state, not findings —")
            show(tel, [k for k, _, _ in tel])
        sys.exit(0)

    # `order` sets the reading order for known classes; anything new is appended
    # rather than dropped. This list used to be the whole filter, so a check
    # could find real problems and print nothing — the count said 5, the report
    # showed 3, and the two that mattered were invisible. A checker that hides
    # findings is worse than no checker. [@claude-code · 2026-08-16]
    order = ["stale-claim", "owner-mismatch", "schema-drift", "log-hygiene", "domain-drift",
             "broken-link", "stale-reference", "orphan"]
    order += [k for k, _, _ in f if k not in order]
    show(f, order)
    print(f"\n{len(f)} finding(s)")
    if tel:
        print("\n— state, not findings —")
        show(tel, [k for k, _, _ in tel])
    sys.exit(1)

if __name__ == "__main__":
    main()
