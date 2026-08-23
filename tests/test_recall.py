"""
The read path — what an agent actually gets back when it asks.

Measured on the 27-question set: **recorded decisions are worth 18 points of
grounding**, and learnings support the sentences rather than find the files.
Both stores were invisible to retrieval until these two modules existed, because
retrieval was built for prose and these are the vault's most structured records.

The behaviour worth pinning is the *ranking*, not the plumbing. "Why not the
Keychain" must surface the decision that refused the Keychain — not every
decision that happens to mention keys.
"""
import json
import pathlib

import pytest

from synth import decisions, learnings


@pytest.fixture
def projects(tmp_path, monkeypatch):
    """Two projects with records, standing in for the real vault."""
    monkeypatch.setattr(decisions, "VAULT", tmp_path)
    monkeypatch.setattr(decisions, "PROJECTS", tmp_path / "02-Projects")
    monkeypatch.setattr(learnings, "VAULT", tmp_path)
    monkeypatch.setattr(learnings, "PROJECTS", tmp_path / "02-Projects")

    def add(project, name, row):
        d = tmp_path / "02-Projects" / project
        d.mkdir(parents=True, exist_ok=True)
        with (d / name).open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    return add


def decide(add, project, chose, over, why, ts="2026-08-20T10:00:00"):
    add(project, "Decisions.jsonl",
        {"kind": "decision", "chose": chose, "over": over, "why": why, "ts": ts})


def learn(add, project, key, insight, confidence=7, ts="2026-08-20T10:00:00"):
    add(project, "Learnings.jsonl",
        {"kind": "learning", "key": key, "insight": insight, "type": "pitfall",
         "confidence": confidence, "ts": ts})


# ------------------------------------------------------------------ decisions

def test_a_rejected_option_is_findable_by_its_own_name(projects):
    """**The thing a brain that stores pages structurally cannot do.** Nobody
    writes a note about the option they did not take, so the rejection has to
    be a field."""
    decide(projects, "alpha", "a plaintext file the owner writes",
           ["the macOS Keychain", "an env var"],
           "a routine that stops for a password has already failed")
    hits = decisions.rejected("keychain")
    assert len(hits) == 1
    assert "Keychain" in " ".join(hits[0]["over"])
    assert "stops for a password" in hits[0]["why"]


def test_a_match_in_the_rejected_list_outranks_a_match_in_the_chosen_one(projects):
    """Asking "why not X" should surface the decision that refused X, not every
    decision that happens to mention X."""
    decide(projects, "alpha", "keychain storage everywhere", [], "unrelated",
           ts="2026-08-21T10:00:00")
    decide(projects, "alpha", "a plaintext file", ["keychain"],
           "needs a human present", ts="2026-08-19T10:00:00")
    top = decisions.rejected("keychain")[0]
    assert top["chose"] == "a plaintext file"


def test_every_record_carries_the_file_it_came_from(projects):
    """Grounding means citable. A claim with no file behind it is a claim."""
    decide(projects, "alpha", "a plaintext file", ["the macOS Keychain"], "why")
    row = decisions.rejected("keychain")[0]
    assert row["project"] == "alpha"
    # Compare path *parts*, not a string ending. The first version of this
    # assertion matched "alpha/Decisions.jsonl" and failed on Windows, where
    # the separator is a backslash — a test written against one platform,
    # which is the whole reason the matrix exists.
    assert pathlib.PurePath(row["source"]).parts[-2:] == ("alpha", "Decisions.jsonl")


def test_records_from_every_project_are_searched_by_default(projects):
    decide(projects, "alpha", "a", ["keychain"], "w")
    decide(projects, "beta", "b", ["keychain"], "w")
    assert {r["project"] for r in decisions.rejected("keychain")} == {"alpha", "beta"}


def test_a_project_filter_narrows_to_that_project(projects):
    decide(projects, "alpha", "a", ["keychain"], "w")
    decide(projects, "beta", "b", ["keychain"], "w")
    assert {r["project"] for r in decisions.rejected("keychain", project="beta")} \
        == {"beta"}


def test_an_empty_query_returns_nothing_rather_than_everything(projects):
    decide(projects, "alpha", "a", ["keychain"], "w")
    assert decisions.rejected("") == []
    assert decisions.rejected("a of") == []      # stop-length words only


def test_no_match_is_not_the_same_as_never_considered(projects):
    """The distinction the CLI states in words, held here as behaviour: an
    empty result is an absence of record, not evidence of absence."""
    decide(projects, "alpha", "a", ["keychain"], "w")
    assert decisions.rejected("kubernetes") == []


def test_the_prompt_block_shows_chose_over_and_because(projects):
    """The model is asked to cite by index, so the index has to be there."""
    decide(projects, "alpha", "a plaintext file", ["the macOS Keychain"],
           "needs a human present")
    block = decisions.format_for_prompt(decisions.rejected("keychain"))
    assert "[D1]" in block
    assert "CHOSE:" in block and "OVER:" in block and "BECAUSE:" in block
    assert "macOS Keychain" in block


def test_an_empty_result_formats_to_an_empty_block_not_a_heading(projects):
    assert decisions.format_for_prompt([]) == ""


def test_a_corrupt_line_does_not_hide_the_records_around_it(projects, tmp_path):
    decide(projects, "alpha", "kept", ["keychain"], "w")
    with (tmp_path / "02-Projects" / "alpha" / "Decisions.jsonl").open(
            "a", encoding="utf-8") as f:
        f.write("{ half a line\n")
    assert decisions.rejected("keychain")[0]["chose"] == "kept"


def test_decisions_load_newest_first(projects):
    decide(projects, "alpha", "older", [], "", ts="2026-01-01T00:00:00")
    decide(projects, "alpha", "newer", [], "", ts="2026-08-01T00:00:00")
    assert [r["chose"] for r in decisions.load()] == ["newer", "older"]


# ------------------------------------------------------------------ learnings

def test_the_key_outweighs_the_prose(projects):
    """The key is a slug someone deliberately chose to name the finding. A
    match there is a much stronger signal than a word in a long insight."""
    learn(projects, "alpha", "unrelated-finding",
          "mentions the token budget in passing, at length, repeatedly")
    learn(projects, "alpha", "reasoning-models-eat-the-token-budget",
          "they emit thousands of hidden tokens before answering")
    assert learnings.matching("token budget")[0]["key"] \
        == "reasoning-models-eat-the-token-budget"


def test_a_confident_finding_outranks_a_hedged_one_at_equal_match(projects):
    learn(projects, "alpha", "same-words-here", "identical", confidence=3)
    learn(projects, "beta", "same-words-here", "identical", confidence=10)
    assert learnings.matching("same words")[0]["project"] == "beta"


def test_the_top_n_cap_holds(projects):
    """No relevance floor, deliberately — measured 2026-08-23, one record in
    270 scored 2 or less. The cap is what does the work."""
    for i in range(10):
        learn(projects, "alpha", f"token-budget-finding-{i}", "x")
    assert len(learnings.matching("token budget")) == 4
    assert len(learnings.matching("token budget", top=2)) == 2


def test_the_prompt_block_carries_the_confidence_so_it_can_be_weighed(projects):
    learn(projects, "alpha", "a-hard-won-thing", "it broke this way", confidence=9)
    block = learnings.format_for_prompt(learnings.matching("hard won"))
    assert "[L1]" in block
    assert "confidence 9/10" in block
    assert "a-hard-won-thing" in block


def test_learnings_are_findable_across_projects_which_is_the_point(projects):
    """A trap one project fell into is worth surfacing to another before it
    falls in too."""
    learn(projects, "alpha", "svg-cannot-load-a-webfont", "an SVG as an image")
    assert learnings.matching("webfont", project="beta") == []
    assert learnings.matching("webfont")[0]["project"] == "alpha"


def test_an_absent_store_is_empty_not_an_error(projects):
    """A vault where nobody has recorded anything yet still answers."""
    assert learnings.load() == []
    assert learnings.matching("anything") == []
    assert decisions.load() == []
