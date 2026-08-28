# The funnel

`fetch → extract → classify → cluster → score → distil → link → file`

The backend loop from `05-Orchestrator/_index.md`, built against the registry in
`Sources.md`. Local only: Ollama for embeddings and judgment, SQLite for state.
Nothing leaves the machine and there is no server to babysit.

```bash
V=02-Projects/delta/code/tools/.venv/bin/python   # from the vault root
cd 05-Orchestrator

$V -m funnel.run                    # daily run — stages entries, writes a digest
$V -m funnel.run --dry-run          # fetch/classify/score, write nothing
$V -m funnel.run --limit 40         # cap the LLM stages; the rest defers to tomorrow
$V -m funnel.run --since 7          # widen the freshness window
$V -m funnel.run --no-promote       # stage only, do not touch 01-Knowledge Base

$V -m funnel.embed_corpus           # re-index the vault on its own
$V -m funnel.registry               # what the registry currently holds
$V link_notes.py                    # propose links for existing orphans
```

## What each stage does

| Stage | File | Note |
|---|---|---|
| fetch | `fetch.py` | Conditional GET, ETag/Last-Modified cached per feed. RSS and Atom, one path. |
| extract | `fetch.py` | Everything becomes text + metadata. Downstream never learns what the source was. |
| classify | `classify.py` | Category + confidence + entities. Deterministic signals first, model second. |
| cluster | `cluster.py` | Greedy cosine grouping. A cluster means opposite things per category — see below. |
| score | `score.py` | One number, decomposed into named terms that get printed. |
| distil | `distil.py` | The entry shape already used in `Tooling Sources/`. |
| link | `link.py` | Typed links to the nearest existing notes. Can block a file. |
| file | `filer.py` | Staged entries + the daily digest. |

## Five decisions worth not re-litigating

**The registry is `Sources.md`, not a config file.** A second copy would drift the
first time the owner added a feed. The funnel parses the markdown tables he maintains.

**A cluster means two opposite things.** For news, five outlets on one story is
duplication — collapse it, keep one, cite the rest. For tooling, five people
posting the same MCP in a week is an adoption signal and the count *is* the
finding, so members and sources are kept and the score goes *up*. This is the
priority in `Information Lifecycle.md` implemented, not just described.

**The recall bias is code.** A strong deterministic tooling signal — a GitHub
repo, an install line, the letters MCP — overrides a model verdict of `news`.
A missed tool is unrecoverable; a noisy tooling section costs a two-second skim.
Every override is recorded on the item, never silent.

**Nothing files without a link.** An entry that matches nothing in the vault is
flagged `novel: true` rather than written as an orphan. That flag is the signal
that it is either genuinely new or does not belong.

**The funnel owns `01-Knowledge Base/`.** [@owner · 2026-08-16] It writes finished
entries to `05-Orchestrator/staged/<date>/` and then promotes them, in the same
run. Staging is a checkpoint you can inspect, not a boundary that needs a human.
`--no-promote` stops at staging. Every category has a destination:

| Category | Lands in |
|---|---|
| tooling | `Tooling Sources/<category>.md` as a `#### block` — dedup by heading, citation on a match, alphabetical insert otherwise |
| workflow · concept | `01-Knowledge Base/Ingested/<slug>.md` as an atomic note, links already attached |
| industry · news | never promoted — they live and die in the digest |

The previous staging-only design existed because the write-scope table gave that
folder to `@cowork`, which belonged to the Recall era and is retired.

**The vault is not under version control**, so `promote.py` snapshots
`Tooling Sources/` into `state/kb-backups/<date>-<time>/` before its first write
of a run. That is the undo.

## State

`state/mikoshi.db` — every item ever seen (so a second run is cheap), per-feed
cache headers, and two sqlite-vec indexes: vault notes, and distilled entries.
Delete it and the next run rebuilds everything except which items were already
processed.

The index self-invalidates when the embedding model or its task prefixes change
(`store.EMBED_VERSION`). Vectors are only comparable to vectors made the same
way, and the failure mode is silent nonsense rather than an error.

## Stages 8-10, added 2026-08-18

| Stage | Command | What it does |
|---|---|---|
| newsletters | `funnel.newsletters park` | Tier 5. The routine reads the inbox over MCP and drops JSON; this parks it. **No API key exists anywhere in this code**, which is why it cannot leak one. |
| connect | `funnel.connect --apply` | What appears *with* what. Lift over co-occurrence, written onto entity pages. Zero model calls. |
| cleanup | `funnel.cleanup` | Duplicates, contradictions, salience — the first pass that reads what notes *say* rather than how they are shaped. |

## Selection — recalibrated 2026-08-18

The old rule sorted by score and took the top 40. **The score has almost no
resolution**: it is a product of terms that are nearly all constant on a fresh
item, so 80% of 510 items shared a score with another and 22 sat on exactly
0.96. Python's sort is stable, so among tied items the places went to whatever
was fetched first — **fetch order chose the Knowledge Base.**

`score.select_for_filing` replaces it with a relative keep-fraction per category
(volume-invariant by construction, which is what adapting to volume actually
requires) and then round-robins across sources. arXiv is 53% of the corpus by
volume; without the source cap it is 53% of the Knowledge Base. Replayed over
the real 510 items: 15 sources represented instead of one dominating.

## Reliability

`fetch.py` retries transient failures (timeout, connection error, 5xx) up to
three attempts with doubling backoff, and leaves definite answers alone — a 403
is not worth retrying, and `blocked` means the feed is alive and refusing us,
which is a configuration problem to surface rather than a dead feed. After **5
consecutive failing runs** a feed is quarantined for a week: skipped without a
network call, and reported as quarantined rather than as a fresh failure. Any
success clears the streak. A feed dead for a fortnight otherwise costs a timeout
every run and buries the one that broke today.

## Known gaps

- **Anthropic has no RSS feed.** `Sources.md` lists a page, and the fetcher
  correctly reports `not-a-feed`. RSSHub's `/anthropic/news` route closes it.
- **arXiv cs.AI is empty at weekends by design** (`<skipDays>`), not broken.
- **Contradiction detection only checks claims of a decidable shape** — a price,
  a version, a percentage, a port — and deliberately does not ask a model
  whether two paragraphs conflict, because that produces confident nonsense at a
  rate nobody can audit. Prose disagreement is still unhandled.
- **Duplicate pairs are reported, never merged.** A merge is lossy and
  irreversible, so it stops at the owner on rule 7.
