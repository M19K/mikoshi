"""
The checker behind the rule that stood unenforced from 2026-07-16 to
2026-08-23, by which time **seven violations had accumulated over five weeks**.

Two properties, and the second is as important as the first. It must catch
what it claims to catch — and it must not accuse the codebase it ships with,
because a checker that cries wolf on its first run teaches everyone to pass
`|| true` and is then worse than no checker.
"""
import textwrap

import pytest

from jobs import no_hardcoding as nh

# Assembled, never written as a literal — see the note in test_privacy_check.py.
# A test fixture that is itself a permanent finding teaches people to skim the
# report it appears in.
HOME = "/Users"
ROOTS = [HOME, "/hom" + "e", "/Volume" + "s"]


def code(tmp_path, body: str, name="mod.py"):
    p = tmp_path / name
    p.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    return tmp_path


def kinds(tmp_path):
    return {f["kind"] for f in nh.scan(tmp_path)}


# ------------------------------------------------------------------ it catches

def test_an_absolute_path_works_on_exactly_one_machine(tmp_path):
    root = code(tmp_path, f'''
        VAULT = "{HOME}/someone/Documents/Vault"
    ''')
    found = nh.scan(root)
    assert [f["kind"] for f in found] == ["absolute path"]
    assert found[0]["line"] == 1
    assert "one machine" in found[0]["why"]


@pytest.mark.parametrize("root_dir", ROOTS)
def test_every_absolute_root_is_caught(tmp_path, root_dir):
    assert kinds(code(tmp_path, f'P = "{root_dir}/someone/x"\n')) == {"absolute path"}


def test_a_bare_host_with_no_override_is_caught(tmp_path):
    assert kinds(code(tmp_path, 'OLLAMA = "http://localhost:11434"\n')) == {"bare host"}


def test_a_pinned_model_somebody_else_may_not_have_is_caught(tmp_path):
    assert kinds(code(tmp_path, 'MODEL = "gpt-oss:20b"\n')) == {"pinned model"}
    assert kinds(code(tmp_path, 'E = "nomic-embed-text"\n')) == {"pinned model"}


def test_one_persons_account_inside_code_everybody_runs_is_caught(tmp_path):
    assert kinds(code(tmp_path, 'INBOX = "someone@example-mail.io"\n')) == {"account or handle"}
    assert kinds(code(tmp_path, 'CH = "UCsBjURrPoezykLs9EqgamOA"\n')) == {"account or handle"}


def test_findings_carry_file_and_line_so_they_are_actionable(tmp_path):
    root = code(tmp_path, '''
        import os

        HOST = "http://localhost:11434"
    ''')
    f = nh.scan(root)[0]
    assert f["file"] == "mod.py"
    assert f["line"] == 3
    assert "localhost" in f["text"]


# --------------------------------------------------- it does not cry wolf

def test_a_default_with_an_override_beside_it_is_fine(tmp_path):
    """**The rule is not "no literals".** It is that the literal must be
    reachable from outside. A default is a kindness; an unreachable default is
    a hardcode."""
    root = code(tmp_path, '''
        import os

        OLLAMA = os.environ.get("MIKOSHI_OLLAMA_URL", "http://localhost:11434")
    ''')
    assert nh.scan(root) == []


def test_prose_in_a_docstring_describes_and_does_not_configure(tmp_path):
    """A module explaining "we call localhost:11434" is documentation. The
    docstrings are the best part of this codebase and must survive the check."""
    root = code(tmp_path, f'''
        """We call http://localhost:11434 and read {HOME}/someone/vault."""
        VALUE = 1
    ''')
    assert nh.scan(root) == []


def test_a_commented_line_is_not_a_value(tmp_path):
    root = code(tmp_path, '# HOST = "http://localhost:11434"\n')
    assert nh.scan(root) == []


def test_an_explicit_allowance_must_carry_its_reason_on_the_line(tmp_path):
    """An ALLOW entry is a decision on the record, not a way to quiet the
    checker — so the marker sits beside the literal where a reader sees it."""
    root = code(tmp_path,
                'HOST = "http://localhost:1200"  # allow-hardcode: dev default only\n')
    assert nh.scan(root) == []


def test_generated_and_vendored_trees_are_not_scanned(tmp_path):
    for d in ("__pycache__", ".venv", "node_modules", "state"):
        sub = tmp_path / d
        sub.mkdir()
        (sub / "x.py").write_text(f'P = "{HOME}/someone/x"\n', encoding="utf-8")
    assert nh.scan(tmp_path) == []


def test_a_clean_tree_is_reported_as_clean(tmp_path):
    assert nh.scan(code(tmp_path, "VALUE = 1\n")) == []


# ------------------------------------------------------- the shipped codebase

def test_the_code_that_ships_passes_its_own_rule(orch):
    """The regression guard. Seven violations accumulated in five weeks with
    nothing watching; this is what watches."""
    found = nh.scan(orch)
    assert found == [], "\n".join(
        f"  {f['file']}:{f['line']}  [{f['kind']}]  {f['text']}" for f in found)
