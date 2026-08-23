"""
The signal/noise loop. Two rules, both of which are about the loop *refusing
to fire*, which is the part nothing else in the system would notice was wrong.

A preference learned from three clicks is a guess with a mechanism attached:
it fires wrongly, gets distrusted, and is then worse than not existing. And
one-sided examples teach a model to answer one way — the same fault that made
another project's first golden set separate nobody.
"""
import json

import pytest

from funnel import learn


@pytest.fixture
def verdicts(tmp_path, monkeypatch):
    """Point the store at a temp file. Returns a writer."""
    path = tmp_path / "verdicts.jsonl"
    monkeypatch.setattr(learn, "VERDICTS", path)

    def add(item_id, verdict, title=None, why="", domain="tooling"):
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "item_id": item_id, "title": title or f"item {item_id}",
                "source": "test", "domain": domain, "verdict": verdict,
                "why": why, "by": "@owner", "at": "2026-08-23T00:00:00+00:00",
            }) + "\n")
    return add


def fill(add, signal, noise, start=0):
    for i in range(signal):
        add(f"s{start + i}", "signal")
    for i in range(noise):
        add(f"n{start + i}", "noise")


# ------------------------------------------------- the floor, and why it holds

def test_no_verdicts_at_all_is_normal_not_an_error(verdicts):
    """A funnel with no verdicts behaves exactly as it did before this file
    existed. Callers must treat the empty string as ordinary."""
    assert learn.examples() == ""
    assert learn.ready() is False
    assert learn.status()["total"] == 0


def test_one_short_of_the_floor_still_teaches_nothing(verdicts):
    fill(verdicts, learn.MIN_VERDICTS // 2, learn.MIN_VERDICTS - 1 - learn.MIN_VERDICTS // 2)
    assert len(learn.latest()) == learn.MIN_VERDICTS - 1
    assert learn.examples() == ""
    assert learn.status()["need"] == 1


def test_at_the_floor_with_both_sides_it_fires(verdicts):
    fill(verdicts, learn.MIN_VERDICTS // 2, learn.MIN_VERDICTS - learn.MIN_VERDICTS // 2)
    block = learn.examples()
    assert block
    assert "KEPT as signal" in block
    assert "DISCARDED as noise" in block


def test_a_one_sided_set_never_fires_however_large(verdicts):
    """**A set with no counter-examples is worse than none.** Reaching the
    floor is necessary and not sufficient."""
    fill(verdicts, learn.MIN_VERDICTS + 10, 0)
    assert learn.ready() is True          # the count is there
    assert learn.examples() == ""         # and it still teaches nothing


def test_the_other_one_sided_set_is_refused_too(verdicts):
    fill(verdicts, 0, learn.MIN_VERDICTS + 10)
    assert learn.examples() == ""


# ------------------------------------------------------------------ the store

def test_the_newest_verdict_for_an_item_wins(verdicts):
    """Nothing is ever rewritten: a changed opinion is a new line."""
    verdicts("x1", "signal", why="looked useful")
    verdicts("x1", "noise", why="turned out to be an ad")
    assert len(learn.latest()) == 1
    assert learn.latest()["x1"]["verdict"] == "noise"
    assert learn.latest()["x1"]["why"] == "turned out to be an ad"


def test_a_changed_opinion_does_not_inflate_the_count(verdicts):
    """Counting lines rather than items would let one item marked ten times
    unlock the floor on its own."""
    for _ in range(learn.MIN_VERDICTS + 5):
        verdicts("x1", "signal")
    assert learn.status()["total"] == 1
    assert learn.ready() is False


def test_a_corrupt_line_is_skipped_not_fatal(verdicts, tmp_path):
    verdicts("x1", "signal")
    with (tmp_path / "verdicts.jsonl").open("a", encoding="utf-8") as f:
        f.write("{not json\n\n")
    verdicts("x2", "noise")
    assert set(learn.latest()) == {"x1", "x2"}


def test_status_counts_both_sides_and_says_how_many_are_missing(verdicts):
    fill(verdicts, 3, 2)
    s = learn.status()
    assert (s["signal"], s["noise"], s["total"]) == (3, 2, 5)
    assert s["need"] == learn.MIN_VERDICTS - 5
    assert s["ready"] is False


# ------------------------------------------------------------------ the block

def test_the_block_is_capped_so_examples_do_not_drown_the_item(verdicts):
    """Examples compete with the item being judged for the model's attention,
    and a long tail of near-duplicates teaches nothing a few clear cases do."""
    fill(verdicts, 40, 40)
    block = learn.examples()
    kept = block.split("KEPT as signal:")[1].split("DISCARDED")[0]
    assert kept.strip().count("\n") + 1 == learn.SHOW_EACH


def test_the_block_shows_the_reader_their_own_verdicts_not_advice(verdicts):
    """The framing matters: these are the reader's judgements, not guidance
    written for the model, and the prompt says so."""
    fill(verdicts, 15, 15)
    block = learn.examples()
    assert "their own verdicts" in block
    assert "not guidance written for you" in block


def test_a_marked_item_carries_its_reason_into_the_block(verdicts):
    fill(verdicts, 15, 14)
    verdicts("why1", "noise", title="Ten AI tools you must try",
             why="listicle, no implementation detail")
    block = learn.examples()
    assert "Ten AI tools you must try" in block
    assert "listicle, no implementation detail" in block


def test_an_invalid_verdict_is_refused(verdicts):
    """Two words, deliberately. A third makes the store unpartitionable."""
    with pytest.raises(SystemExit):
        learn.mark("x1", "maybe")
