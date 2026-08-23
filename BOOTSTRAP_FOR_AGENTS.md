# BOOTSTRAP_FOR_AGENTS.md — install Mikoshi for the person you work for

**You are the installer.** Your human pasted one line and stopped. Everything
below is yours to do. Ask them only what this file says to ask, in the order it
says, and do not improvise extra questions — Mikoshi is a protocol, and the
questions that are theirs to answer are exactly the ones the interview asks.

**Read this whole file before running anything.** It is short, and two of the
steps are irreversible in ways the human should be told about first.

---

## Hard rules for you, the installing agent

1. **Never invent an answer to an interview question.** If they say "I don't know", record that they did not answer. A placeholder written into `CLAUDE.md` becomes a rule every future agent reads as theirs.
2. **Never edit `PROTOCOL.md`.** It is identical in every vault and is replaced whole on upgrade. Everything the human wants different goes in `CLAUDE.md`.
3. **Do not pull a model without saying what it costs them.** `gpt-oss:20b` is about 13 GB of disk and takes a while. Say so, then pull it.
4. **Never put a key anywhere.** Mikoshi runs on a local model and needs no credential. If they offer one, tell them it is not required.
5. **Stop and report rather than working around a failure.** A vault that half-installed and says so is recoverable. One that half-installed silently is the failure mode this whole system exists to prevent.
6. **Every phase is idempotent.** If something fails, fix it and re-run that phase. Nothing here needs a clean slate.

---

## Phase 1 · Preflight

Check, and install only what is missing:

```bash
python3 --version        # 3.11 or newer
ollama --version         # from ollama.com if absent
```

Then the models, after telling them the size:

```bash
ollama pull gpt-oss:20b          # ~13 GB, does every judgement
ollama pull nomic-embed-text     # ~270 MB, does search
```

**If they refuse the 13 GB:** Mikoshi still installs and still holds their
records. Search works. Written answers do not. Say that plainly and continue —
it is a legitimate choice and the model can be pulled later.

## Phase 2 · Build the vault

From the cloned repo, in the directory that will *be* the vault:

```bash
python3 init.py
```

**This runs the interview.** Seven questions. Ask them exactly as printed, one
at a time, and record what they actually say. When it finishes, **read every
answer back in one block** and ask: *"Is this how you want your agents to work?"*
Only continue when they say yes. If they change one, re-run `init.py` in an
empty directory or edit `CLAUDE.md` directly — it is theirs to edit.

**What this creates**, and tell them the split, because it is the thing most
worth understanding:

- `PROTOCOL.md` — the bedrock. Identical in every Mikoshi vault, never edited, replaced whole on upgrade.
- `CLAUDE.md` — their answers. Theirs to change, and untouched by an upgrade.

## Phase 3 · Check what is missing

```bash
.venv/bin/python doctor.py      # Windows: .venv\Scripts\python doctor.py
```

**Read the DEGRADED rows to them, not the whole output.** Required rows they
must fix. Optional rows are whole features being off, which is fine if they
know. **The degraded rows are the ones that look like they are working.**

## Phase 4 · Prove it works

```bash
.venv/bin/python verify.py
```

**Exit 0 or the install is not done.** This writes a real decision through the
real path, reads it back, removes it, scans for key material, and runs the MCP
server's self-test. Do not report success on anything less. If it fails, the
failing row names the fix — do that and re-run.

## Phase 5 · Wire it to yourself

Mikoshi speaks MCP. Register it so you can call it directly:

**Claude Code**
```bash
claude mcp add mikoshi -- python3 <vault>/05-Orchestrator/mcp_server.py
```

**Anything else that speaks MCP** — point it at the same command over stdio.
Six tools appear: `answer`, `why_not`, `recall`, `entity`, `queue`, `digest`.

**Tell them what this means:** any agent with that server registered can read
and write the vault. That is the point, and it is also the consequence.

## Phase 6 · Schedule the routines, or say plainly that it will not maintain itself

The three prompts in `05-Orchestrator/routines/` are what repair the vault,
pull the outside world in, and notice a source going quiet. **Without them
Mikoshi answers questions and holds records and does nothing on its own.**

Read `05-Orchestrator/routines/README.md` and set them up in whatever your
harness uses for scheduled work. Stagger them: check, then ingestion, then
reconciliation.

**If the harness cannot schedule anything, say so rather than pretending.** A
human running `funnel.run` by hand once a week is a real fallback; a human who
believes it is automatic and finds out in a month is not.

## Phase 7 · Hand off

Say these five things, in this order, and no more:

1. **It is empty, and that is correct.** Measured at 0 of 27 questions answered on an empty vault. It has nothing to cite and will not invent. That refusal is the product.
2. **The value tracks what they put in.** Their notes are two thirds of it, measured.
3. **The unusual habit is recording what they turned down**, not only what they chose. That store alone was worth 5 of 27 correct answers, and almost nobody keeps it.
4. **Nothing leaves their machine** unless they set `MIKOSHI_LLM_BASE_URL`.
5. **The three things to try**, which `verify.py` prints — ask it something it cannot know, record a real decision, ask again.

---

## When something fails

| What you see | What it means |
|---|---|
| `NO-SOURCES:` from the funnel | Nothing declared to read. Correct on a fresh vault; add feeds to `Sources.md`. |
| `vec=False` on the first line of a run | `sqlite-vec` is missing. Semantic search is off and everything else looks fine. Fix it. |
| `ABSENT` from `connectors status` | A source has stopped arriving. **Not the same as quiet** — quiet means it looked and found nothing. |
| `model_error` from the answer layer | The model returned nothing usable. Not a refusal — check the model is up. |
| `doctor` green, `verify` red | Everything needed is present and the chain between the pieces is broken. This is the interesting failure. |

**Report a partial install as partial.** Name the phase that failed and what
you tried. An agent that says "done" over a broken vault is the exact failure
Mikoshi is built to make impossible, and it would be a poor first impression to
do it during the install.
