#!/usr/bin/env python3
"""
connect.py — what appears *with* what. Zero LLM calls.

**The gap this closes.** `entities.py` gives every tool, company and technique
its own page recording *where it appears*. Nothing read the other direction:
which entities keep turning up in the same notes, and which pairs do so far more
often than their own frequencies would predict. 171 entity pages existed and
nothing walked the links between them.

**Why co-occurrence counting is not enough on its own.** Count alone ranks the
popular. `Claude Code` appears in almost everything, so it co-occurs with almost
everything, and a list topped by it says nothing you did not already know. The
useful signal is a pair that appears together *more than chance* — two entities
that are each uncommon but keep arriving in the same note.

So each pair gets a lift:

    lift(A,B) = P(A and B) / (P(A) * P(B))

1.0 means exactly what independence predicts. Above 1 means they travel
together. Two entities in 2 notes each, both the same 2 notes, score enormously
— which is right: that is a real association, on thin evidence. `support` (how
many notes back it) is carried alongside so thin evidence is visible rather than
hidden, and never traded away for a bigger number.

**Bridges are the output that matters.** A pair with strong lift where neither
page links the other is a connection the vault holds but has never stated. That
is the same job the reconciliation routine does across projects, done inside the
knowledge base.

    python3 -m funnel.connect              # report, write nothing
    python3 -m funnel.connect --apply      # write "Appears with" onto each page
    python3 -m funnel.connect --bridges 40
"""
import argparse
import itertools
import pathlib
import re
from collections import defaultdict

from . import entities, store

ENTITIES = entities.ENTITIES

# A pair needs this many shared notes before it is reported at all. Below it,
# lift is arithmetic on noise: two entities sharing one note produce a huge
# ratio and mean nothing.
MIN_SUPPORT = 2

# Above this, an entity is treated as background. `Claude Code` in 60% of notes
# is not a connection to anything, it is the subject of the vault.
UBIQUITY = 0.45

# A note mentioning more than this many distinct entities is a CATALOGUE, and
# contributes no co-occurrence evidence at all.
#
# Measured 2026-08-18 over 65 notes: the median note mentions 2 entities and p90
# is 16, but `Tooling Index.md` mentions 160, `Workflows and Best Practices.md`
# 115 and `Tooling Sources (Pilot).md` 52. 61 of 65 notes sit at or below 25, so
# the threshold separates catalogues from prose cleanly rather than splitting a
# continuum.
#
# Without this the whole measure collapses. Nearly every entity appears *only* in
# the two big catalogues, so nearly every pair shares exactly those two notes and
# scores an identical maximum lift: the first run produced 7,528 pairs all tied
# at 32.5, which ranks nothing. Two tools listed in the same 656-entry index were
# never discussed together — they were alphabetised together.
CATALOGUE_ENTITIES = 25

TOP_PER_PAGE = 8
SECTION = "## Appears with"


def cooccurrence(keep, vocab):
    """(pairs, note_count_by_entity, total_notes) from entities.run()'s output."""
    from collections import Counter
    breadth = Counter()
    for hits in keep.values():
        for rel, _n, _d in hits:
            breadth[rel] += 1
    catalogues = {rel for rel, n in breadth.items() if n > CATALOGUE_ENTITIES}

    notes_by = {vocab[k][0]: {rel for rel, _n, _d in hits if rel not in catalogues}
                for k, hits in keep.items()}
    notes_by = {k: v for k, v in notes_by.items() if v}
    universe = set().union(*notes_by.values()) if notes_by else set()
    total = len(universe)

    common = {n for n, s in notes_by.items() if total and len(s) / total >= UBIQUITY}
    ranked = []
    for a, b in itertools.combinations(sorted(notes_by), 2):
        if a in common or b in common:
            continue
        shared = notes_by[a] & notes_by[b]
        if len(shared) < MIN_SUPPORT:
            continue
        pa, pb = len(notes_by[a]) / total, len(notes_by[b]) / total
        lift = (len(shared) / total) / (pa * pb) if pa and pb else 0.0
        ranked.append({"a": a, "b": b, "support": len(shared),
                       "lift": round(lift, 2), "notes": sorted(shared)})
    ranked.sort(key=lambda r: (-r["lift"], -r["support"]))
    return ranked, notes_by, total, common, catalogues


def _links_each_other(a, b):
    """True if either entity page already names the other."""
    for x, y in ((a, b), (b, a)):
        p = ENTITIES / f"{entities.slug(x)}.md"
        if p.exists() and re.search(rf"(?<![\w-]){re.escape(y)}(?![\w-])",
                                    p.read_text(encoding="utf-8"), re.I):
            return True
    return False


def bridges(ranked, limit=40):
    """Strong pairs neither page mentions — the connections nobody stated."""
    out = []
    for r in ranked:
        if len(out) >= limit:
            break
        if not _links_each_other(r["a"], r["b"]):
            out.append(r)
    return out


def write_sections(ranked, apply=False):
    """Put each entity's strongest partners on its own page, replacing any
    previous block. Rewriting in place rather than appending matters: this runs
    repeatedly, and an appending version would grow a page a section per run."""
    by_entity = defaultdict(list)
    for r in ranked:
        by_entity[r["a"]].append((r["b"], r))
        by_entity[r["b"]].append((r["a"], r))

    written = 0
    for name, partners in by_entity.items():
        p = ENTITIES / f"{entities.slug(name)}.md"
        if not p.exists():
            continue
        partners.sort(key=lambda x: (-x[1]["lift"], -x[1]["support"]))
        lines = [SECTION, "",
                 "Entities that arrive in the same notes as this one, more often than "
                 "their own frequencies predict. `lift` is how many times more than "
                 "chance; `support` is how many notes back it. Computed by counting, "
                 "not by a model.", ""]
        for other, r in partners[:TOP_PER_PAGE]:
            # Link only if the target page exists. An entity can be in the
            # co-occurrence set without having a page — `entities.run` requires
            # two notes before it writes one — and names carrying unusual
            # characters (GLM‑5.3 uses a non-breaking hyphen) slug to something
            # no file matches. Emitting the link regardless produced 14 broken
            # links on the first apply. A dead wikilink is worse than plain
            # text: it reads as a promise the vault does not keep.
            target = ENTITIES / f"{entities.slug(other)}.md"
            label = (f"[[01-Knowledge Base/Entities/{entities.slug(other)}|{other}]]"
                     if target.exists() else f"**{other}**")
            lines.append(f"- {label} — lift {r['lift']}, {r['support']} shared notes")
        block = "\n".join(lines) + "\n"

        text = p.read_text(encoding="utf-8")
        if SECTION in text:
            head, _sep, tail = text.partition(SECTION)
            rest = tail.split("\n## ", 1)
            text = head + block + ("\n## " + rest[1] if len(rest) > 1 else "")
        else:
            text = text.rstrip() + "\n\n" + block
        if apply:
            p.write_text(text, encoding="utf-8")
        written += 1
    return written


def run(apply=False, limit=40, progress=None):
    _stats, keep, vocab = entities.run(apply=False, progress=progress)
    ranked, notes_by, total, common, catalogues = cooccurrence(keep, vocab)
    br = bridges(ranked, limit=limit)
    written = write_sections(ranked, apply=apply)
    return {"entities": len(notes_by), "notes": total, "background": sorted(common),
            "catalogues": sorted(catalogues), "pairs": len(ranked), "bridges": len(br),
            "pages_touched": written if apply else 0}, ranked, br


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="write the 'Appears with' section onto each entity page")
    ap.add_argument("--bridges", type=int, default=25,
                    help="how many unstated connections to print")
    a = ap.parse_args()

    stats, ranked, br = run(apply=a.apply, limit=a.bridges,
                            progress=lambda i, n: None)
    print(f"{stats['entities']} entities over {stats['notes']} notes")
    print(f"{stats['pairs']} pairs above support {MIN_SUPPORT}")
    if stats["catalogues"]:
        print("excluded as catalogues: " + ", ".join(
            pathlib.Path(c).name for c in stats["catalogues"]))
    if stats["background"]:
        print(f"treated as background (in ≥{int(UBIQUITY*100)}% of notes): "
              + ", ".join(stats["background"]))
    print(f"\n— top {min(len(ranked), 15)} associations —")
    for r in ranked[:15]:
        print(f"  lift {r['lift']:>7}  support {r['support']:>2}   {r['a']} ↔ {r['b']}")
    print(f"\n— {len(br)} unstated connections (neither page names the other) —")
    for r in br[:a.bridges]:
        print(f"  lift {r['lift']:>7}  support {r['support']:>2}   {r['a']} ↔ {r['b']}")
    if a.apply:
        print(f"\nwrote 'Appears with' onto {stats['pages_touched']} pages")


if __name__ == "__main__":
    main()
