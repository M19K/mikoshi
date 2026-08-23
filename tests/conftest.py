"""
Shared fixtures. **The import root is `orchestrator/`, not the repo root** —
the modules use relative imports and are run as `python3 -m funnel.run` from
inside a vault's `05-Orchestrator/`, so the tests import them the same way the
system does. Importing them any other way would test a shape nothing runs.
"""
import pathlib
import shutil
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
ORCH = REPO / "orchestrator"

if str(ORCH) not in sys.path:
    sys.path.insert(0, str(ORCH))


@pytest.fixture(scope="session")
def repo() -> pathlib.Path:
    return REPO


@pytest.fixture(scope="session")
def orch() -> pathlib.Path:
    return ORCH


@pytest.fixture
def vault(tmp_path) -> pathlib.Path:
    """A throwaway vault with the real code installed where a vault keeps it.

    `record.py` and friends derive the vault from their own `__file__`, which
    is the correct design — a vault is a directory, not an environment
    variable — and it means a test that wants the real write path has to build
    a real directory. Copying is cheap and it is the only way the test
    exercises the same path a user does.
    """
    root = tmp_path / "vault"
    (root / "02-Projects" / "demo").mkdir(parents=True)
    shutil.copytree(ORCH, root / "05-Orchestrator",
                    ignore=shutil.ignore_patterns("__pycache__", "state",
                                                  "staged", "digests"))
    return root


@pytest.fixture
def run_record(vault):
    """Invoke `record.py` as a user does — a subprocess, not an import.

    An in-process call would not have caught the failure this fixture exists
    for: on 2026-08-23 `record.py` died on Windows printing its own arrow,
    under cp1252, in a console. Only a real process has a real encoding.
    """
    def _run(*args, encoding_env=None, expect=0):
        env = None
        if encoding_env:
            import os
            env = dict(os.environ, **encoding_env)
        p = subprocess.run(
            [sys.executable, str(vault / "05-Orchestrator" / "record.py"), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env)
        if expect is not None:
            assert p.returncode == expect, (
                f"expected exit {expect}, got {p.returncode}\n"
                f"stdout:\n{p.stdout}\nstderr:\n{p.stderr}")
        return p
    return _run
