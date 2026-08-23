# The QA protocol

**One QA standard for every product in the vault. Callable at any milestone.**

Invoke it with the `mikoshi-qa` skill from inside any project.

---

## The whole thing in one paragraph

You write what the product is supposed to do, in plain English, as a Gherkin
`.feature` file. A proven open-source testing agent reads that file, opens the
real product, does the steps, and decides pass or fail — recording video,
screenshots, network traffic and its own reasoning as it goes. Mikoshi adds
four things on top: routing to the right engine per product kind, reading the
evidence back into a report, filing the result into the vault, and capturing
clean product shots for later. **Nothing in the testing itself is ours.**

## Five phases, and you can ask for one

**It used to be one pipeline and that was the defect.** [@owner · 2026-08-20]
Starting it ran everything, so re-checking how a site *looks* meant sitting
through a functional suite nobody was in doubt about. A check you cannot run on
its own is a check you run less often than you should.

| # | Phase | What it answers | Run by |
|---|---|---|---|
| 1 | **design** | how it looks — spacing, hierarchy, contrast, overlap, drift | an agent, via `/design-review` |
| 2 | **code** | does it build, does it typecheck, do its own tests pass | the project's own commands |
| 3 | **feature** | does it do what the written use cases say | Hercules (web) · Midscene (desktop) |
| 4 | **behaviour** | what breaks when someone uses it in ways nobody wrote down | an agent, via `/qa` |
| 5 | **access** | can it be used with a keyboard and a screen reader | Hercules' accessibility pass |

```bash
run.sh <project>                     # all five, in that order
run.sh <project> --phase design      # just this one
run.sh <project> --phase design,code
run.sh --phases                      # what they are
```

**Design runs first** [@owner · 2026-08-20] — not because it is cheapest, but
because a build that looks wrong is not worth driving, and how a thing looks
reaches a person before any of its behaviour does.

**Two phases cannot be run by a shell script, and that is stated rather than
hidden.** `design` and `behaviour` are judgment work. `run.sh` prints what to
invoke, records the phase in `phases.json` as **requested-but-not-done**, and
exits 4; `verify_run.py` then fails the run until the evidence is filed. **A
phase you skip can never read as coverage** — that is the same rule as "no
pixels, no run", one level up.

**A phase that ran no command reports `blocked`, never clean.** A project with
no build and no tests gets "NOTHING TO CHECK", not a tick. This vault has
already been bitten once by a check that could not fail.

Then, once a feature phase has produced verdicts:

| Stage | Script | What it does |
|---|---|---|
| Harvest | `harvest.py` | evidence → `report.md`, `verdicts.json`, clean shots |
| Verify | `verify_run.py` | fails a run that photographed nothing, left a scenario unanswered, or skipped a phase it asked for |

## Why nothing in it is ours

Three attempts came before this one. The first was a hand-written procedure that
reimplemented what already existed; on its first real use, on project-three, it wrote a
confident report about keyboard focus and tab order from **zero screenshots**,
having driven the app's HTTP API and never opened the window. The second wrapped
`gstack /qa`. The third searched GitHub — which is what should have happened
first — and found a project that already met the whole specification.

**The rule that came out of it now lives in `CLAUDE.md`: search before you build.**

## The engines

| Product | Engine | Why |
|---|---|---|
| **Web app or site** | **[TestZeus Hercules](https://github.com/test-zeus-ai/testzeus-hercules)**, as our patched build `mikoshi/hercules` | Gherkin in, full evidence out. The primary engine. **It reasons over the accessibility tree, not pixels, unless its vision tools are switched on** — see below. |
| **Desktop / Electron** | **[Midscene](https://github.com/web-infra-dev/midscene)** | Hercules is Playwright-only. This is the one real gap it does not cover. |
| **iOS** | `gstack /ios-qa` | Live-device vision loop. |
| **Exploratory discovery** | `gstack /qa` | Finds bugs nobody wrote a use case for. Complementary, not a substitute. |
| **Visual / design pass** | `gstack /design-review` | Spacing, hierarchy, drift. |
| **CLI** | Bash, the real binary | — |
| **Library, no UI** | Static checks only | Say so. Never fake a UI pass. |

### Hercules

1.1k stars, AGPL-3.0, ~350 commits, actively maintained. Every run emits,
without being asked:

- **video of the whole run**, and screenshots before and after every action
- **network logs** and **console logs**
- **`agent_inner_thoughts.json`** — the agent's own reasoning, auditable afterwards
- **JUnit XML and HTML** reports

It also covers API testing, WCAG accessibility, security scanning via Nuclei,
mobile emulation, and auto-healing when selectors move.

**We run a patched build, and the patch is the reason the protocol works.**
`05-Orchestrator/qa/engine/` builds `mikoshi/hercules` from upstream with one
change. Upstream requires the planner agent's entire final reply to be a JSON
object, stripping only ```` ```json ```` fences before `json.loads`. When a model
prefixes its own agent name — `planner_agent: ` — the parse throws and the JUnit
XML gets the literal words **`Runtime Failure`**, which reads as a test result
and is a parser complaint. On the first real run, 2026-08-19, that discarded
**10 of 11 completed, judged scenarios**; the one that did not emit the prefix
carries a full correct verdict. The patch scans for the first balanced JSON
object instead, and **fails the image build** if upstream moves the line it
edits — a patch that silently stops applying is how a pipeline looks fixed and
is not.

**Its vision tools are off by default and that makes it a false-negative
machine.** Hercules ships `validate_visual_feature` and
`compare_visual_screenshot`, and gates both behind `LOAD_EXTRA_TOOLS`, which its
own config sets to `false`. With them off it reasons only over the accessibility
tree. On the 2026-08-19 run that tree carried the page header and nothing else,
with an empty alt-text list, and the engine reported *"no photograph"* and *"no
input fields found"* about a page whose own screenshot — captured by the same
run, in the same second — shows a portrait filling half the hero and a field
reading *"Ask me about anything."* `run.sh` now sets `LOAD_EXTRA_TOOLS=true`.
**This requires a multimodal model**: the default local `gpt-oss:20b` cannot see,
so the vision tool loads and returns nothing useful. Use a multimodal model, or
do not write a Gherkin step about pixels.

**Verified end to end 2026-08-19 on a real product.** `project-three`,
`uc-01-arrive`, the scenario that had failed with *"no photograph detected after
comprehensive inspection"*: re-run with the patch and vision on, it **passes**,
its verdict parses without salvage, and `visual_validations/` holds the frame
the model looked at and its written finding. The full suite followed.

**Two honest costs.** About **4 minutes per scenario** on the free local model
(`gpt-oss:20b` via Ollama), and it cannot see; a multimodal hosted model is
faster and is what the visual steps need. And **AGPL-3.0** — fine for testing
our own products on our own machine, never to be bundled into anything shipped.
Our patch is a local modification, not conveyed and not network-served; if that
ever changes, `05-Orchestrator/qa/engine/` is its complete corresponding source.

## Writing a use case

`<project>/QA/features/uc-NN-<slug>.feature`. Gherkin: plain English,
executable, the standard for twenty years.

```gherkin
Feature: Get in without an account
  Scenario: A new user starts with only their own key
    Given a user is on the page http://localhost:3000
    When they type a provider key into the API key field
    And they click Continue
    Then the workspace should be visible
    And no sign-up or sign-in control should appear
```

**Be exact in the `Then` steps** — the engine is pedantic, which is the feature.
Sloppy expectations produce false failures.

**Hercules cannot scroll a page with key presses, and on a scroll-gated page
that means it tests the header and nothing else.** [2026-08-19] On
`project-three` it pressed PageDown thirty times and End eighteen times and
moved the page **zero pixels** — a key-press scroll needs focus on a scrollable
element and it never established one. Every one of that run's ten failures is
downstream of that single failed scroll, because the site's whole interactive
surface is gated behind 99% scroll progress.

**Fixed, through upstream's own extension point rather than another patch.**
`05-Orchestrator/qa/tools/scroll_page.py` adds the tool Hercules does not ship,
loaded via `ADDITIONAL_TOOL_DIRS`, which `run.sh` mounts and sets. It scrolls in
steps so frame-loop animations complete rather than snapping, scrolls an inner
container when the document has no room to move, and — the part that matters —
reports **`SCROLL DID NOT MOVE`** explicitly. The failure being fixed is a
scroll silently doing nothing and the agent concluding the content below it does
not exist, so the tool has to make a dead scroll loud.

**Check it yourself with a second instrument before blaming the product.**
`agent-browser` is installed and drives real Chrome from a shell:

```bash
agent-browser open http://localhost:PORT/
agent-browser eval "window.scrollTo(0, document.documentElement.scrollHeight); 'ok'"
agent-browser eval "JSON.stringify({y: window.scrollY, h: document.documentElement.scrollHeight})"
```

One `scrollTo` took that page 0% → 100%, un-hid its navigation and made its
input visible. The site was fine the whole time.

**Two things the same run teaches about reading verdicts.**

*An accessibility-tree engine cannot see what the page hides from assistive
technology, and that is it being right.* `aria-hidden="true"` on an **ancestor**
removes the whole subtree from the tree, and a descendant's own `aria-label`
cannot override it; a `<canvas>` has nothing to report at all. Both were true
here — but as a *consequence* of the failed scroll, not as a defect. **To know
what a person sees, use vision** with a multimodal model.

*And do not trust a byte count over a screenshot, or a screenshot over a second
instrument.* This run was misdiagnosed twice from this desk before it was
driven a second way: first as a viewport-filtered snapshot, from a 3 KB tree;
then as an accessibility defect in the product, from `aria-hidden` in its
source. Both readings were coherent, evidenced, and wrong. **The protocol's own
rule — a failed assertion is a question until it reproduces — applies to the
agent's conclusions, not only to the engine's.**

**Only the owner confirms a use case.** An agent may draft from the spec and mark it
`unconfirmed` in a comment. A use case an agent invents is the implementation
restated back to itself, and it always passes.

## Running it

```bash
05-Orchestrator/qa/run.sh <project>                        # local model, free, slow, blind
05-Orchestrator/qa/run.sh <project> --model <name>         # hosted, fast, can see
05-Orchestrator/qa/run.sh <project> --only uc-01           # one scenario
```

`--only` matches a substring of the feature filename. It exists because this
protocol's own rule is that a vision-engine failure is a question until it
reproduces, and before `--only` reproducing one scenario meant re-running all
eleven — fifty minutes to re-check one thing. A rule nobody can afford to follow
is not a rule.

A hosted model needs `BASE_URL` and `LLM_KEY` in the environment. Read the key
from the project's gitignored file and never echo it:

```bash
export LLM_KEY="$(python3 -c "import json,pathlib;print(json.loads(pathlib.Path('<path>/secrets.json').read_text())['openrouter_key'])")"
export BASE_URL="https://openrouter.ai/api/v1"
```

A second run on the same day gets its own directory (`2026-08-19-2`). The run
already on disk is the only record of what the product did that day.

## `harvest.py` — the stage that turns a run into an answer

```bash
python3 05-Orchestrator/qa/harvest.py <project> [--run YYYY-MM-DD]
```

**The protocol assumed a tool that runs and records produces findings. It does
not.** Hercules produces evidence and a JUnit XML, and until 2026-08-19 nothing
read either — so the first real run ended with 410 images, 11 videos and no
answer, and `verify_run.py` correctly failed it for a missing `report.md` while
the pixels sat beside it.

Harvest reads the XML, and **whenever the XML says `Runtime Failure` it recovers
the real verdict from the container log** and marks it `salvaged`, so a repair is
never mistaken for a clean read. It writes:

- `<run>/report.md` — verdict per scenario, each finding carrying its video,
  screenshot directory, the agent's own reasoning, and counts of console errors
  and failed requests taken from the logs rather than from the model
- `<run>/verdicts.json` — the same, machine-readable
- `<project>/QA/portfolio/<slug>.png` — one clean shot per scenario

It **exits non-zero if any scenario has no result at all**. A run where the
engine could not answer is not a run that passed.

Salvage stays even though the patched image fixes the parse at source: runs
already on disk still carry the damage, and a repair that exists only inside a
rebuilt image is one you cannot audit.

## What Mikoshi adds — and only this

**Routing.** The table above. Pick the engine that can actually drive the thing.

**Harvesting.** `harvest.py`. The engine records; something has to read it.

**Vault filing.** Copy the run into `<project>/QA/runs/<date>/`; append one line
to the project's `## Log`; `record.py learning <project>` for anything the next
agent would hit again. Read `record.py show <project>` first — `Decisions.jsonl`
records what the owner turned down, and a finding proposing a rejected option is noise.

**Portfolio capture.** One clean, unannotated shot per use case →
`<project>/QA/portfolio/<slug>.png`. Done by `harvest.py`, which prefers the
landing frame and falls back to the last. Product shots for public use later.
**Captured, not published** — anything outward-facing stops at the owner.

## `verify_run.py` — a backstop, and why it still exists

```bash
python3 05-Orchestrator/qa/verify_run.py <project>
```

It fails a run that captured no pixels, a report making interface claims with no
image behind it, any positive verdict on a run that photographed nothing, and —
added 2026-08-19 — **any run carrying a scenario the engine never answered**.
`harvest.py` already exits non-zero on that, but the gate has to know it
independently, or a run verified on its own would pass while a scenario sat
unanswered inside it. It also *warns* when verdicts had to be salvaged, which is
the signal that the run did not use the patched image.

**Both real engines satisfy it by construction**, because they record whether or
not anyone asks. It exists for the one case left: an agent falling back to
driving something by hand and skipping the capture. That is precisely the
failure that happened, so the backstop stays.

## Standing rules

**One stack. No project builds its own QA suite.** [@owner · 2026-08-18] A
product-specific need is a feature file, or a rule added here — never a private
runner in a repo. Five projects previously had five standards and only one of
them tested anything.

**Unit tests are not QA.** A test asserting a function returns the right value
stays where it is. What is forbidden is per-project *QA machinery*.

**Fix what you find — once you have confirmed it is the product.**
[@owner · 2026-08-18] Do not report a defect and ask whether to fix it. Fix it,
re-verify with fresh evidence, say what you fixed. Only the four that stop at
the owner are exempt: money, anything the outside world sees, anything irreversible,
anything that is a matter of his taste.

**The qualifier is not a softening, and it was measured.** [H-014, 2026-08-19]
`project-three` found **four of five Midscene assertion failures were the
engine, not the product** — a parked core returning, the grammatical person of
an answer, and a section heading all behaved correctly when the same steps were
driven deterministically. Followed literally on that run, "fix what you find"
would have had an agent change working code to satisfy a mistaken assertion.

So: **a failed assertion from a vision engine is a question until it reproduces
deterministically.** Before touching the product, drive the same steps a second
way — a scripted click, a direct check, a fresh run — and confirm the failure is
still there. If it only fails under the model's eye, the finding is against the
test, and the fix belongs in the feature file.

This does not reintroduce asking. Reproducing takes seconds, needs nobody, and
is itself part of fixing; what it prevents is an agent confidently repairing
something that was never broken. `run.sh --only <slug>` re-runs one scenario.

**Open the run's own screenshot before you act on a "not present" finding.**
[2026-08-19] The strongest case for the rule above came from this engine
contradicting itself inside a single run: it reported *"no photograph or image
elements detected after comprehensive inspection"* and *"exhaustive page
inspection completed — NO input fields found"*, and the landing frame it saved
in that same run shows a portrait filling half the hero and a field reading
*"Ask me about anything."* The evidence and the verdict came from the same
engine in the same second and disagreed. `harvest.py` puts the shot path beside
every finding for exactly this reason — the check costs one look.

**The report is produced, never written.** `harvest.py` builds `report.md` from
the engine's own artifacts. Do not hand-write one: the failure this protocol
exists to prevent is a confident report with nothing behind it, and a report an
agent types is that failure with extra steps. Add your own reading *under* the
generated file, marked as yours.

**Capture must never quietly downgrade.** [@owner · 2026-08-20] project-three's design run
hit a macOS Screen Recording prompt, and rather than stop it switched to having
the app photograph itself. That is a different, weaker instrument, and the run
carried on as if nothing had changed — the owner had in fact granted the permission
seconds later, and the run never went back.

**The rule: if the thing that sees the screen cannot see the screen, the run
stops.** Say which permission is missing and where to grant it, in one line, and
halt. Do not substitute a lesser capture method, and never report a run as
complete on evidence gathered by a fallback the protocol did not choose. A
silent downgrade is the same failure as a silent skip: it looks like coverage.

**the owner has standing authorisation to grant these** — his words, 2026-08-20:
*"this is internal testing, I'm authorizing all of this."* macOS grants Screen
Recording **once per application, permanently**, so this is a one-time
interruption per tool, not a per-run tax. **The agent surfaces it; the owner clicks
it.** An agent does not click system security dialogs on his behalf, and that
line does not move — but it must never route around one either.

**Credentials.** Read from the project's gitignored `.env`. Never print, echo,
log or paste a key; never let one reach a screenshot or report; mask it if it
would appear on screen. Name which key was needed, never its value. The local
model path needs no key at all — but *no key* is not the same as *safe to leave
alone*, and this line used to conflate them.

**The local model has a failure mode that looks exactly like success.**
`gpt-oss:20b` over Ollama's `/v1` endpoint writes a reasoning trace before it
writes an answer, and `/v1` has no field to turn that off. Under a completion cap
it returns HTTP 200 with `finish_reason: "length"` and either an empty string or
a plausible half-written one — no error, nothing in a log, and a run that is then
judged by a model saying nothing. Measured 2026-08-20: a one-line decision costs
222 tokens and a short plan costs 440, so any single "keep the cap above N" rule
passes on the first and fails on the second. [H-019]

`run.sh` therefore probes the configured model with both prompt shapes before the
feature phase and refuses to start if either comes back empty or truncated —
`preflight.py`, overridable with `SKIP_PREFLIGHT=1`, which must be declared in
the run report. An open port proves the server is up; only an answer proves the
model can answer.
