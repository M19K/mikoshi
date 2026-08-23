# <Your Vault> — Read This First

> **This file is tool-neutral.** The `CLAUDE.md` filename is historical. Every
> agent, tool and model follows this same protocol. `AGENTS.md` and `GEMINI.md`
> are pointers to it and deliberately restate nothing: a rule written in two
> places drifts the first time one copy is edited.

**Edit this file before you use it.** It is a working protocol, not a
suggestion, and the parts that are yours are marked `<like this>`.

---

## Orientation — the 60-second version

1. **Where am I?** The vault. It is the only working root: markdown context and code live together under `02-Projects/<project>/`.
2. **Read the queue first.** `05-Orchestrator/Queue.md` — who is doing what, what is blocked, what is addressed to you.
3. **What do I read?** This file → the owner's profile → the project's `_index.md`, `Reference.md`, `Live Status.md`.
4. **What may I write?** Your project's `Live Status.md` and its `code/`, freely. Curated files only to record a decision the owner made in this session.
5. **How do I record it?** Append one line to `## Log` in that `Live Status.md`: `- YYYY-MM-DD · @your-tag — what changed.` Absolute dates. Append only.
6. **What must I never do?** Invent a rule and attribute it to the owner. Rewrite another agent's log line. Restructure the vault uninvited.
7. **No tag assigned?** Use `@unknown-agent` and declare your origin on your first line.

---

## The model

**The owner decides. Routines maintain. Agents execute.**

That sentence is the whole system and everything below is a consequence of it.

**The owner decides:** direction, money, anything irreversible or outward-facing,
standing rules, and what counts as good. **Routines maintain:** scheduled jobs
repair the vault's own machinery and run the ingestion funnel without being
asked. **Agents execute:** one project at a time, full authority inside it, none
outside it.

The correction worth stating plainly, because most setups get it backwards: the
owner does not edit markdown. A rule saying "only the owner may write this file"
was never about hands — it was about whose *decision* the contents represent.

---

## The decision rules

**These exist so agents stop asking.** Every question sent up that one of these
already answers is a question the agent should have answered itself.

1. **Decide anything reversible.** If undoing it costs less than asking, do it and log why.
2. **Measure before you make a rule.** No threshold or policy without a number you actually took.
3. **Verify by fetching, never by assuming.** Open the thing, count the items, read the title.
4. **Prove yourself wrong before saying no.** Search first; report impossibility only after, and say what you searched.
5. **Search before you build.** Someone has built something close. Take it, understand it, improve it, differentiate.
6. **Say what is true, not what is comfortable.** A generous self-assessment is a broken instrument.
7. **Four things stop at the owner:** money, anything the outside world sees, anything irreversible, anything that is a matter of their taste.
8. **Do your own recommendations.** If you are writing "I recommend…", you have already decided. Do it, then say what you did — unless rule 7 applies.

**When two collide, rule 7 wins outright.**

---

## Structure

```
00-Inbox/            the owner's own capture queue — agents do not write here
01-Knowledge Base/   distilled reference every project draws on; the funnel owns it
02-Projects/         one folder per project, markdown context plus code/
03-Archive/          finished or superseded, kept for reference, deliberately dead
05-Orchestrator/     the queue, the funnel, the maintenance jobs, the ledger
Home.md              navigation entry point for a human
```

### Areas of focus

Tags, not folders. Each project's `_index.md` carries a `domains:` list.
**Replace this table with your own** — the health checker validates against it.

| Tag | Area |
|---|---|
| `<Your Area>` | `<what it covers, and whether it ends>` |

### Per project

```
02-Projects/<project-name>/
├── _index.md      dashboard + frontmatter (type, domains, code, status)
├── Reference.md   standing rules, every line [@source · date]-tagged
├── Live Status.md current state, the ownership lock, and the append-only ## Log
├── Decisions.jsonl   what the owner chose, and what they turned down
├── Learnings.jsonl   what an agent found out the hard way
└── code/          the repository, if this project has one
```

---

## Writing

| File | An agent may write it |
|---|---|
| `Live Status.md`, `code/` | Freely, in the project you hold |
| `Reference.md`, `_index.md` | To record a decision the owner made — tag the line `[@owner · date]` |
| `CLAUDE.md`, `Home.md` | Same rule, in a session where they asked for it |
| `01-Knowledge Base/` | The funnel owns it; an agent may add a verified finding, sourced |
| Another project's files | **No. Post a handoff in the queue instead.** |

**The one prohibition that matters: never invent a rule and present it as the
owner's.** A line tagged `[@owner · date]` is a claim that they decided it.

### Write it down the moment it happens

**Chat is not storage.** The log is past tense and outcome-only, so it cannot
hold the two things that matter most — what the owner preferred, and what an
agent learned the hard way. Those get their own files, appended as they happen:

```bash
python3 record.py decision <project> --chose "..." --over "a,b" --why "..."
python3 record.py learning <project> --key some-slug --insight "..."
python3 record.py show <project>
```

**The rejected options are the valuable half.** Without them the next agent
re-proposes what was already refused.

### The log

`- 2026-01-15 · @claude-code/<project> — what happened, in one line.`

**The characters are exact.** Middle dot `·` (U+00B7) after the date, em dash
`—` (U+2014) before the text. A hyphen instead and the tooling silently ignores
the entry. **Never delete or rewrite a log entry** — corrections are new lines.

### The lock

`owner:` and `last_write:` in `Live Status.md` frontmatter. Advisory, not a
mutex: it exists so a second agent knows someone is already there.

---

## Communication

| Channel | Tense | For |
|---|---|---|
| `05-Orchestrator/Queue.md` | **Present** | who is doing what · what is blocked · what one project needs from another |
| `<project>/Live Status.md` → `## Log` | **Past**, append-only | what happened |

Read the queue at session start. Update it before you finish. Both mandatory.

---

## Closing duties

**Finishing means the record is finished too. Every time, unasked.**

1. **Update the board** of what is still outstanding.
2. **Sweep the leftovers.** When a thing reaches its final version, re-read its docs and delete what the new version replaced. A correction appended is not a correction.
3. **Record any outside dependency**, in the same turn you adopt it. Never later.

---

## How to talk to the owner

**`<This section is entirely yours. The defaults below are one person's and
almost certainly not yours — rewrite them.>`**

- Replies are five points maximum. If it does not fit in five, it is noise.
- Order by: was the thing accomplished · is there a blocker · what changes what I do next.
- Single-line bullets. Tables fine. Flowing prose is not.
- No jargon. If a term is unavoidable, say what it means in the same breath.
- A question gets two or three lines, not five points.

---

## Keys

Every API key lives in one gitignored file, and code asks a resolver for the one
labelled for its project — never parses the file, never reads a raw environment
variable. **The resolver never falls back to another label:** a missing key
raises and says what to do, because quietly substituting whatever key exists is
how one product's spend lands on another's bill.

```python
from ledger.keys import resolve
key = resolve("openrouter", project="<your-project>")
```

**Never print, echo, log or commit a key.** Name the label, never the value.
