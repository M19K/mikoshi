# Maintenance jobs

One script. `vault_check.py` is the canonical vault health check, run daily by
`mikoshi-daily-vault-check`.

| Script | What it does |
|---|---|
| `vault_check.py` | Stale claims, owner mismatch, schema drift, log hygiene, domain-tag drift, broken links, **ambiguous links**, stale references, orphans, **ledger drift**. Deterministic — no LLM, every finding computable. Also prints quiet projects under **state, not findings**. |

```bash
python3 05-Orchestrator/jobs/vault_check.py --stamp
```

**`--stamp` repairs one class before checking: `updated:` and `last_write:`
trailing the newest log entry.** That drift was found and hand-fixed on
2026-08-17, 2026-08-20 and 2026-08-22 — four projects each time — because
appending a log line and stamping the frontmatter are two actions and only the
first is the agent's purpose. The log proves the write date and the check only
ever flags one direction, so stamping forward cannot lose information. A date
legitimately *ahead* of the log is left alone. Run without `--stamp` to report
without repairing. [@claude-code/maintenance · 2026-08-22]

**Two older scripts were archived 2026-08-19** to
`03-Archive/superseded-linters-2026-08-19/`. `vault-lint.py` and
`vault-status.py` predated this one and overlapped it; their own README had
called the first "largely superseded" and neither was ever scheduled. Every
check unique to them is now here — log hygiene and `domains:` validation folded
in on 2026-08-18, ambiguous links on 2026-08-19. The roll-up that
`vault-status.py` produced is now the Open Board's job.

**A check nobody schedules is not a check.** That was the lesson: log hygiene
and domain validation lived only in the unscheduled script, so four real
findings sat unseen for two days while the scheduled run reported clean.

**Findings and state are separated, and only findings set the exit code.**
`quiet-project` is computed and printed under "state, not findings", never
counted. The vault's own rule says project activity is state rather than a
question (`Queue.md`, [@owner · 2026-08-15]) — but the checker was emitting it as
a finding, so a vault with genuinely nothing to act on exited 1 every single
morning and the exit code stopped carrying information. Add future
dashboard-only classes to `TELEMETRY` rather than to the finding list.

**QA run folders are evidence, not notes.** Anything under `<project>/QA/runs/`
is exempt from the orphan check — some of it is output the product under test
wrote to disk and the run captured verbatim, so nothing will ever link to it.
Broken-link scanning still applies there, because that is a real defect anywhere.

**Scaffolding stems are exempt from the orphan check at any depth**, and the set
is deliberately identical to `link_notes.py`'s `SKIP_STEMS`. Keep them in sync:
when the two populations disagree, this check raises orphans that `link_notes.py
--only` — the remedy the maintenance protocol names — refuses to touch. **A finding
its own prescribed fix cannot act on is a defect in the checker.**

**One design note.** The chronological-order finding is cleared by a later
`CORRECTION` log entry citing the bad date in backticks. The log is append-only
and reordering is forbidden, so without an escape hatch the finding would fire
every day forever on something no agent is allowed to fix — and a report that
always shows the same unfixable line stops being read.
