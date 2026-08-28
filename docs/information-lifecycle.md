---
tags: [orchestrator, reference, protocol]
created: 2026-08-10
updated: 2026-08-10
---

# Information Lifecycle

How a thing gets into Mikoshi, how it earns its place, and how it leaves.

**The objective is not volume. It is signal.** A knowledge base that grows every day and is never pruned becomes an archive nobody reads. Every stage below exists to keep the corpus small enough to stay useful.

---

## Stage 1 — Reach

Getting to content that lives behind a platform. **All access uses the owner's own logged-in session, read at call time. No agent-owned accounts, anywhere.**

| Source | Method | Notes |
|---|---|---|
| Instagram | `yt-dlp` + `gallery-dl`, Chrome cookies | Rate-limits hard. Needs backoff and a retry queue. |
| YouTube | Per-channel RSS → `yt-dlp` | No auth needed. The easiest source by far. |
| X | RSSHub self-hosted, `TWITTER_AUTH_TOKEN` from the owner's web session | Removes the public-instance rate limit |
| Websites / blogs | RSS where it exists, else fetch + readability | |
| Newsletters | AgentMail inbox, subscribed directly | Designed to be received. No scraping, no ToS grey area. |

**Design rule:** prefer the path the platform intends. Newsletters and RSS are cooperative; scraping is adversarial and breaks. Weight the source registry toward cooperative paths.

---

## Stage 2 — Read

Content arrives in incompatible forms. Each needs its own reader; all converge on the same output.

| Form | How it's read |
|---|---|
| Video with captions | Caption track directly |
| Video without captions | `mlx-whisper` locally |
| Screen recording | Keyframes at 1/1.5s, scene-detection first, uniform sample as fallback |
| Carousel | Every slide, then contact-sheeted so it reads in one pass |
| Article | Fetch + readability extraction |
| Newsletter | Email body, HTML stripped |
| Caption / thread text | Direct |

**Everything converges to:** text + images + metadata. Downstream stages never care what the source was.

---

## The priority that outranks every optimisation

**Practical AI implementation is the point. Everything else is context.** [@owner · 2026-08-15]

Three domains, deliberately unequal:

1. **Tooling and implementation — PRIMARY.** New tools, MCPs, repos, connectors, skills, stacks, workflows, and named concepts as they emerge (`graph engineering` is the type case — it went from a tweet to a discipline in three weeks). *How people are actually building*, at the level of "do 1, 2, 3, 4, 5".
2. **Startup ecosystem and markets** — YC batches, funding, who is building what. Context for the first.
3. **Business and financial reaction** — how the market responds to both. Context for the first two.

This is not a preference. `Owner Profile.md` names the driver: *"a real fear of technological obsolescence / FOMO on AI and tech developments — the direct motivation behind wanting a fully autonomous AI & Tech Tracking loop."* Missing a funding round costs nothing. Missing a technique everyone adopts for six months is the failure this whole system exists to prevent.

**Three consequences for the design, all of which override the general rules below:**

- **Instagram and YouTube are first-class, not a fallback.** Practical implementation content lives in short-form video and tutorials before it reaches any blog. Most of the Recall corpus is Instagram for exactly this reason. Do not demote a source because it is expensive to extract — demote it because it carries little.
- **Redundancy means the opposite thing here.** For news, five sources carrying one story is duplication to collapse. For tooling, **five people posting the same MCP in a week is an adoption signal** and the strongest buy-indication available. Never dedupe tooling down to one mention — record the count and who, because the count is the finding.
- **Bias recall over precision on this domain only.** A missed tool is unrecoverable; a slightly noisy tooling section is a two-second skim. Everywhere else, prefer precision.

---

## Stage 3 — Classify

Every item gets a category and a confidence score. **Category determines whether it is kept at all, and how long it lives.**

| Category | Kept? | Half-life | Why |
|---|---|---|---|
| **Tooling** | ✅ | 6 months | Tools change. A tool entry more than two releases old is usually wrong. |
| **Workflow** | ✅ | 12 months | Patterns outlive the tools that implement them |
| **Technical concept** | ✅ | 36 months | Loop engineering, RAG, embeddings — durable |
| **Industry shift** | ✅ | 12 months | Structural change, not the daily churn |
| **News** | ❌ | 7 days | Discarded by default. Surfaces in the digest, never files. |

**News is not stored.** It appears in the daily digest and expires. If the owner wants a specific item kept, he says so and it re-files under a durable category.

Below the confidence threshold, an item escalates to the approval queue rather than being guessed at.

---

## Stage 4 — Filter

Four gates, in order. Cheapest first.

1. **Duplicate** — is this already in the corpus? Exact and near-match. Drop, or add a citation to the existing entry.
2. **Novelty** — does this say anything the corpus does not already say? A fifth video describing the same pattern adds a citation, not an entry.
3. **Relevance** — does this connect to anything the owner is doing or has said he cares about? Checked against `Ingestion Preferences` and active project state.
4. **Confidence** — is the extraction sound? Partial reads and ambiguous claims escalate rather than file.

**Most items should die here. That is the gate working, not failing.**

---

## Stage 5 — Distil and link

- Distil to the smallest form that survives without the source: what it is, why it matters, where it came from.
- **Embed the distilled entry and find its nearest existing notes. Those become its links, written at file time.**
- This is the fix for the orphan ring — roughly 800 unlinked notes in the current graph. Nothing new enters unlinked.
- Source is recorded inline, with a URL and a retrieval date, so any claim traces back.

**Links are typed, not bare.** A bare link says only "related", which is not enough to traverse usefully. Every link carries a relationship:

`implements` · `supersedes` · `depends_on` · `alternative_to` · `used_by` · `built_with` · `contradicts`

An agent can then answer *"what depends on the tool that just went obsolete"* rather than *"what is vaguely near this"*. Written as `[[note|type:implements]]` or as a `links:` block in frontmatter — whichever the linker settles on, it must be machine-readable.

**Extract entities as first-class records.** Tools, people, companies, models and projects get their own note the first time they are seen, not just a mention inside someone else's. A mention updates the entity's record; the record accumulates every mention. This is what makes `supersedes` work at Stage 7 — you cannot retire "the old version of X" unless X is a thing the vault knows about.

---

## Stage 6 — File

Destination by category:

- Tooling → `01-Knowledge Base/Tooling Sources/<category>.md`
- Workflow → `01-Knowledge Base/Workflows and Best Practices.md`
- Concept → `01-Knowledge Base/` as an atomic note
- Project-specific → straight into that project

Frontmatter carries: `category`, `filed`, `half_life`, `source_url`, `confidence`, `supersedes`, `entities`, `links`.

**Retrieval is hybrid** — three passes over the same corpus, merged:

1. **Keyword** — grep. Exact, cheap, never silently drops a match.
2. **Vector** — local embeddings via `nomic-embed-text`, stored in **sqlite-vec**. Finds meaning where words differ.
3. **Graph** — walk the typed links out from whatever the first two found.

**Why SQLite and not Postgres + pgvector.** At 573 vectors and one user, a database server is a daemon to babysit for no gain — sqlite-vec is a single file, no server, and keeps the promise that Mikoshi needs a text editor rather than a database. Revisit only if the corpus passes ~100k vectors.

---

## Stage 7 — Age out

Two triggers.

**Time.** An entry past its half-life is flagged for review, not deleted. Review asks one question: *is this still true?* Yes → refresh the date. No → dispose.

**Supersession — the more important one.** A new entry that describes the same subject at a newer version marks the old one superseded automatically. A Sonnet 5 entry supersedes the Sonnet 4 entry without waiting for a clock.

### Disposal — two modes

| Mode | When | What remains |
|---|---|---|
| **Tombstone** *(default)* | The thing existed and mattered | One line: what it was, when it was current, what replaced it. Bulk deleted. |
| **Delete** | Never mattered, or is actively misleading | Nothing. Logged in `Live Status` so the removal is auditable. |

**Tombstone is the default** because "this tool existed and was replaced by X" is itself useful, and costs one line instead of forty.

---

## The learning loop

- The daily digest lists what was filed and what was dropped.
- the owner marks items signal or noise.
- Those verdicts append to `Ingestion Preferences`, with reasons.
- The classifier reads that file on every run.
- **As the preference file thickens, the confidence threshold for auto-filing drops.**
- The end state is explicit: the owner stops approving individual items and approves *categories and sources*. He is not removed from the loop — he moves up a level in it.

---

## Cadence

Daily to start. One config value, not a code change. Reviewed once there's enough signal to judge whether daily is right.
