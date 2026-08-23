"""
The write path, exercised as a user exercises it: a real subprocess against a
real vault directory.

**Why a subprocess and not an import.** On 2026-08-23 CI found `record.py`
dying on Windows while printing its own `→`, under cp1252. Recording a
decision is measured as worth 5 of 27 correct answers on the eval set, so the
single most valuable write in the system did not work on an entire operating
system — and every other check was green. Only a real process has a real
console encoding, so only a real process could have caught it.
"""
import json


def read(vault, name):
    p = vault / "02-Projects" / "demo" / name
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


# ------------------------------------------------------------------ decisions

def test_a_decision_records_what_was_turned_down(run_record, vault):
    """**The rejected options are the valuable half.** Without them the next
    agent re-proposes what was already refused."""
    run_record("decision", "demo", "--chose", "vertical layout",
               "--over", "horizontal, grid", "--why", "reads top to bottom")
    rows = read(vault, "Decisions.jsonl")
    assert len(rows) == 1
    assert rows[0]["chose"] == "vertical layout"
    assert rows[0]["over"] == ["horizontal", "grid"]
    assert rows[0]["why"] == "reads top to bottom"
    assert rows[0]["kind"] == "decision"
    assert rows[0]["ts"]


def test_records_are_append_only(run_record, vault):
    """A changed opinion is a new line. Nothing here ever rewrites history —
    that is what makes the record checkable rather than trusted."""
    run_record("decision", "demo", "--chose", "first")
    run_record("decision", "demo", "--chose", "second")
    rows = read(vault, "Decisions.jsonl")
    assert [r["chose"] for r in rows] == ["first", "second"]


def test_an_empty_over_list_is_a_list_not_an_empty_string(run_record, vault):
    """Readers iterate this field. A string would iterate character by
    character and print a decision turned down over 'h', 'o', 'r'."""
    run_record("decision", "demo", "--chose", "only option")
    assert read(vault, "Decisions.jsonl")[0]["over"] == []


# ------------------------------------------------------------------ learnings

def test_a_learning_keeps_its_key_type_confidence_and_files(run_record, vault):
    run_record("learning", "demo", "--key", "svg-cannot-load-a-webfont",
               "--insight", "An SVG shown as an image cannot fetch a webfont.",
               "--type", "pitfall", "--files", "assets/logo.svg, README.md",
               "--confidence", "10")
    r = read(vault, "Learnings.jsonl")[0]
    assert r["key"] == "svg-cannot-load-a-webfont"
    assert r["type"] == "pitfall"
    assert r["confidence"] == 10
    assert r["files"] == ["assets/logo.svg", "README.md"]


def test_an_invalid_learning_type_is_refused(run_record):
    """The four types are a vocabulary. A fifth invented at the command line
    makes the store unsearchable by type."""
    p = run_record("learning", "demo", "--key", "k", "--insight", "i",
                   "--type", "musing", expect=2)
    assert "invalid choice" in (p.stderr + p.stdout)


# ------------------------------------------------------------------ costs

def test_a_cost_tells_the_caller_the_ledger_is_not_updated_yet(run_record, vault):
    """Recording the spend is half the duty; the ledger is generated from a
    registry and does not update itself. Saying so at the moment of writing is
    the only reminder that arrives in time."""
    p = run_record("cost", "demo", "--service", "Example Cloud",
                   "--plan", "Pro", "--monthly", "20")
    assert read(vault, "Costs.jsonl")[0]["service"] == "Example Cloud"
    assert "NOT YET IN THE LEDGER" in p.stdout
    assert "products.json" in p.stdout


# ------------------------------------------------------------------ reading

def test_show_reads_back_every_kind(run_record):
    run_record("decision", "demo", "--chose", "vertical layout", "--over", "grid")
    run_record("learning", "demo", "--key", "a-key", "--insight", "an insight")
    run_record("cost", "demo", "--service", "Example Cloud", "--plan", "Pro",
               "--monthly", "20")
    out = run_record("show", "demo").stdout
    assert "vertical layout" in out
    assert "turned down: grid" in out
    assert "a-key" in out
    assert "Example Cloud" in out


def test_show_on_an_empty_project_says_nothing_recorded_rather_than_failing(run_record):
    out = run_record("show", "demo").stdout
    assert "nothing recorded yet" in out


def test_show_survives_a_corrupt_line(run_record, vault):
    """The store is append-only text. A half-written line from a killed
    process must not make every earlier record unreadable."""
    run_record("decision", "demo", "--chose", "kept")
    f = vault / "02-Projects" / "demo" / "Decisions.jsonl"
    with f.open("a", encoding="utf-8") as fh:
        fh.write('{"kind": "decision", "chose": tru\n')
    out = run_record("show", "demo").stdout
    assert "kept" in out


# ------------------------------------------------------------------ failure

def test_an_unknown_project_fails_and_lists_the_real_ones(run_record):
    """Silently creating the folder would put a stranger's decisions somewhere
    nothing reads them."""
    p = run_record("decision", "not-a-project", "--chose", "x", expect=1)
    assert "no such project" in (p.stdout + p.stderr)
    assert "demo" in (p.stdout + p.stderr)


def test_an_unknown_project_writes_nothing(run_record, vault):
    run_record("decision", "not-a-project", "--chose", "x", expect=1)
    assert not (vault / "02-Projects" / "not-a-project").exists()


# ------------------------------------------------------------------ encoding

def test_the_write_path_survives_a_console_that_cannot_encode_its_own_output(run_record, vault):
    """**The 2026-08-23 Windows regression, pinned on every platform.**

    `record.py` prints `recorded →`. A cp1252 console cannot encode that
    character, and the process died *after* the append but while reporting it —
    a non-zero exit on a write that had actually succeeded, which is the worst
    of both. `PYTHONIOENCODING` reproduces the same console here."""
    p = run_record("decision", "demo", "--chose", "written under cp1252",
                   encoding_env={"PYTHONIOENCODING": "cp1252"})
    assert read(vault, "Decisions.jsonl")[0]["chose"] == "written under cp1252"
    assert p.returncode == 0


def test_non_ascii_content_round_trips(run_record, vault):
    """The vault's own log separator is `·` and its dash is `—`. A record that
    mangles them corrupts the thing it exists to preserve."""
    text = "chose the “Torii” mark — vermilion · #BE3A28"
    run_record("decision", "demo", "--chose", text)
    assert read(vault, "Decisions.jsonl")[0]["chose"] == text
