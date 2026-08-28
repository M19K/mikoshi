#!/usr/bin/env python3
"""
model.py — which model should judge this product, and on what evidence.

    python3 model.py <project>              # decide, print shell exports
    python3 model.py <project> --explain    # decide, and say why in prose
    python3 model.py <project> --json       # for a runner that wants fields

Called by `run.sh` before any phase runs, so **every QA run states what judged
it and how well that thing was measured on this product.**

── Why this exists ──────────────────────────────────────────────────────────

The QA lane picked its model on availability: `qwen2.5vl:7b` locally, because
it needs no key and can run unattended. That is a fine reason to start and a bad
reason to continue, because availability says nothing about whether the model
can see a defect.

**Withdrawn 2026-08-27.** This paragraph used to quote 55% against 91% as the
measured gap. That number came from the set worst affected by the defect gate
that never fired, so it is not evidence and is not repeated here. `decide()`
now refuses to cite any catch rate from a set built before the repair. **A passing assertion from a model that misses nearly half of what it
is looking for is weak evidence**, and a missed defect is silent where a false
alarm is loud. Running QA on a product you already suspect is flawed with that
setup is the worst case for it.

── What it will not do ──────────────────────────────────────────────────────

**It never silently upgrades an unmeasured product to a paid model.** If this
product has no measurement of its own, it says so, keeps the free local lane,
and writes that into the run record — because delta's own finding is that
quality levels do not transfer between products (rank correlation 0.83 for
judging, 0.49 for pointing, and every model dropped a median 22 points moving
between two products). A table measured on somebody else's site is a guess here.

**It never fails the run.** If delta is absent, unmeasured or unreachable,
QA proceeds on the local default with a line in the record saying so. A QA
protocol that cannot start because a routing layer is missing is worse than one
that starts honestly.
"""
import argparse
import json
import os
import subprocess
import sys

VAULT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SR = os.path.join(VAULT, "02-Projects", "delta", "code")

# The lane as it stands: free, local, no credential, and unmeasured.
#
# **Both values are defaults, not decisions.** Somebody else does not
# necessarily have this vision model pulled, and does not necessarily reach
# their Ollama through Docker's host alias. Pinning either is the difference
# between "works here" and "works". [no-hardcoding rule, @owner · 2026-07-16]
LOCAL = {
    "model": os.environ.get("MIKOSHI_QA_MODEL", "qwen2.5vl:7b"),
    "base_url": os.environ.get("MIKOSHI_QA_BASE_URL",
                               "http://host.docker.internal:11434/v1"),
    "key": "ollama",
    "paid": False,
}


def measured_for(project):
    """What delta has actually measured on THIS product, if anything.

    A set is named by whoever built it, so `--name portfolio` and a project
    folder called `gamma` are the same product with two spellings.
    Exact match first, then a unique prefix — and the set actually used is
    always named in the reasons, because silently matching the wrong product's
    measurement is worse than finding none.
    """
    import glob
    runs = os.path.join(SR, "state", f"runs_{project}")
    if not os.path.isdir(runs):
        # `state/runs` has an empty suffix, so an unguarded prefix test matches
        # every project and makes the match ambiguous for all of them.
        candidates = []
        for d in glob.glob(os.path.join(SR, "state", "runs_*")):
            suffix = os.path.basename(d)[5:]
            if not os.path.isdir(d) or not suffix:
                continue
            if project.startswith(suffix) or suffix.startswith(project):
                candidates.append(d)
        if len(candidates) != 1:
            return None
        runs = candidates[0]
    measured_for._set = os.path.basename(runs)[5:]
    best = {}
    for p in sorted(glob.glob(os.path.join(runs, "*.json"))):
        try:
            with open(p) as f:
                s = json.load(f)["summary"]
        except Exception:
            continue
        if s.get("cases", 0) >= 40:
            best[s["model"]] = s
    return best or None


def proxy_up(port=8787):
    import urllib.request
    try:
        # The proxy is started by this process on this machine, so the
        # loopback address is the only one it can be on.
        health = f"http://127.0.0.1:{port}/health"  # allow-hardcode: our own proxy, this machine
        with urllib.request.urlopen(health, timeout=2) as r:
            return json.load(r).get("status") == "ok"
    except Exception:
        return False


def gate_ever_fired(project):
    """Was this exam built by a version of the builder whose defect gate worked?

    **The failure this exists for, found 2026-08-27.** The builder ran ffmpeg's
    `metadata=print` under `-v error`, and that line is written at *info* level,
    so the comparison read an empty result on every call and fell through to
    "everything changed" — the one answer that passes every check. **A gate that
    cannot fail is not a gate**, and this one was the whole basis of the claim
    that a planted defect is visible in the frame it is asserted about.
    Retroactively: 17% of one set's cases assert a defect on a frame where
    nothing changed, and every model was marked wrong for answering truthfully.

    **So a catch rate from a set built before the fix is not evidence, and this
    module's whole job is to hand somebody a catch rate as evidence.**

    Derived, not hardcoded: the builder carries the fix, so a set whose
    directory predates the builder's own mtime was built by the broken one.
    Nothing here needs a date typed into it, and re-running the builder makes
    the answer true by itself.
    """
    builder = os.path.join(SR, "golden", "qa-vision", "build_generic.py")
    exam = os.path.join(SR, "golden", "qa-vision", "sets", project)
    if not os.path.isfile(builder) or not os.path.isdir(exam):
        return None                      # nothing to judge; say nothing
    return os.path.getmtime(exam) >= os.path.getmtime(builder)


def decide(project):
    """Returns (config, reasons). Never raises — QA must always be able to run."""
    reasons = []
    if not os.path.isdir(SR):
        reasons.append("delta is not present in this vault")
        return dict(LOCAL, source="local default"), reasons

    rows = measured_for(project)
    if rows and gate_ever_fired(project) is False:
        # Refuse to cite the number rather than cite it with a caveat. A caveat
        # beside a figure still leaves the figure in the run record, and the
        # run record is read later by somebody who was not here.
        reasons.append(
            f"REFUSING the measured catch rates for '{project}': its exam was "
            f"built before the defect gate was repaired on 2026-08-27, so every "
            f"model was scored against cases that may assert a change on an "
            f"unchanged frame. Rebuild and re-score it, then this chooses on "
            f"evidence again")
        reasons.append(
            f"to rebuild: cd {os.path.relpath(SR, VAULT)} && python3 "
            f"golden/qa-vision/build_generic.py --origin <url> --name {project}")
        return dict(LOCAL, source="local default, measurement withdrawn"), reasons
    if rows:
        used = getattr(measured_for, "_set", None)
        if used and used != project:
            reasons.append(f"using the set named '{used}' — the closest match to "
                           f"'{project}'; rename it if that is the wrong product")
    if not rows:
        # "never measured" and "measured, but those runs can no longer be
        # compared" are different situations with different fixes, and telling
        # a user to build a set they already have wastes their time.
        used = getattr(measured_for, "_set", None)
        stale = os.path.isdir(os.path.join(SR, "state", f"runs_{used}")) if used else False
        if stale:
            reasons.append(
                f"a set for '{used}' exists but has no comparable runs — they were "
                f"quarantined because the exam version they sat cannot be identified")
            reasons.append(
                f"re-score it rather than rebuilding: python3 -m delta.evals "
                f"--set {used} --model <a> --model <b>")
        else:
            reasons.append(
                f"no measurement exists for '{project}' — delta has never "
                f"been pointed at it")
        reasons.append(
            "quality does not transfer between products (measured: every model "
            "dropped a median 22 points, and for pointing the ranking barely "
            "held at all), so another product's table would be a guess here")
        if not stale:
            reasons.append(
                f"to measure it: cd {os.path.relpath(SR, VAULT)} && python3 "
                f"golden/qa-vision/build_generic.py --origin <url> --name {project}")
        return dict(LOCAL, source="local default, unmeasured"), reasons

    # Rank what was measured on this product by catch rate, then by cost.
    ranked = sorted(rows.values(),
                    key=lambda s: (-(s.get("catch_when_answered") or s.get("catch") or 0),
                                   s.get("cost_usd", 0)))
    best = ranked[0]
    catch = best.get("catch_when_answered") or best.get("catch")
    cheapest_good = next(
        (s for s in sorted(ranked, key=lambda s: s.get("cost_usd", 0))
         if (s.get("catch_when_answered") or s.get("catch") or 0) >= catch - 10), best)
    c_catch = cheapest_good.get("catch_when_answered") or cheapest_good.get("catch")
    # The interval has to belong to the number it is bracketing. Until
    # 2026-08-23 this printed the BEST model's interval beside the CHOSEN
    # model's catch rate, so a line reading "62% (74-96 at 95%)" was two
    # different models' measurements spliced into one sentence — and the
    # sentence is the whole reason this file exists.
    ci = cheapest_good.get("catch_ci") or [0, 0]

    reasons.append(
        f"measured on {project}: {cheapest_good['model']} catches {c_catch}% of "
        f"planted defects ({ci[0]}-{ci[1]} at 95%), against {catch}% for the best "
        f"measured model")
    if proxy_up():
        reasons.append("the delta proxy is up, so the run goes through it "
                       "and every call is logged with its cost")
        return {"model": "delta/auto",
                "base_url": "http://host.docker.internal:8787/v1",
                "key": "delta-holds-its-own-key", "paid": True,
                "source": "delta proxy"}, reasons

    reasons.append("the delta proxy is not running, so the model is named "
                   "directly — start it with `python3 -m delta.serve "
                   "--shadow 20` to get cost logging and drift detection")

    # **A local model that wins has to be returned as a local model.** The
    # ladder deliberately includes the free lane's own `local/qwen2.5vl:7b`, and
    # a free model costs nothing, so `cheapest_good` picks it whenever it is
    # within ten points of the best — which is the good outcome and was, until
    # 2026-08-23, a broken one: the id was handed to OpenRouter, marked paid,
    # and given a key that does not exist there. The run would have failed on
    # every call with a model-not-found, on the strength of a measurement that
    # said the free lane was fine.
    if cheapest_good["model"].startswith("local/"):
        return dict(LOCAL, model=cheapest_good["model"][len("local/"):],
                    source="delta measurement — the free local lane, "
                           "measured on this product and good enough"), reasons

    return {"model": cheapest_good["model"],
            "base_url": "https://openrouter.ai/api/v1",
            "key": os.environ.get("OPENROUTER_API_KEY", ""),
            "paid": True, "source": "delta measurement, direct"}, reasons


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--explain", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--record", help="write the decision into this run directory")
    a = ap.parse_args()

    cfg, reasons = decide(a.project)
    payload = {**cfg, "project": a.project, "why": reasons}

    if a.record:
        os.makedirs(a.record, exist_ok=True)
        with open(os.path.join(a.record, "model.json"), "w") as f:
            json.dump(payload, f, indent=1)

    if a.json:
        print(json.dumps(payload, indent=1))
    elif a.explain:
        print(f"QA on {a.project} will be judged by: {cfg['model']}")
        print(f"  via {cfg['base_url']}  ({cfg['source']})")
        for r in reasons:
            print(f"  · {r}")
        if not cfg["paid"]:
            print("  · free and unattended-safe, and NOT measured on this product — "
                  "read a passing assertion as weak evidence")
    else:
        # shell exports, for run.sh to eval
        print(f'MODEL={cfg["model"]}')
        print(f'BASE_URL={cfg["base_url"]}')
        print(f'LLM_KEY={cfg["key"]}')
        print(f'QA_MODEL_SOURCE="{cfg["source"]}"')


if __name__ == "__main__":
    main()
