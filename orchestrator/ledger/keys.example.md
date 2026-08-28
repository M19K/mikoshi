# Master API Keys — FORMAT EXAMPLE

**This file is the template. The real one is `keys.md`, which is gitignored and
never leaves the machine.** Copy this shape exactly — `keys.py` parses it, and a
label it does not recognise is reported rather than guessed at.

## The shape

- A `##` heading names the **provider**. Case-insensitive, spaces allowed.
- Each line under it is `label: value`.
- `#` comments and blank lines are ignored. Anything not `label: value` is ignored.

**The label is the routing key.** For a product it is the project's folder name
exactly — `gamma`, not `gamma` or `gamma-app`. That exactness is the whole point:
an agent resolves its key from `pwd`, so a label that does not match a folder is
a key nobody will ever find.

## Rules that do not bend

- Never print, echo, log, or paste a value. Name the label you used, never the key.
- Never copy a value into a project's own repo or `.env`. Read it here, at the moment you need it.
- `management` is not for inference. It can create and revoke keys — it is the most
  powerful line in the file and the first to rotate if the file is ever exposed.

---

## OpenRouter

- gamma: sk-or-v1-REPLACE
- delta: sk-or-v1-REPLACE
- beta: sk-or-v1-REPLACE
- gamma: sk-or-v1-REPLACE
- mikoshi-internal: sk-or-v1-REPLACE
- owner-personal: sk-or-v1-REPLACE
- management: sk-or-v1-REPLACE

## Hume

- gamma-api: REPLACE
- gamma-secret: REPLACE

## OpenAI

- gamma: sk-REPLACE
