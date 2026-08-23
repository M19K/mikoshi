# tests

170 tests, no network, no model, about two seconds. They run on Ubuntu 3.11,
Ubuntu 3.12, Windows and macOS in the same workflow as the install check.

```bash
python -m pip install -e ".[dev]" && python -m pytest
```

## The one rule that is not obvious

**`orchestrator/` is generated. If a test fails, fix the vault, not this repo.**

`orchestrator/` is rebuilt whole from a working vault by `extract_system.py`.
An edit made here looks like a fix, passes, and is silently reverted by the
next extraction — which happened three times in one day on 2026-08-23,
including to a fix that had already been reported as done. The extractor now
names every file it is about to overwrite. Read that output.

Everything outside `orchestrator/` — `init.py`, `doctor.py`, `verify.py`,
`templates/`, and these tests — is edited here, normally.

## What is covered, and why these

Each file pins behaviour that failed at least once, in a way nothing else
noticed.

| File | What it holds down |
|---|---|
| `test_record.py` | The write path, in a real subprocess. It died on Windows printing its own arrow while every other check stayed green. |
| `test_registry.py` | An unconfigured registry must refuse, not report `0/0 feeds, nothing new` — a healthy-looking run over an empty corpus. |
| `test_keys.py` | `resolve()` never substitutes a key that happens to exist, and nothing ever returns a value where a label would do. |
| `test_connectors.py` | *Quiet* and *ABSENT* must never print the same line, and `--strict` must exit non-zero on the second. |
| `test_learn.py` | The signal/noise loop stays inert below 30 verdicts **and** whenever the examples are one-sided. |
| `test_no_hardcoding.py` | The checker catches all four kinds, exempts genuine defaults, and does not accuse the codebase it ships with. |
| `test_privacy_check.py` | Keys, addresses, private IPs, home paths, filenames and git history — caught; documentation examples — not. |
| `test_recall.py` | Ranking on the read path: "why not X" surfaces the decision that refused X, not everything mentioning X. |
| `test_predictions.py` | The only record that can be graded: a claim cannot be edited, hedged, or resolved into existence after the fact — and an unscored corpus reports no score, never a flattering one. |
| `test_shipped_repo.py` | Invariants of the artifact: it compiles, nothing personal survives extraction, no corpus or database ships. |

## What is not covered, stated rather than implied

A green suite that quietly skips half a checker reports a confidence it has not
earned.

- **The image and video passes of the privacy check.** They need `tesseract`
  and a local vision model; no CI runner has either. Run by hand before a
  release. This is the surface where both real leaks actually lived.
- **Anything that needs a model.** Classification, distillation, embedding,
  answering. Pulling a model is 13 GB per run and a green tick that took twenty
  minutes gets ignored. Covered by the eval suite, which runs locally.
- **Fetching.** No test reaches the network. What a real feed returns is
  verified by fetching and counting, by hand, when a source is added.
- **The QA layer.** It drives a browser through a container.
- **`vault_check.py`, `sync_board.py`, `ledger.py`.** Deterministic and
  testable; simply not written yet. The largest remaining gap in this suite.

## Writing another one

Two habits, both learned here the hard way:

**Assert a stable token, never the prose.** A test that matched the funnel's
"no sources" wording failed on working code the day the wording improved. The
registry emits `NO-SOURCES:` for exactly this reason.

**Never name a real project.** `tests/` is edited here, so it never passes
through the extractor's de-personalising step — a project name written into a
docstring ships exactly as typed. The privacy check caught one the day these
were written.

**Never assume a path separator.** An assertion that a citation ends
`alpha/Decisions.jsonl` passed on three platforms and failed on Windows, where
it is a backslash. Compare `PurePath(...).parts`, not string endings — this is
the fourth test in this project written against one machine.

**Assemble fixtures at run time.** A literal key, address or home path written
into a test file becomes a permanent finding in the privacy checker's own
report — and a checker whose report is mostly its own test fixtures is a
checker people learn to skim.
