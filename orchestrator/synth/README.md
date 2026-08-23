---
tags: [orchestrator, synthesis, retrieval, reference]
created: 2026-08-21
---

# synth — the answer layer

`recall` hands back passages and you read them. **`synth` writes the answer, and
pins every claim to a line you can open.** It runs entirely on a local model.

```bash
python3 -m synth.answer "why is there one API key per product"
python3 -m synth.answer "what is blocking this" --scope project-one
python3 -m synth.decisions "macos keychain"     # what was this option's fate?
python3 -m synth.evals                          # is it still trustworthy?
```

Also live over MCP as **`answer`** and **`why_not`**, so any agent in any thread
can use it without shelling out.

## Four things it does that a page-citing answer layer cannot

Measured 2026-08-21 against the ordinary shape of these systems: an answer
that cites a page, over a corpus with no authorship inside it.

| | Citing a page | Here |
|---|---|---|
| **Citation grain** | a page (`[slug]`) or a take (`[slug#3]`) | **a file and a line number** — a wrong claim is caught by looking, not by believing |
| **Authority** | none — pages have no author within them | every cited line says whether **the owner decided it** or an agent wrote it, and the answer reports the mix |
| **"Why not X?"** | structurally impossible — a rejected option leaves no page | **165 recorded decisions, all carrying their rejected alternatives**, searched rejection-first |
| **Keys** | `think` needs `ANTHROPIC_API_KEY`; degrades to gather-only without it | no key exists in this path |

The first three come from metadata the vault already carried. **None of them is
a property of the model**, which is why a small local model does not give them
up — and why building this as generic RAG over note text would have produced a
worse `think` instead of a different one.

## The guard that matters most

Every `[E3]` the model emits is checked against the evidence actually retrieved.
A marker that does not resolve is **stripped and counted**, never rendered.
The usual fallback is to parse markers out of prose and render whatever is
found — reasonable on a frontier model, dangerous on a local one, because
small models invent markers more often.

## Measured, 2026-08-21

`python3 -m synth.evals` — the set grew from 12 questions to **30** during the
day, so the last column is the one to quote.

| | 12 q · source-level | 12 q · + claim check | 12 q · + strict | **30 q · full** |
|---|---|---|---|---|
| **grounded** — cited the file holding the answer | 9/10 | 6/10 | 7/10 | **18/27 (67%)** |
| **clean** — no invented citations | 12/12 | 12/12 | 12/12 | **30/30** |
| **honest** — flagged the unanswerable ones | 2/2 | 2/2 | 2/2 | **3/3 — see below** |
| **backed** — sentences a cited line supports | — | 37% | 61% | **35/49 (71%)** |
| seconds per question | ~13 | ~30 | ~30 | **~88** |

### Routed vs local, same questions, same day

**Measured 2026-08-22 on set `bf02b3bcba`, @owner approving the spend. It cost $0.062.**

| | local `gpt-oss:20b` | routed `qwen3.7-flash` |
|---|---|---|
| grounded — cited the file holding the answer | 18/27 (67%) | **23/27 (85%)** |
| clean — no invented citations | 30/30 | 29/29 |
| backed — sentences the cited line supports | 71% | **87%** |
| honest — flagged the unanswerable | 3/3 | **2/3** |
| time for the set | ~45 min | **1 min** |
| cost | $0 | $0.0088 |

**Then measured again with the model thinking, 2026-08-22 — same model, same
set, one variable**, after project-four found that switching reasoning off cost
catch rate materially on *their* task:

| | thinking off | thinking on |
|---|---|---|
| grounded | 23/27 | 22/27 |
| clean | 29/29 | 29/29 |
| backed | 87% | 82% |
| honest | **2/3** | **3/3** |
| time for the set | 1 min | 7.8 min |

**Read this carefully, because the obvious reading is wrong.** The grounded and
backed gaps are one case and five points — both inside the ±2-point run-to-run
noise floor — so thinking did **not** make the answers more accurate here, and
quoting those as differences would be quoting noise. What it changed is the
single case that matters: **with thinking off the layer answered a question the
vault does not hold; with it on, it declined.** That is worth 8× the wall time
only because not inventing is the entire claim. **Opposite shape to project-four's
result on their judging task — so the effect of reasoning is per-task, and a
global setting for it is a guess wearing a measurement's clothes.**

**The one regression is the one that matters most.** Routed, the layer answered
a question the vault does not hold instead of declining — an `all_unmarked`
outcome, meaning it asserted something and cited nothing, and strict mode
deleted the lot. Better at finding and supporting an answer; worse at refusing
to give one. For a system whose entire claim is that it does not invent, that
trade is not automatically worth taking, and it is why local remains the
default for everyday use.

**A false result on the way, worth keeping.** The first routed run scored 3/30
and looked like collapse. It was the plumbing: reasoning tokens are spent out of
`max_tokens`, so on a 3,244-token prompt the model thought its whole budget away
and returned `content: null`. `reasoning: {"enabled": false}` fixes it, returns
zero reasoning tokens, and is the cheapest of the options —
**which corrects this vault's own note that reasoning cannot be disabled over an
OpenAI-compatible endpoint.** True of Ollama's `/v1`; not true through OpenRouter.

### What each memory store is actually worth — first ablation, 2026-08-23

The same 30 questions, five times, removing one store each run. Until now every
number here measured all three together, so "85% grounded" was true and said
nothing about which store earned it.

| configuration | grounded | backed | honest |
|---|---|---|---|
| all-on | 23/27 | 94% | 2/3 |
| no-evidence | **9/27** | 94% | 2/3 |
| no-decisions | 18/27 | 81% | 3/3 |
| no-learnings | **24/27** | 86% | 2/3 |
| no-memory | 0/27 | — | 3/3 |

**The markdown corpus is the backbone** — remove it and grounding falls 85% → 33%.
No surprise, but it had never been shown.

**`Decisions.jsonl` earns its place twice over:** removing it costs 18 points of
grounding and 14 of claim support. The store that was invisible to retrieval
until two days ago is the second most valuable thing in the layer.

**`Learnings.jsonl` is the marginal one, and not in the direction expected.**
Removing it made grounding *better* by one case and claim support worse by 9
points. So it does not help find the right file — it helps support sentences
once the file is found. Keep it, but that is now a measured statement rather
than an assumption, and it is the first candidate if the prompt ever needs
trimming.

**`no-memory` scoring 0/27 is the harness proving itself.** With every store
removed the layer cites nothing at all, which is what it should do — an
ablation whose floor still scores is measuring something other than what it
claims.

**One number of mine did not survive this run.** `backed` on the all-on
configuration read 82% two hours earlier and 94% here, same model, same set —
12 points, far outside the ±2 noise floor I had been quoting. **That ±2 came
from project-four's classification task, not from this metric**, and carrying it
across was exactly the mistake this project has recorded twice: a level measured
on one task does not transfer to another. `backed`'s own noise floor is not
known, and until it is, differences in that column under about 12 points are
not differences.

### `honest` was right by accident, and the accident hid a product bug

**Checked 2026-08-21 rather than assumed.** The metric counted *any* empty
answer as honesty, and `synthesize()` returned an empty answer for four
different reasons. Reading the raw model output on all three unanswerable
questions settled which one was happening: **the model declines correctly every
time**, returning no claim plus a gap naming exactly what is missing —

    {"answer": "", "gaps": ["which model was measured as best for classify"]}

— and it does so identically on a repeat. So **3/3 stands**, but nothing in the
old code could have told you that, and the expectation going in was the
opposite.

**The damage was in the product, not the eval.** That decline was reported to
the reader as *"The local model returned nothing usable"* — the layer blaming
the model for being right. Anyone asking the vault over MCP about something it
does not record saw a failure message instead of the named gap. There are now
four outcomes — `no_evidence`, `declined`, `model_error`, `all_unmarked` — only
the first two count as honesty, a model failure fails the run, and `clean` is
scored over answers actually given rather than over all cases. **`clean 30/30`
above therefore has an out-of-date denominator and is restated on the next full
run.**

**The 30-question set is the first one worth quoting.** On twelve questions
`grounded` swung 9 → 6 → 7 across a single day, which is mostly noise: the set
was too small to separate a code change from Ollama's non-determinism at
temperature 0. At thirty it lands at 67%, close to the 70% the small set was
hovering around — so the earlier swings were the measurement, not the system.
**That last sentence was an inference and is now being measured directly:**
project-four's 592-case faithfulness set is being run twice against this vault on
the same local model, and the case-by-case agreement between two identical runs
is the noise floor every score here should be quoted against.

**`backed` rose to 71% while `grounded` stayed flat**, which is the result worth
noticing: wiring in decisions and learnings did not help the answer find the
right *file* much, but it markedly improved how much of each answer is actually
supported. Structured records make better evidence than prose.

**Cost went up sharply and honestly: ~88 seconds per question**, because claim
checking is one model call per claim and richer evidence means more claims. The
full set takes about 45 minutes. `--no-claim-check` returns it to ~15s and is
much weaker.

**Not known for this run:** which nine questions missed. The background runner
kept only the tail of the output, so the aggregate is sound but the per-question
breakdown was lost. Re-runnable.

### The two gaps the expansion found

**Structured records were the least reachable.** `funnel.retrieve` builds its
corpus from markdown, so **165 decisions and 292 learnings were invisible to
retrieval** — the highest-signal records in the vault, uncitable. Decisions had
a rescue index; learnings had none, and nobody noticed because no question had
ever asked for one. Same shape as the entity pages: 135 of 177 unreferenced
because nothing consumed them.

**Pre-flight your expectations before spending model time.** A non-model check
asking *"can retrieval even reach the expected source?"* caught this in seconds.
A full model run would have shown a low score with no diagnosis.

**Entity wiring, measured separately:** on entity-named questions it contributes
**4 relevant notes per question** that BM25 and vectors both missed.

## What it is not

**A local model writes a weaker answer than Sonnet, and that is not defended
here.** The claim is narrower: every sentence is pinned to a line you can open in
one keystroke. A wrong sentence with a real citation is caught in seconds; a
fluent one without a citation is not caught at all.

**The local model is not reliably deterministic.** Intermittently it returns a
correct answer with no markers at all. That is worse than a wrong answer, because
an uncited answer would otherwise be presented as grounded. `answer.py` retries
once and then labels the output **UNCITED** with the evidence listed, rather than
dressing it up.

**Strict mode makes answers terse.** Dropping unmarked sentences removes the
connective prose that rounds a paragraph out, because that prose is exactly what
carries no citation. Answers read clipped. `--loose` keeps them, clearly labelled
as uncheckable.

**Supersession is thin.** Only 8 markers exist vault-wide today, so the
stale-evidence filter rarely fires. It is built, it is correct, and it is not a
headline feature until the corpus carries more of them.

## Files

| File | What it is |
|---|---|
| `evidence.py` | line-level gather, tagged with authority, supersession and project scope |
| `decisions.py` | the "why not X?" index over every project's `Decisions.jsonl` |
| `answer.py` | prompt, local model, citation verification, gap and conflict extraction |
| `learnings.py` | the "what did we find out the hard way?" index over every `Learnings.jsonl` |
| `verify.py` | claim-level checking — one sentence, one line, no context |
| `evals.py` | the scored set — run it before trusting a change |

Retrieval itself is **not** reimplemented: `funnel.retrieve` already fuses BM25
and vectors and measures 85% right-answer-first. `synth` narrows its winners to
the lines that earned the hit.
