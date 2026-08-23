# Routines — the part that makes it self-maintaining

**These are prompts, not scripts, and that is the whole design.** Each one is
run on a schedule by a *session* of your agent — Claude Code, Hermes, anything
that can hold a scheduled task. A session already holds the connections you are
signed into, so mail, meetings and documents get read **with no API key existing
anywhere** to leak or rotate.

**Without these, Mikoshi does not maintain itself.** It answers questions and
holds your records, but nothing repairs broken links, nothing pulls the outside
world in, and nothing notices that a source stopped arriving. That is the single
biggest difference between the vault this came from and a fresh install.

| Routine | When | What it does |
|---|---|---|
| `mikoshi-daily-vault-check` | daily, morning | Repairs the vault's own machinery — broken links, schema drift, stale ownership claims, queue hygiene. Fixes what is unambiguous, escalates the rest. |
| `mikoshi-daily-ingestion` | daily, after the check | Runs the funnel over every declared source, then reads mail, meetings and Drive through the connections the session holds. |
| `mikoshi-weekly-reconciliation` | daily or weekly | Reads every project's log and posts the connections nobody spotted, so you stop being the message bus between your own agents. |

## Installing them

**Claude Code** — one per scheduled task, pasting the file's contents as the
prompt. Ask your agent: *"create a scheduled task named `mikoshi-daily-vault-check`
that runs at 08:20 daily, with the prompt in `templates/routines/mikoshi-daily-vault-check.md`"*.

**Anything else** — the prompt is portable. It needs a working directory at the
vault root and whatever connectors the ingestion one is told to use.

## Read them before you run them

They are written to be read. Each explains why it does what it does, and two of
them hold real authority — the vault check repairs files without asking. **That
authority is only safe because every fix is logged**, so if you narrow what your
routine may do, keep the logging.

**Edit the times.** They are staggered in the original so the check finishes
before ingestion starts; keep the order, move the clock.
