"""
The only record in this system that can be *wrong* in a way anybody can measure.

Decisions and learnings are both written after the fact, so neither can ever be
graded. A prediction can — and grading it needs the claim to exist before the
outcome does, which is why the interesting behaviour here is all about refusing
to let a claim be edited, hedged, or quietly dropped once it is made.
"""
import json

import pytest

from synth import calibration


def read(vault, name="Predictions.jsonl"):
    p = vault / "02-Projects" / "demo" / name
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


@pytest.fixture
def scored(tmp_path, monkeypatch):
    """A store of resolved predictions, written directly."""
    monkeypatch.setattr(calibration, "PROJECTS", tmp_path / "02-Projects")
    d = tmp_path / "02-Projects" / "demo"
    d.mkdir(parents=True)
    f = d / "Predictions.jsonl"

    def add(key, confidence, outcome=None, by_when="2026-01-01"):
        rows = [{"kind": "prediction", "key": key, "claim": key,
                 "by_when": by_when, "confidence": confidence, "ts": "2026-01-01T00:00:00"}]
        if outcome:
            rows.append({"kind": "resolution", "key": key, "outcome": outcome,
                         "ts": "2026-02-01T00:00:00"})
        with f.open("a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
    return add


# ------------------------------------------------------------------ recording

def test_a_prediction_carries_its_date_and_its_confidence(run_record, vault):
    run_record("prediction", "demo", "--key", "ci-goes-red",
               "--claim", "CI will fail again within a month",
               "--by-when", "2026-09-30", "--confidence", "55", "--why", "it has before")
    r = read(vault)[0]
    assert r["kind"] == "prediction"
    assert (r["key"], r["by_when"], r["confidence"]) == ("ci-goes-red", "2026-09-30", 55)
    assert r["why"] == "it has before"


def test_a_date_that_is_not_a_date_is_refused(run_record, vault):
    """A claim with no checkable date can never come due, so it is never
    scored — which is indistinguishable from never having made it."""
    p = run_record("prediction", "demo", "--key", "k", "--claim", "c",
                   "--by-when", "next quarter", "--confidence", "60", expect=1)
    assert "YYYY-MM-DD" in (p.stdout + p.stderr)
    assert not (vault / "02-Projects" / "demo" / "Predictions.jsonl").exists()


@pytest.mark.parametrize("bad", ["-1", "101", "500"])
def test_a_confidence_outside_the_range_is_refused(run_record, bad):
    run_record("prediction", "demo", "--key", "k", "--claim", "c",
               "--by-when", "2026-09-30", "--confidence", bad, expect=1)


def test_resolving_a_claim_nobody_made_is_refused(run_record, vault):
    """Otherwise a resolution invents the claim it scores, after the fact."""
    run_record("prediction", "demo", "--key", "real", "--claim", "c",
               "--by-when", "2026-09-30", "--confidence", "60")
    p = run_record("resolve", "demo", "--key", "invented",
                   "--outcome", "true", expect=1)
    assert "no prediction keyed" in (p.stdout + p.stderr)
    assert "real" in (p.stdout + p.stderr)      # say what does exist


def test_a_resolution_is_appended_never_an_edit(run_record, vault):
    """The claim as originally stated has to survive being wrong."""
    run_record("prediction", "demo", "--key", "k", "--claim", "the original wording",
               "--by-when", "2026-09-30", "--confidence", "90")
    run_record("resolve", "demo", "--key", "k", "--outcome", "false",
               "--note", "it did not happen")
    rows = read(vault)
    assert [r["kind"] for r in rows] == ["prediction", "resolution"]
    assert rows[0]["claim"] == "the original wording"
    assert rows[0]["confidence"] == 90


def test_a_changed_verdict_is_a_new_line_and_the_newest_wins(run_record, vault, monkeypatch):
    run_record("prediction", "demo", "--key", "k", "--claim", "c",
               "--by-when", "2026-09-30", "--confidence", "60")
    run_record("resolve", "demo", "--key", "k", "--outcome", "true")
    run_record("resolve", "demo", "--key", "k", "--outcome", "false",
               "--note", "looked closer")
    monkeypatch.setattr(calibration, "PROJECTS", vault / "02-Projects")
    got = calibration.resolved()
    assert len(got) == 1 and got[0]["outcome"] == "false"


def test_unresolvable_is_an_allowed_outcome(run_record, vault):
    """93% of candidate claims fail a falsifiability filter on a large corpus.
    A store with no way to say so reports a calibration for the small tail it
    happened to phrase sharply."""
    run_record("prediction", "demo", "--key", "k", "--claim", "c",
               "--by-when", "2026-09-30", "--confidence", "60")
    run_record("resolve", "demo", "--key", "k", "--outcome", "unresolvable",
               "--note", "nobody can check this")
    assert read(vault)[1]["outcome"] == "unresolvable"


def test_an_invented_outcome_is_refused(run_record):
    run_record("prediction", "demo", "--key", "k", "--claim", "c",
               "--by-when", "2026-09-30", "--confidence", "60")
    run_record("resolve", "demo", "--key", "k", "--outcome", "sort-of", expect=2)


# ------------------------------------------------------------------ scoring

def test_a_perfect_forecaster_scores_zero(scored):
    scored("a", 100, "true")
    scored("b", 0, "false")
    assert calibration.brier(calibration.resolved()) == 0.0


def test_saying_fifty_to_everything_scores_the_baseline(scored):
    """The number only means something against the do-nothing baseline."""
    scored("a", 50, "true")
    scored("b", 50, "false")
    assert calibration.brier(calibration.resolved()) == pytest.approx(calibration.BASELINE)


def test_confident_and_wrong_scores_worse_than_saying_nothing(scored):
    scored("a", 95, "false")
    assert calibration.brier(calibration.resolved()) > calibration.BASELINE


def test_unresolvable_claims_are_excluded_not_counted_as_misses(scored):
    """Scoring them as wrong would punish exactly the ambitious predictions
    worth making, and reward only the safe ones."""
    scored("a", 90, "true")
    scored("b", 90, "unresolvable")
    assert calibration.brier(calibration.resolved()) == pytest.approx(0.01)
    rep = calibration.report()
    assert rep["scored"] == 1 and rep["unresolvable"] == 1


def test_the_unresolvable_rate_is_always_reported(scored):
    scored("a", 90, "true")
    scored("b", 90, "unresolvable")
    assert calibration.report()["unresolvable_rate"] == pytest.approx(0.5)


def test_no_resolutions_scores_nothing_rather_than_zero(scored):
    """A vault that has just started recording has no score, and must not
    report a flattering one."""
    scored("a", 90)
    assert calibration.brier(calibration.resolved()) is None
    assert calibration.report()["brier"] is None
    assert calibration.report()["resolved"] == 0


def test_the_buckets_expose_overconfidence_the_mean_hides(scored):
    """Being 90% sure and right 50% of the time, balanced by hedging
    elsewhere, is invisible in the headline number."""
    for i in range(4):
        scored(f"hi{i}", 90, "true" if i < 2 else "false")
    for i in range(4):
        scored(f"lo{i}", 10, "true" if i < 2 else "false")
    bands = {b["band"]: b for b in calibration.by_bucket(calibration.resolved())}
    top = bands["80-100%"]
    assert top["stated"] == 90 and top["actual"] == 50


# ------------------------------------------------------------------ what is due

def test_a_claim_past_its_date_and_unscored_shows_as_due(scored):
    scored("old", 60, by_when="2020-01-01")
    scored("future", 60, by_when="2099-01-01")
    due = [r["key"] for r in calibration.open_claims(due_only=True)]
    assert due == ["old"]


def test_a_resolved_claim_stops_being_open(scored):
    scored("old", 60, "true", by_when="2020-01-01")
    assert calibration.open_claims(due_only=True) == []
    assert calibration.report()["due"] == 0


def test_open_claims_are_listed_soonest_first(scored):
    scored("c", 60, by_when="2027-01-01")
    scored("a", 60, by_when="2020-01-01")
    scored("b", 60, by_when="2026-06-01")
    assert [r["key"] for r in calibration.open_claims()] == ["a", "b", "c"]
