"""
Key routing is a money rule, not a convenience.

**Measured 2026-08-20:** four products shared one key and **94% of $25.27
could not be attributed to any of them**. Providers attribute spend *by key*
and OpenRouter's history reaches back 30 days, so attribution missed is
attribution gone. The two behaviours that matter here are therefore:
`resolve()` never substitutes a key that happens to exist, and no function in
this module ever returns a value where a label would do.
"""
import pytest

from ledger import keys

FAKE = "sk-or-v1-" + "a" * 40
OTHER = "sk-or-v1-" + "b" * 40


def write_keys(tmp_path, body):
    p = tmp_path / "keys.md"
    p.write_text(body, encoding="utf-8")
    return p


# ------------------------------------------------------------------ parsing
# The file is written by hand, at speed, by a person. A parser that demands a
# schema from a human writing a key list is a parser that silently finds
# nothing — which is what the strict first version did on the first real file.

def test_headings_name_the_provider(tmp_path):
    k = keys.load(write_keys(tmp_path, f"""
## OpenRouter

demo-project: {FAKE}
mikoshi-internal: {OTHER}
"""))
    assert k["openrouter"]["demo-project"] == FAKE
    assert k["openrouter"]["mikoshi-internal"] == OTHER


def test_a_file_with_no_headings_still_parses_by_prefix(tmp_path):
    """A person pasting keys into a list does not write section headings."""
    k = keys.load(write_keys(tmp_path, f"demo-project: {FAKE}\n"))
    assert k["openrouter"]["demo-project"] == FAKE


def test_a_label_ending_in_a_colon_takes_the_next_line(tmp_path):
    k = keys.load(write_keys(tmp_path, f"""
## OpenRouter

demo-project:
{FAKE}
"""))
    assert k["openrouter"]["demo-project"] == FAKE


def test_labels_with_spaces_and_capitals_normalise(tmp_path):
    k = keys.load(write_keys(tmp_path, f"""
## OpenRouter

Demo Project: {FAKE}
"""))
    assert "demo-project" in k["openrouter"]


def test_a_section_label_is_not_mistaken_for_a_key(tmp_path):
    """`For DEMO:` is a heading a human wrote, not a label whose value is on
    the next line."""
    k = keys.load(write_keys(tmp_path, f"""
## OpenRouter

For DEMO:
demo-project: {FAKE}
"""))
    assert list(k["openrouter"]) == ["demo-project"]


def test_an_unreplaced_template_value_is_not_a_key(tmp_path):
    k = keys.load(write_keys(tmp_path, """
## OpenRouter

demo-project: sk-or-v1-REPLACE
"""))
    assert k == {}


def test_a_missing_file_says_what_to_do_and_forbids_the_workaround(tmp_path):
    with pytest.raises(keys.KeyError_) as e:
        keys.load(tmp_path / "absent.md")
    msg = str(e.value)
    assert "keys.example.md" in msg
    # The instruction is the point: the tempting fix is the forbidden one.
    assert "another project" in msg


# ------------------------------------------------------------------ routing

def test_vault_work_bills_internal():
    for name in (None, "", "mikoshi", "vault", "funnel", "05-orchestrator"):
        assert keys.which(name) == keys.INTERNAL
    assert keys.which("demo-project", internal=True) == keys.INTERNAL


def test_a_qa_run_against_a_real_product_bills_that_product(monkeypatch):
    """[@owner · 2026-08-20] `run.sh` already takes the project name; testing a
    product is a cost of building it. Folding QA into the internal bucket would
    make internal the largest line while saying nothing about which product is
    expensive."""
    monkeypatch.setattr(keys, "_products", lambda: {"demo-project"})
    assert keys.which("demo-project", qa=True) == "demo-project"


def test_a_qa_run_against_nothing_in_particular_falls_to_internal(monkeypatch):
    monkeypatch.setattr(keys, "_products", lambda: {"demo-project"})
    assert keys.which("not-a-product", qa=True) == keys.INTERNAL


# ------------------------------------------------------------------ resolve

def _point_at(monkeypatch, path):
    monkeypatch.setattr(keys, "KEYS", path)


def test_resolve_returns_the_label_the_caller_is_entitled_to(monkeypatch, tmp_path):
    p = write_keys(tmp_path, f"## OpenRouter\n\ndemo-project: {FAKE}\n"
                             f"mikoshi-internal: {OTHER}\n")
    _point_at(monkeypatch, p)
    monkeypatch.setattr(keys, "_products", lambda: {"demo-project"})
    assert keys.resolve("openrouter", project="demo-project") == FAKE
    assert keys.resolve("openrouter", internal=True) == OTHER


def test_resolve_never_falls_back_to_a_key_that_happens_to_exist(monkeypatch, tmp_path):
    """**The whole reason this module exists.** A resolver that substitutes is
    how one product's spend lands on another's bill."""
    p = write_keys(tmp_path, f"## OpenRouter\n\nmikoshi-internal: {OTHER}\n")
    _point_at(monkeypatch, p)
    monkeypatch.setattr(keys, "_products", lambda: {"demo-project"})
    with pytest.raises(keys.KeyError_) as e:
        keys.resolve("openrouter", project="demo-project")
    assert OTHER not in str(e.value)          # never a value in an error
    assert "internal=True" in str(e.value)    # say what the deliberate choice is


def test_resolve_refuses_an_unknown_provider_rather_than_guessing(monkeypatch, tmp_path):
    _point_at(monkeypatch, write_keys(tmp_path, f"## OpenRouter\n\ndemo: {FAKE}\n"))
    with pytest.raises(keys.KeyError_):
        keys.resolve("hume", project="demo")


def test_no_error_message_anywhere_contains_a_key(monkeypatch, tmp_path):
    """`status` reports presence and fingerprint only; errors name labels. A
    key that reaches a log or a screenshot is already exposed."""
    p = write_keys(tmp_path, f"## OpenRouter\n\nmikoshi-internal: {OTHER}\n")
    _point_at(monkeypatch, p)
    for kwargs in ({"project": "demo-project"}, {"label": "nope"}):
        with pytest.raises(keys.KeyError_) as e:
            keys.resolve("openrouter", **kwargs)
        assert OTHER not in str(e.value)
        assert FAKE not in str(e.value)


# ------------------------------------------------------------------ hygiene

def test_fingerprint_is_stable_short_and_not_the_key():
    f = keys.fingerprint(FAKE)
    assert f == keys.fingerprint(FAKE)
    assert len(f) == 8
    assert f not in FAKE and FAKE not in f
    assert keys.fingerprint(OTHER) != f


@pytest.mark.parametrize("value,expect", [
    (FAKE, None),
    (FAKE + " ", "whitespace"),
    ("sk-or-v1-aaaa\\bbbb" + "c" * 30, "backslash"),
    ("sk-or-v1-aaa bbb" + "c" * 30, "space"),
    ("Sk-or-v1-" + "a" * 40, "capitalised"),
])
def test_suspicious_names_the_silent_corruptions(value, expect):
    """Both likeliest corruptions look right in the file and fail at the
    provider as a 401 you would then debug in entirely the wrong place."""
    got = keys.suspicious(value)
    if expect is None:
        assert got is None
    else:
        assert got and expect in got


def test_suspicious_never_echoes_the_whole_key():
    bad = "Sk-or-v1-" + "z" * 40
    msg = keys.suspicious(bad)
    assert msg and bad not in msg
