"""
The two silences.

**The failure this module exists to prevent.** On 2026-08-20 the newsletter
tier produced nothing and reported a healthy run, because the parking step
parks files from disk and there was no file to park. Nine subscriptions went
missing from the corpus and the log looked clean.

    quiet     the routine ran, the connector answered, there was nothing.
    ABSENT    no drop file at all — the routine did not run, or ran without
              the connector attached. An outage.

They must never print the same line, and `status --strict` must exit non-zero
on the second. Everything below is that distinction.
"""
import datetime as dt
import json
import os
import subprocess
import sys

import pytest

from funnel import connectors


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(connectors, "STATE", tmp_path)

    def drop(name, records=None, *, age_days=0, raw=None):
        d = tmp_path / name
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{name}-{age_days}.json"
        f.write_text(json.dumps(raw if raw is not None else (records or [])),
                     encoding="utf-8")
        if age_days:
            when = (dt.datetime.now() - dt.timedelta(days=age_days)).timestamp()
            os.utime(f, (when, when))
        return f
    return drop


# ------------------------------------------------------------------ the three states

def test_a_connector_that_never_ran_is_absent_not_quiet(state):
    h = connectors.health("email")
    assert h["state"] == "ABSENT"
    assert h["days"] is None
    assert "never run" in h["why"]


def test_an_empty_drop_file_is_quiet_and_that_is_healthy(state):
    """**The distinction, in one test.** An empty drop is the routine saying
    "I looked and there was nothing" — which is a good answer, and the only
    thing separating it from an outage."""
    state("email", [])
    h = connectors.health("email")
    assert h["state"] == "quiet"
    # Zero, never negative. A file written a moment ago can carry an mtime a
    # fraction ahead of the clock it is compared to, and `timedelta.days`
    # floors — so this read `-1d ago` on Windows until it was clamped.
    assert h["days"] == 0
    assert "looked and there was nothing" in h["why"]


def test_a_drop_from_the_future_reports_zero_rather_than_a_negative_age(state, monkeypatch):
    """Clock skew is not an outage and must not print as one."""
    f = state("email", [])
    import os
    ahead = (dt.datetime.now() + dt.timedelta(hours=6)).timestamp()
    os.utime(f, (ahead, ahead))
    h = connectors.health("email")
    assert h["days"] == 0
    assert h["state"] == "quiet"


def test_records_waiting_is_flowing(state):
    state("email", [{"id": "1", "body": "x"}, {"id": "2", "body": "y"}])
    h = connectors.health("email")
    assert h["state"] == "flowing"
    assert "2 record(s)" in h["why"]


# ------------------------------------------------------------------ the thresholds

def test_staleness_is_per_connector_not_global(state):
    """Mail arrives daily; a Drive folder may genuinely go a week without an
    edit. One threshold for both would either cry wolf or say nothing."""
    state("email", [], age_days=5)
    state("drive", [], age_days=5)
    assert connectors.health("email")["state"] == "ABSENT"
    assert connectors.health("drive")["state"] == "quiet"


def test_a_stale_drop_reports_the_age_and_the_limit(state):
    state("email", [], age_days=9)
    h = connectors.health("email")
    assert h["state"] == "ABSENT"
    assert h["days"] == 9
    assert "limit 2" in h["why"]
    assert "not a quiet week" in h["why"]


def test_a_drop_inside_the_limit_is_not_an_outage(state):
    state("meetings", [], age_days=6)      # limit is 7
    assert connectors.health("meetings")["state"] == "quiet"


def test_freshness_is_the_files_date_not_the_newest_item_inside_it(state):
    """Deliberate: an old item in a fresh drop still means the routine ran."""
    state("email", [{"id": "1", "date": "2019-01-01", "body": "old"}])
    assert connectors.health("email")["state"] == "flowing"


# ------------------------------------------------------------------ envelopes

@pytest.mark.parametrize("raw", [
    [{"id": "1"}, {"id": "2"}],
    {"items": [{"id": "1"}, {"id": "2"}]},
    {"messages": [{"id": "1"}, {"id": "2"}]},
    {"documents": [{"id": "1"}, {"id": "2"}]},
    {"meetings": [{"id": "1"}, {"id": "2"}]},
    {"records": [{"id": "1"}, {"id": "2"}]},
])
def test_every_envelope_a_connector_writes_is_accepted(state, raw):
    """Three connectors write three different envelopes and arguing with them
    is not worth a schema."""
    state("email", raw=raw)
    assert len(connectors.load_drops("email")) == 2


def test_a_bare_object_is_treated_as_one_record(state):
    state("email", raw={"id": "1", "body": "just the one"})
    assert len(connectors.load_drops("email")) == 1


def test_a_corrupt_drop_does_not_take_the_good_ones_with_it(state):
    state("email", [{"id": "1"}])
    (connectors.STATE / "email" / "broken.json").write_text("{ not json",
                                                            encoding="utf-8")
    assert len(connectors.load_drops("email")) == 1


def test_each_record_remembers_which_file_it_came_from(state):
    """Provenance: an item that turns out to be wrong has to be traceable to
    the run that produced it."""
    f = state("email", [{"id": "1"}])
    assert connectors.load_drops("email")[0]["_file"] == f.name


def test_drops_are_read_oldest_first(state):
    state("email", [{"id": "a"}], age_days=0)
    state("email", [{"id": "b"}], age_days=3)
    ids = [r["id"] for r in connectors.load_drops("email")]
    assert ids == sorted(ids)


# ------------------------------------------------------------------ identity

def test_item_ids_are_stable_and_distinguish_records():
    assert connectors.item_id("mail", "a", "b") == connectors.item_id("mail", "a", "b")
    assert connectors.item_id("mail", "a", "b") != connectors.item_id("mail", "a", "c")
    assert connectors.item_id("mail", "a").startswith("mail-")


def test_empty_parts_do_not_collide_with_present_ones():
    assert connectors.item_id("mail", "a", None) != connectors.item_id("mail", "a", "b")


# ------------------------------------------------------------------ the gate

def _status(tmp_path, strict):
    """Run the command a scheduled routine runs, in a real process — the exit
    code is the contract, and only a process has one."""
    argv = ["connectors", "status"] + (["--strict"] if strict else [])
    code = (
        "import pathlib, sys\n"
        "from funnel import connectors\n"
        f"connectors.STATE = pathlib.Path({str(tmp_path)!r})\n"
        f"sys.argv = {argv!r}\n"
        "connectors.main()\n"
    )
    from conftest import ORCH
    return subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          cwd=str(ORCH))


def test_strict_status_fails_loudly_when_a_source_is_absent(tmp_path):
    """A scheduled run must fail rather than pass quietly. This is the exit
    code that would have caught the nine missing newsletters."""
    p = _status(tmp_path, strict=True)
    assert p.returncode == 1
    assert "ABSENT" in p.stdout
    assert "not healthy" in p.stdout


def test_status_without_strict_reports_but_does_not_fail(tmp_path):
    p = _status(tmp_path, strict=False)
    assert p.returncode == 0
    assert "ABSENT" in p.stdout


def test_strict_status_passes_when_every_connector_has_dropped(tmp_path):
    for name in ("newsletters", "email", "meetings", "drive"):
        d = tmp_path / name
        d.mkdir(parents=True)
        (d / "run.json").write_text("[]", encoding="utf-8")
    p = _status(tmp_path, strict=True)
    assert p.returncode == 0, p.stdout
    # The word appears in the header explaining the states; no ROW may carry it.
    rows = [l for l in p.stdout.splitlines() if l.startswith("  ")]
    assert rows and not any("ABSENT" in l for l in rows)
    assert all("quiet" in l for l in rows)
