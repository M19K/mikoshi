#!/usr/bin/env python3
"""
cleanup.py — Stage 8. The pass that reads what the vault *says*, not how it is shaped.

**The gap this closes.** Two routines run every morning and both check structure:
`vault_check.py` verifies links, schema, ownership and sequencing; `age.py`
flags entries past their half-life and retires superseded ones. Neither ever
opens a note and asks whether its *content* is redundant, contradicted, or worth
keeping. So the knowledge base could hold the same tool written up three times,
under three names, one of them wrong, and every check would report clean.

Three passes, cheapest first. Every one is deterministic or measured; none
claims a verdict it cannot show its working for.

  **Duplicates.**  Near-identical notes, by cosine similarity over embeddings
                   computed here rather than read from the vault index — every
                   note is stripped of its template first, so the comparison is
                   of what two notes say and not of the form they share. The
                   index holds notes whole, so it cannot answer this question.
                   Reported as candidates, never merged automatically — a merge
                   is lossy and irreversible, which puts it behind a human on
                   rule 7.
  **Contradictions.**  Two notes about the same entity that disagree on a fact
                   that can be checked mechanically: a number, a price, a
                   version, a yes/no. This deliberately does NOT ask a model
                   whether two paragraphs "conflict" — that produces confident
                   nonsense at a rate nobody can audit. It finds claims of the
                   same *shape* about the same *subject* with different values,
                   and shows both so a human decides in two seconds.
  **Salience.**    What is actually load-bearing, so pruning has a basis other
                   than age. Inbound links, entity mentions, and recency of last
                   edit, combined into one number whose terms are printed.

    python3 -m funnel.cleanup                 # report everything, write nothing
    python3 -m funnel.cleanup --duplicates    # just one pass
    python3 -m funnel.cleanup --apply         # write salience into frontmatter
"""
import argparse
import datetime as dt
import math
import pathlib
import re
from collections import defaultdict

from . import store

VAULT = store.VAULT
KB = VAULT / "01-Knowledge Base"

# Cosine similarity above which two notes are candidate duplicates. Set from the
# observed distribution rather than picked: unrelated vault notes sit around
# 0.3-0.6, genuinely related ones 0.7-0.85, and near-copies above 0.92.
DUPLICATE_AT = 0.92

# Frontmatter keys and prose patterns whose values are comparable across notes.
# Each is a claim shaped so that "different" is decidable without judgment.
# Each pattern must be a claim whose subject is unambiguous from the text
# itself, because the entity is established separately by proximity.
#
# `version` was originally `\bv?(\d+\.\d+)\b` and matched EVERY decimal number
# in the vault — prices, percentages, dates, "$3.4", "0.19", "29.4.0" — which
# reported Anthropic as self-contradicting about its version 26 times. A version
# claim now has to be marked as one.
CLAIM_PATTERNS = {
    "price": re.compile(r"\$\s?(\d+(?:\.\d{1,2})?)\s*(?:/|per\s+)\s*(?:mo|month)\b", re.I),
    "version": re.compile(r"\b(?:version|v)\s?(\d+\.\d+(?:\.\d+)?)\b", re.I),
    "percent": re.compile(r"\b(\d{1,3}(?:\.\d+)?)\s?%"),
    "port": re.compile(r"\bport\s+(\d{2,5})\b", re.I),
}

SKIP_DIRS = {".obsidian", ".git", ".claude", "code", "03-Archive", "state",
             "staged", "digests", "node_modules"}

# A claim only counts as being *about* an entity if it sits this close to a
# mention of it. Measured against the false positives the unwindowed version
# produced — see the note in claims().
WINDOW = 320

# Generated pages are excluded from prune candidates. `Entities/` is rebuilt from
# the vocabulary on every run, so a low score there means "nothing links to a
# generated index page", which is true, expected, and not a finding. Flagging 171
# of them buries the handful of real ones.
#
# The rest of this tuple is the same class of answer, and `CLAUDE.md` already
# names it under "Lint by hand": root meta files and `00-Inbox/` waypoints are
# on the vault's own do-not-flag list, and this checker was only implementing
# part of that list. `AGENTS.md`, `GEMINI.md` and `README.md` are pointers to
# `CLAUDE.md` that "deliberately restate nothing", so zero inbound links is
# their design rather than a defect; `00-Inbox/` is the owner's own capture queue and
# nothing is supposed to link into it. All five were reported on 2026-08-21,
# five of the fifteen prune candidates spent on files that can never be pruned.
GENERATED = ("01-Knowledge Base/Entities/", "00-Inbox/",
             "AGENTS.md", "GEMINI.md", "README.md")


def notes():
    for p in sorted(VAULT.rglob("*.md")):
        if not any(x in SKIP_DIRS for x in p.parts):
            yield p


def _rel(p):
    return str(p.relative_to(VAULT))


# --- 1 · duplicates --------------------------------------------------------

TOMBSTONE = re.compile(r"^(?:type:\s*tombstone|status:\s*retired)\s*$|"
                       r"^tags:.*\btombstone\b", re.M)


def _is_tombstone(text: str) -> bool:
    """A retirement stub, which is boilerplate by design.

    Tombstones are the vault's answer to a deleted note: same wording every
    time, only the name and destination differ. Fourteen of the sixteen pairs
    this check reported on 2026-08-19 were tombstone-against-tombstone — seven
    stubs in `01-Knowledge Base/AI Stack/`, all above 0.94 because they are
    *supposed* to read alike. Comparing them measures the template, not the
    content, so they are excluded rather than reported every run forever.
    """
    return bool(TOMBSTONE.search(text[:400]))


# Scaffolding every filed note carries: frontmatter, the adoption-signal line,
# the source line, the score breakdown, and the trailing "## Related" block.
BOILERPLATE = (
    re.compile(r"\A---\n.*?\n---\n", re.S),                    # frontmatter
    re.compile(r"^\*\*Adoption signal.*$", re.M),
    re.compile(r"^\*\*Source\.\*\*.*$", re.M),
    re.compile(r"^\*\*Score [\d.]+\*\*.*$", re.M),
    re.compile(r"\n## Related\n.*\Z", re.S),
)


def _content(text: str) -> str:
    """A note's own words, with the template it shares with every other note removed.

    Filed notes are short — three or four sentences — and carry five lines of
    identical scaffolding. That scaffolding was a large enough fraction of the
    embedded text to push unrelated notes over the threshold: on 2026-08-19
    `ai-agent-value-framework` (where to deploy agents) and
    `human-ai-team-evaluation` (how to score human-AI collaboration) measured
    0.93 against each other on nothing but shared furniture. Compare what the
    note says, not the form it says it in.
    """
    for pat in BOILERPLATE:
        text = pat.sub("", text)
    return text.strip()


# QA runs live at `<project>/QA/runs/<date>[-n]/`. They are immutable dated
# evidence of what a run saw, not notes that can be deduplicated: two runs of the
# same suite on the same day are SUPPOSED to read alike, and the report is
# generated by `harvest.py` from a fixed template. On 2026-08-21 the only pair
# this check reported was `2026-08-19-3/report.md` against `2026-08-19/report.md`
# at 0.966 — two runs of one suite, hours apart. Merging them would destroy the
# record the QA protocol exists to keep, so the finding has no available action.
EVIDENCE = re.compile(r"/QA/runs/", re.I)


def _is_evidence(path: str) -> bool:
    return bool(EVIDENCE.search("/" + path.replace("\\", "/")))


def duplicates(db, threshold=DUPLICATE_AT):
    """Near-identical notes, compared on their own words.

    Both sides are embedded here rather than read from the vault index. The
    index holds each note whole — frontmatter, scoring line, link block — and
    comparing a stripped query against whole-note vectors measures two
    different things and reports the difference as a finding.
    """
    rows = list(db.execute("SELECT path, title FROM notes"))
    if not rows:
        return []

    vecs = {}
    for path, _title in rows:
        f = VAULT / path
        if not f.exists():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if _is_tombstone(text) or _is_evidence(path):
            continue
        body = _content(text)[:4000]
        if len(body) < 200:
            continue
        try:
            v = store.embed(body)
        except Exception:
            continue
        if v is not None:
            vecs[path] = v

    paths = sorted(vecs)
    found = []
    for i, a in enumerate(paths):
        va = vecs[a]
        na = math.sqrt(sum(x * x for x in va)) or 1.0
        for b in paths[i + 1:]:
            vb = vecs[b]
            nb = math.sqrt(sum(x * x for x in vb)) or 1.0
            similarity = sum(x * y for x, y in zip(va, vb)) / (na * nb)
            if similarity >= threshold:
                found.append({"a": a, "b": b, "similarity": round(similarity, 3)})
    found.sort(key=lambda r: -r["similarity"])
    return found


# --- 2 · contradictions ----------------------------------------------------

def contradictions(entity_notes=None):
    """Same subject, same claim shape, different value.

    Scoped to notes that share an entity, because two unrelated notes both
    containing "$20" are not in disagreement — they are both just using money.
    """
    from . import entities
    if entity_notes is None:
        _stats, keep, vocab = entities.run(apply=False)
        entity_notes = {vocab[k][0]: [rel for rel, _n, _d in hits]
                        for k, hits in keep.items()}

    cache = {}

    def claims(rel, entity):
        """Claims made *near* the entity, not merely present in the same file.

        The first version of this scanned whole files and was useless: it read
        `Tooling Index.md` and `Queue.md`, found "$40" in one and "$3" in the
        other, and reported that OpenRouter, Anthropic, Vercel, Recall, Instagram
        and CLAUDE.md were each contradicted about price — ten findings, zero
        real. A number in the same 30KB document as a name is not a claim about
        that name. So the window: only text within WINDOW characters of a mention
        counts, which is the difference between "this file mentions both" and
        "this sentence says so".
        """
        key = (rel, entity)
        if key in cache:
            return cache[key]
        p = VAULT / rel
        out = defaultdict(set)
        if p.exists():
            text = p.read_text(encoding="utf-8", errors="ignore")
            spans = [m.start() for m in
                     re.finditer(rf"(?<![\w-]){re.escape(entity)}(?![\w-])", text, re.I)]
            for s in spans:
                # Same sentence, not a fixed window. A 320-character window still
                # spanned whole paragraphs in this vault's dense prose and kept
                # attributing one line's number to the previous line's subject.
                lo = max((text.rfind(c, max(0, s - WINDOW), s) for c in ".\n!?"),
                         default=-1)
                hi = min((h for h in (text.find(c, s, s + WINDOW) for c in ".\n!?")
                          if h != -1), default=s + WINDOW)
                near = text[lo + 1 if lo != -1 else max(0, s - WINDOW): hi]
                for kind, rx in CLAIM_PATTERNS.items():
                    for m in rx.finditer(near):
                        out[kind].add(m.group(1))
        cache[key] = out
        return out

    found = []
    for ent, rels in entity_notes.items():
        rels = [r for r in rels if (VAULT / r).exists()]
        if len(rels) < 2:
            continue
        for i, a in enumerate(rels):
            for b in rels[i + 1:]:
                ca, cb = claims(a, ent), claims(b, ent)
                for kind in CLAIM_PATTERNS:
                    va, vb = ca.get(kind, set()), cb.get(kind, set())
                    # Both must state exactly one value of this kind. A note
                    # listing five versions is a changelog, not a claim.
                    if len(va) == 1 and len(vb) == 1 and va != vb:
                        found.append({"entity": ent, "kind": kind,
                                      "a": a, "a_value": next(iter(va)),
                                      "b": b, "b_value": next(iter(vb))})
    # one row per (entity, kind, pair)
    uniq, out = set(), []
    for f in found:
        key = (f["entity"], f["kind"], f["a"], f["b"])
        if key not in uniq:
            uniq.add(key)
            out.append(f)
    return out


# --- 3 · salience ----------------------------------------------------------

def salience():
    """What is load-bearing. Terms are printed, so the number can be argued with."""
    texts = {}
    for p in notes():
        texts[_rel(p)] = p.read_text(encoding="utf-8", errors="ignore")

    inbound = defaultdict(int)
    stems = {r[:-3] if r.endswith(".md") else r: r for r in texts}
    for rel, t in texts.items():
        for m in re.finditer(r"\[\[([^\]|#]+)", t):
            target = m.group(1).strip()
            if target in stems and stems[target] != rel:
                inbound[stems[target]] += 1

    today = dt.date.today()
    out = []
    for rel, t in texts.items():
        p = VAULT / rel
        try:
            age_days = (today - dt.date.fromtimestamp(p.stat().st_mtime)).days
        except OSError:
            age_days = 999
        terms = {
            "inbound": min(1.0 + 0.15 * inbound[rel], 2.5),
            "size": 1.0 if len(t) > 800 else 0.7,
            "fresh": 1.0 if age_days <= 30 else 0.8 if age_days <= 90 else 0.6,
        }
        total = 1.0
        for v in terms.values():
            total *= v
        out.append({"path": rel, "salience": round(total, 3),
                    "inbound": inbound[rel], "age_days": age_days, "terms": terms})
    # QA evidence is excluded here for the same reason it is excluded from the
    # duplicate check: a run report is an immutable record, so "nothing links to
    # it" is its normal condition and pruning it is never the answer. Two of the
    # fifteen slots went to run reports on 2026-08-21.
    out = [r for r in out
           if not r["path"].startswith(GENERATED) and not _is_evidence(r["path"])]
    out.sort(key=lambda r: r["salience"])
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--duplicates", action="store_true")
    ap.add_argument("--contradictions", action="store_true")
    ap.add_argument("--salience", action="store_true")
    ap.add_argument("--apply", action="store_true",
                    help="write salience into each note's frontmatter")
    a = ap.parse_args()
    every = not (a.duplicates or a.contradictions or a.salience)

    if a.duplicates or every:
        db = store.connect()
        d = duplicates(db)
        print(f"\n— duplicates (cosine ≥ {DUPLICATE_AT}) — {len(d)} candidate pairs")
        for r in d[:15]:
            print(f"  {r['similarity']}  {r['a']}\n         ↔ {r['b']}")
        if not d:
            print("  none. Nothing in the vault is a near-copy of anything else.")

    if a.contradictions or every:
        c = contradictions()
        print(f"\n— contradictions — {len(c)} claims of the same shape that disagree")
        for r in c[:15]:
            print(f"  [{r['kind']}] {r['entity']}: {r['a_value']} vs {r['b_value']}")
            print(f"        {r['a']}\n        {r['b']}")
        if not c:
            print("  none found. This checks numbers, versions, prices and ports —")
            print("  not whether two paragraphs disagree in prose.")

    if a.salience or every:
        s = salience()
        print(f"\n— salience — {len(s)} notes, lowest 15 (prune candidates)")
        for r in s[:15]:
            print(f"  {r['salience']:>6}  in:{r['inbound']:<3} {r['age_days']:>4}d  {r['path']}")
        if a.apply:
            n = 0
            for r in s:
                p = VAULT / r["path"]
                t = p.read_text(encoding="utf-8")
                if not t.startswith("---"):
                    continue
                end = t.find("\n---", 3)
                if end < 0:
                    continue
                fm = t[:end]
                fm = re.sub(r"\nsalience: [0-9.]+", "", fm)
                p.write_text(fm + f"\nsalience: {r['salience']}" + t[end:],
                             encoding="utf-8")
                n += 1
            print(f"\nwrote salience into {n} notes")


if __name__ == "__main__":
    main()
