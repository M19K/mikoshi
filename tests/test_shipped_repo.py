"""
Invariants of the artifact itself, as opposed to any one module.

`orchestrator/` is **generated** from a vault by `extract_system.py`. That
makes it a build output, and a build output needs its own checks: every one of
these has a matching failure in this project's history.

**If a test here fails, the fix belongs in the vault, not in this repo.** Three
times on 2026-08-23 a fix was made in the extracted tree and the next
extraction silently reverted it — including one already reported as done.
"""
import pathlib
import re
import subprocess
import sys

import pytest

TEXT = {".py", ".md", ".txt", ".json", ".toml", ".yml", ".yaml", ".sh"}
SKIP = {".git", "__pycache__", ".venv", "node_modules", ".pytest_cache"}


def files(root, suffixes=TEXT):
    return [p for p in sorted(root.rglob("*"))
            if p.is_file() and not set(p.parts) & SKIP and p.suffix in suffixes]


# ------------------------------------------------------------------ it compiles

def test_every_shipped_module_compiles(repo):
    """The cheapest check there is, and it has caught real breakage."""
    p = subprocess.run([sys.executable, "-m", "compileall", "-q",
                        str(repo / "orchestrator"), str(repo / "init.py"),
                        str(repo / "doctor.py"), str(repo / "verify.py")],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr


def test_the_entry_points_a_reader_is_told_to_run_all_exist(repo):
    for name in ("init.py", "doctor.py", "verify.py", "README.md",
                 "BOOTSTRAP_FOR_AGENTS.md", "LICENSE", "pyproject.toml"):
        assert (repo / name).is_file(), name


def test_every_python_package_is_actually_importable(repo):
    """`rsshub/` is deliberately absent from this list — it is a
    docker-compose deployment, not a Python package, and giving it an
    `__init__.py` to satisfy a test would be the test changing the code."""
    for pkg in ("funnel", "synth", "jobs", "ledger", "qa"):
        d = repo / "orchestrator" / pkg
        assert d.is_dir(), pkg
        assert (d / "__init__.py").is_file(), f"{pkg} is not importable"
    assert (repo / "orchestrator" / "rsshub" / "docker-compose.yml").is_file()


# ------------------------------------------------------------------ nothing personal

def test_the_owners_name_survives_nowhere_in_what_ships(repo):
    """The extractor's own leak check, asserted from outside it.

    It earned its place on its first run by catching two the prose rules
    missed: a constant named for the owner, and a shouted label."""
    hits = [str(p.relative_to(repo)) for p in files(repo)
            if re.search(r"\bmaaz\b", p.read_text(encoding="utf-8", errors="replace"), re.I)]
    assert hits == [], hits


def test_the_cost_registry_ships_as_an_example_not_as_the_owners(repo):
    """Rewriting product names left one product's database plan, domain and
    reasons for paying in the published registry. The structure ships; the
    subscriptions do not."""
    import json
    reg = json.loads((repo / "orchestrator" / "ledger" / "products.json")
                     .read_text(encoding="utf-8"))
    assert list(reg["products"]) == ["example-product"], list(reg["products"])
    assert list(reg["pools"]) == ["openrouter"], list(reg["pools"])


def test_no_key_material_ships(repo):
    pat = re.compile(r"sk-(or-v1|ant|proj)-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}")
    hits = []
    for p in files(repo):
        body = p.read_text(encoding="utf-8", errors="replace")
        if "re.compile" in body or "PATTERNS" in body:
            continue                       # a detector contains what it detects
        if pat.search(body):
            hits.append(str(p.relative_to(repo)))
    assert hits == [], hits


def test_the_corpus_the_caches_and_the_outputs_are_not_in_the_repo(repo):
    """The system ships empty. What one person's funnel has filed is content,
    and content is not the system."""
    for d in ("state", "staged", "digests", "01-Knowledge Base", "02-Projects",
              "00-Inbox"):
        assert not (repo / d).exists(), d
    assert not (repo / "orchestrator" / "ledger" / "keys.md").exists()


def test_no_database_or_record_file_ships(repo):
    for suffix in (".db", ".sqlite", ".sqlite3", ".jsonl"):
        found = [str(p.relative_to(repo)) for p in repo.rglob(f"*{suffix}")
                 if not set(p.parts) & SKIP]
        assert found == [], found


# ------------------------------------------------------------------ the derivation

def test_nothing_in_the_generated_tree_claims_to_be_hand_maintained(repo):
    """`orchestrator/` is rebuilt whole on every extraction. A file there that
    exists only in the repo is a fix that is about to be reverted."""
    extract = repo / "orchestrator" / "jobs" / "extract_system.py"
    assert not extract.exists(), (
        "extract_system.py builds this repo, so it is meaningless inside it — "
        "and it necessarily contains the patterns the leak check looks for.")


@pytest.mark.parametrize("doc", ["README.md", "BOOTSTRAP_FOR_AGENTS.md"])
def test_the_documents_a_stranger_reads_first_are_not_empty(repo, doc):
    body = (repo / doc).read_text(encoding="utf-8")
    assert len(body) > 2000, doc


def test_the_readme_states_the_gaps_rather_than_hiding_them(repo):
    """A generous self-assessment is a broken instrument. The README's promise
    is that it says what is missing; this is the check on that promise."""
    body = (repo / "README.md").read_text(encoding="utf-8").lower()
    assert "test" in body
    assert any(w in body for w in ("not", "no ", "missing", "gap", "limitation"))


def test_the_licence_is_the_one_the_project_claims(repo):
    assert "MIT" in (repo / "LICENSE").read_text(encoding="utf-8")
    assert 'license = { text = "MIT" }' in (repo / "pyproject.toml").read_text(encoding="utf-8")
