#!/usr/bin/env bash
# run.sh — the QA protocol, in phases you can ask for one at a time.
#
#   ./run.sh <project>                        every phase, design first
#   ./run.sh <project> --phase design         only that one
#   ./run.sh <project> --phase design,code    only those
#   ./run.sh <project> --only uc-01           one feature file, feature phase
#   ./run.sh <project> --model <name>         override the LLM
#   SKIP_PREFLIGHT=1 ./run.sh <project>       skip the model health probe
#   ./run.sh --phases                         what the phases are
#
# **It used to be one pipeline and that was the defect.** Starting it ran
# everything, so re-checking how a site LOOKS meant sitting through a functional
# suite nobody was in doubt about. A check you cannot run on its own is a check
# you run less often than you should. [@owner · 2026-08-20]
#
# Nothing in the testing itself is ours. Hercules (AGPL-3.0, patched build —
# see engine/) drives the feature phase; gstack's /design-review and /qa carry
# the design and behaviour phases. We route, read the evidence back, and file it.
set -euo pipefail

VAULT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
QA="$VAULT/05-Orchestrator/qa"

if [ "${1:-}" = "--phases" ] || [ "${1:-}" = "--help" ]; then
  python3 "$QA/phases.py"; exit 0
fi

PROJECT="${1:?usage: run.sh <project> [--phase LIST] [--only X] [--model NAME]  ·  --phases to list}"; shift || true
PDIR="$VAULT/02-Projects/$PROJECT"
[ -d "$PDIR" ] || { echo "no such project: $PROJECT"; exit 2; }

# ── which model judges this product ──────────────────────────────────────────
# Asked, not assumed. `model.py` reads what project-four has measured ON THIS
# PRODUCT and picks accordingly; where nothing has been measured it keeps the
# free local lane and says so, because quality does not transfer between
# products and another product's table would be a guess here.
#
# It never fails: if project-four is absent, unmeasured or not running, QA
# proceeds on the local default with that fact written into the run record. A
# QA protocol that cannot start because a routing layer is missing is worse
# than one that starts honestly.
#
# An explicit --model or MODEL= still wins, so a one-off override needs no
# argument with the resolver.
if [ -z "${MODEL:-}" ]; then
  eval "$(python3 "$QA/model.py" "$PROJECT" 2>/dev/null)" || true
fi
MODEL="${MODEL:-gpt-oss:20b}"
BASE_URL="${BASE_URL:-http://host.docker.internal:11434/v1}"
KEY="${LLM_KEY:-ollama}"
QA_MODEL_SOURCE="${QA_MODEL_SOURCE:-default, project-four not consulted}"
IMAGE="${IMAGE:-mikoshi/hercules}"
LOAD_EXTRA_TOOLS="${LOAD_EXTRA_TOOLS:-true}"
SCENARIO_TIMEOUT="${SCENARIO_TIMEOUT:-1200}"
SKIP_PREFLIGHT="${SKIP_PREFLIGHT:-0}"
ONLY=""
PHASE_SPEC=""

while [ $# -gt 0 ]; do
  case "$1" in
    --model) shift; MODEL="${1:?--model needs a name}";;
    --only)  shift; ONLY="${1:?--only needs a substring}";;
    --phase) shift; PHASE_SPEC="${1:?--phase needs a list, e.g. design,code}";;
  esac
  shift || true
done

PHASES=$(python3 "$QA/phases.py" --resolve "$PHASE_SPEC") || exit 2

# A second run on the same day gets its own directory. Re-using one would write
# new evidence on top of an existing run's, and the run already on disk is the
# only record of what the product did that day.
RUN="$PDIR/QA/runs/$(date +%F)"
if [ -d "$RUN" ] && [ -n "$(ls -A "$RUN" 2>/dev/null)" ]; then
  N=2; while [ -d "$PDIR/QA/runs/$(date +%F)-$N" ]; do N=$((N+1)); done
  RUN="$PDIR/QA/runs/$(date +%F)-$N"
fi
mkdir -p "$RUN"
python3 "$QA/phases.py" --request "$RUN" "$(echo "$PHASES" | tr '\n' ',')"

echo "→ $PROJECT · phases: $(echo "$PHASES" | tr '\n' ' ')"
echo "  run: ${RUN#"$VAULT"/}"
echo "  judged by: $MODEL  ($QA_MODEL_SOURCE)"

# The decision and its reasoning go INTO the run, so a report can never be read
# without knowing what looked at the product and how well that thing was
# measured on it.
python3 "$QA/model.py" "$PROJECT" --record "$RUN" >/dev/null 2>&1 || true
python3 "$QA/model.py" "$PROJECT" --explain 2>/dev/null | sed 's/^/  /' | tail -n +2
echo

NEEDS_AGENT=0
RAN_FEATURE=0

for PHASE in $PHASES; do
case "$PHASE" in

# ── design ────────────────────────────────────────────────────────────────
# Judgment work. A shell script cannot look at a page and say the hierarchy is
# muddled, so this phase names what to invoke rather than pretending to do it.
design)
  echo "── design · how it looks"
  mkdir -p "$RUN/design"
  python3 "$QA/phases.py" --need-agent design "$RUN" "$PROJECT"
  NEEDS_AGENT=1
  echo
  ;;

# ── code ──────────────────────────────────────────────────────────────────
# The project's own commands, discovered rather than declared where possible.
# A project may override everything with QA/code.sh.
code)
  echo "── code · does it build, do its own tests pass"
  mkdir -p "$RUN/code"
  # CODE_STATE starts as "nothing" rather than "pass". A phase that ran no
  # command must never report clean: this vault already recorded that trap once
  # as `a-check-that-cannot-fail-is-not-a-check`, when three viewports silently
  # stayed at one size and the report printed three sets of passes.
  CODE_OK=1
  CODE_STATE=nothing
  if [ -x "$PDIR/QA/code.sh" ]; then
    echo "  · QA/code.sh"
    CODE_STATE=ran
    ( cd "$PDIR" && ./QA/code.sh ) >"$RUN/code/code.log" 2>&1 || CODE_OK=0
  else
    SRC=""
    for c in "$PDIR/code" "$PDIR/code/site" "$PDIR"; do
      [ -f "$c/package.json" ] && { SRC="$c"; break; }
    done
    if [ -n "$SRC" ]; then
      : >"$RUN/code/code.log"
      RAN_ANY=0
      for s in build typecheck lint test; do
        if node -e "process.exit(require('$SRC/package.json').scripts?.['$s']?0:1)" 2>/dev/null; then
          echo "  · npm run $s"
          RAN_ANY=1; CODE_STATE=ran
          { echo "=== npm run $s ==="; ( cd "$SRC" && npm run "$s" 2>&1 ); } \
            >>"$RUN/code/code.log" || CODE_OK=0
        fi
      done
      [ "$RAN_ANY" = 1 ] || echo "  · package.json declares none of build/typecheck/lint/test" \
        | tee -a "$RUN/code/code.log"
    else
      echo "  · no package.json and no QA/code.sh — nothing to run. Saying so, never faking a pass." \
        | tee "$RUN/code/code.log"
    fi
  fi
  if [ "$CODE_STATE" = nothing ]; then
    echo "  · NOTHING TO CHECK — this project declares no build, tests or QA/code.sh."
    echo "    Recorded as blocked, not passed. A phase that ran no command is not a clean phase."
    python3 "$QA/phases.py" --record code "$RUN" blocked
  elif [ "$CODE_OK" = 1 ]; then
    echo "  ✓ clean"
    python3 "$QA/phases.py" --record code "$RUN" done
  else
    echo "  ✗ FAILED — see code/code.log"
    python3 "$QA/phases.py" --record code "$RUN" failed
  fi
  echo
  ;;

# ── feature ───────────────────────────────────────────────────────────────
feature)
  echo "── feature · does it do what the use cases say"
  FEATURES="$PDIR/QA/features"
  if [ ! -d "$FEATURES" ] || [ -z "$(ls -A "$FEATURES"/*.feature 2>/dev/null)" ]; then
    echo "  · no .feature files at $FEATURES — write the use cases first."
    python3 "$QA/phases.py" --record feature "$RUN" blocked
    echo; continue
  fi

  SELECTED=()
  for F in "$FEATURES"/*.feature; do
    if [ -n "$ONLY" ]; then
      case "$(basename "$F")" in *"$ONLY"*) SELECTED+=("$F");; esac
    else
      SELECTED+=("$F")
    fi
  done
  [ "${#SELECTED[@]}" -gt 0 ] || { echo "  · no feature file matches --only '$ONLY'"; exit 2; }

  if [ "$BASE_URL" = "http://host.docker.internal:11434/v1" ] \
     && ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
    echo "Ollama is not answering on :11434. Start it, or pass a hosted --model."; exit 3
  fi

  # An open port proves the server is up, not that the model can answer. The
  # local model returns an EMPTY STRING with HTTP 200 when a completion cap
  # eats its reasoning trace (H-019), so a reachability check passes and the
  # whole run is then judged by a model saying nothing. Probe it for real.
  if [ "$SKIP_PREFLIGHT" != "1" ]; then
    PRE_URL="${BASE_URL/host.docker.internal/localhost}"
    python3 "$QA/preflight.py" --model "$MODEL" --base-url "$PRE_URL" --key "$KEY" \
      || { echo "  · preflight failed — not running QA against a model that cannot answer."
           echo "  · SKIP_PREFLIGHT=1 to override, and say so in the run report if you do."
           exit 4; }
  fi
  if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "  · building $IMAGE (the patch is guarded; a build failure means upstream moved)"
    docker build -t "$IMAGE" "$QA/engine"
  fi

  mkdir -p "$RUN"/{input,output,test_data}
  rm -f "$RUN"/input/*.feature
  cp "${SELECTED[@]}" "$RUN/input/"
  echo "  · ${#SELECTED[@]} feature file(s) · model $MODEL"

  # ONE CONTAINER PER FEATURE FILE. Hercules does not glob its input directory:
  # INPUT_GHERKIN_FILE_PATH is a single file and defaults to input/test.feature,
  # so a run with named feature files died on FileNotFoundError after creating
  # its output folders — which looked from outside like a run that had happened.
  FAILED=0
  for F in "$RUN/input"/*.feature; do
    NAME=$(basename "$F" .feature)
    echo "    · $NAME"
    CNAME="mikoshi-qa-$$-$NAME"
    docker rm -f "$CNAME" >/dev/null 2>&1 || true
    ( sleep "$SCENARIO_TIMEOUT"; docker rm -f "$CNAME" >/dev/null 2>&1 ) & WATCHDOG=$!
    docker run --rm --name "$CNAME" \
      -v "$RUN:/testzeus-hercules/opt" \
      -e INPUT_GHERKIN_FILE_PATH="/testzeus-hercules/opt/input/$(basename "$F")" \
      -e LLM_MODEL_NAME="$MODEL" \
      -e LLM_MODEL_API_KEY="$KEY" \
      -e LLM_MODEL_BASE_URL="$BASE_URL" \
      -e HEADLESS=true \
      -e LOAD_EXTRA_TOOLS="$LOAD_EXTRA_TOOLS" \
      -v "$QA/tools:/mikoshi-tools/qa_tools:ro" \
      -e ADDITIONAL_TOOL_DIRS=/mikoshi-tools/qa_tools \
      "$IMAGE" >"$RUN/output/$NAME.log" 2>&1 \
        || { echo "      (non-zero exit or hit the ${SCENARIO_TIMEOUT}s cap — see output/$NAME.log)"
             FAILED=$((FAILED+1)); }
    kill "$WATCHDOG" >/dev/null 2>&1 || true
    wait "$WATCHDOG" 2>/dev/null || true
  done
  [ "$FAILED" -gt 0 ] && echo "  · $FAILED feature(s) had a non-zero exit"
  RAN_FEATURE=1
  python3 "$QA/phases.py" --record feature "$RUN" done
  echo
  ;;

# ── behaviour ─────────────────────────────────────────────────────────────
behaviour)
  echo "── behaviour · what breaks when nobody followed the script"
  mkdir -p "$RUN/behaviour"
  python3 "$QA/phases.py" --need-agent behaviour "$RUN" "$PROJECT"
  NEEDS_AGENT=1
  echo
  ;;

# ── access ────────────────────────────────────────────────────────────────
# Hercules emits accessibility_logs.csv per scenario, so this phase reads what
# the feature run already produced rather than driving the product twice.
access)
  echo "── access · keyboard and screen reader"
  if [ -z "$(find "$RUN/proofs" -name accessibility_logs.csv 2>/dev/null)" ]; then
    echo "  · nothing to read — this phase reads what the feature phase captures."
    echo "    Run:  run.sh $PROJECT --phase feature,access"
    python3 "$QA/phases.py" --record access "$RUN" blocked
  else
    python3 "$QA/phases.py" --access "$RUN"
    python3 "$QA/phases.py" --record access "$RUN" done
  fi
  echo
  ;;

esac
done

# Harvest and verify only make sense once a feature run has produced verdicts.
if [ "$RAN_FEATURE" = 1 ]; then
  echo "→ harvesting verdicts and writing the report"
  python3 "$QA/harvest.py" "$PROJECT" || HARVEST_FAILED=1
  echo
fi

echo "→ verifying"
python3 "$QA/verify_run.py" "$PROJECT" || VERIFY_FAILED=1

if [ "$NEEDS_AGENT" = 1 ]; then
  echo
  echo "→ one or more phases need an agent and have NOT run. They are recorded in"
  echo "  phases.json as requested-but-not-done, and verification fails until they are."
  exit 4
fi
[ "${HARVEST_FAILED:-0}" = 1 ] && { echo; echo "→ a scenario has NO result — see report.md."; exit 1; }
[ "${VERIFY_FAILED:-0}" = 1 ] && exit 1
exit 0
