"""
The check that runs before anything leaves this machine.

**It exists because the rule failed twice without it.** One project shipped
screenshots carrying the local machine's IP address; another built its vision
test set from product photographs that included pictures of the owner. Neither
was noticed at push time; both were noticed afterwards, by him.

Only the cheap surfaces are exercised here — text, filenames, git history.
Images and video need OCR and a local vision model, which no CI runner has,
and **that limit is stated rather than implied**: a suite that quietly skips
half a checker reports a confidence it has not earned.
"""
import subprocess

import pytest

from jobs import privacy_check as pc

# **Every fixture below is assembled at run time, never written as a literal.**
# A test file for a detector would otherwise be a permanent finding in the
# detector's own report — and a checker whose report is mostly its own test
# fixtures is a checker people learn to skim. The values are real-shaped when
# the test runs, which is the only place it matters.
KEYS = [
    "sk-or-v1-" + "a" * 40,
    "sk-ant-" + "b" * 40,
    "ghp_" + "c" * 36,
    "AKIA" + "D" * 16,
    "xoxb-" + "1" * 20,
]


def tree(tmp_path, files: dict):
    for name, body in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return tmp_path


def kinds(root, names=()):
    return {f["kind"] for f in pc.scan_text(root, names)}


# ------------------------------------------------------------------ it catches

@pytest.mark.parametrize("key", KEYS)
def test_every_key_shape_is_caught(tmp_path, key):
    assert kinds(tree(tmp_path, {"config.py": f'KEY = "{key}"\n'})) == {"api key"}


def test_a_real_email_address_is_caught(tmp_path):
    addr = "jane.doe" + chr(64) + "acme-corp.net"
    assert kinds(tree(tmp_path, {"README.md": f"write to {addr}\n"})) == {"email address"}


def test_a_private_ip_is_caught(tmp_path):
    """**One of the two real leaks, in one line.** A project shipped
    screenshots carrying the local machine's address in a corner nobody
    looks at."""
    for ip in ("192.168" + ".1.14", "10.0" + ".0.7", "172.16" + ".4.9"):
        assert kinds(tree(tmp_path, {"notes.md": f"host {ip}\n"})) == {"private IP"}


def test_a_home_directory_path_is_caught(tmp_path):
    for path in ("/Users" + "/someone/vault", "/home" + "/someone/vault"):
        assert kinds(tree(tmp_path, {"run.sh": f"cd {path}\n"})) == {"home directory path"}


def test_a_phone_number_is_caught(tmp_path):
    assert kinds(tree(tmp_path, {"contact.md": "call 555 123" + " 4567\n"})) \
        == {"phone number"}


def test_a_name_taken_from_the_vault_is_caught_anywhere_it_appears(tmp_path):
    """**Indirect is where both real leaks lived.** Nobody published a
    password; they published a name in a path and a face in a fixture."""
    root = tree(tmp_path, {"docs/notes.md": "written by Jane Doe\n"})
    found = pc.scan_text(root, ["Jane Doe"])
    assert [f["kind"] for f in found] == ["name: Jane Doe"]


def test_a_finding_says_where_it_is_down_to_the_line(tmp_path):
    root = tree(tmp_path, {"a.md": "clean\nclean\n" + f'key {KEYS[0]}\n'})
    f = pc.scan_text(root, [])[0]
    assert f["where"] == "a.md:3"


def test_filenames_are_a_surface_too(tmp_path):
    """Information does not care which file it sits in — including the name of
    the file itself."""
    (tmp_path / "Jane Doe resume.md").write_text("nothing here\n", encoding="utf-8")
    found = pc.scan_filenames(tmp_path, ["Jane Doe"], [])
    assert found and "Jane Doe" in found[0]["where"]


# --------------------------------------------------- it does not cry wolf

@pytest.mark.parametrize("line", [
    "mail us at hello@example.com",
    "set your-email in the config",
    "listening on 127.0.0.1:8000",
    "open http://localhost:3000",
    "noreply@github.com sends this",
])
def test_documentation_examples_are_not_disclosures(tmp_path, line):
    """Flagging these trains people to ignore the tool, which is how the two
    real leaks got through checks that did exist."""
    assert pc.scan_text(tree(tmp_path, {"README.md": line + "\n"}), []) == []


def test_the_detector_does_not_accuse_itself(orch):
    """A file that defines these patterns necessarily contains them."""
    found = pc.scan_text(orch / "jobs", [])
    offenders = [f for f in found if f["where"].startswith("privacy_check.py")]
    assert offenders == [], offenders


def test_a_clean_tree_produces_nothing(tmp_path):
    assert pc.scan_text(tree(tmp_path, {"a.py": "x = 1\n", "b.md": "# Title\n"}), []) == []


def test_generated_and_vendored_trees_are_not_scanned(tmp_path):
    for d in (".venv", "node_modules", "__pycache__", "dist", "build"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "x.py").write_text(f'K = "{KEYS[0]}"\n', encoding="utf-8")
    assert pc.scan_text(tmp_path, []) == []


# ------------------------------------------------------------------ history

def test_history_is_a_surface_because_history_is_what_gets_cloned(tmp_path):
    """A secret deleted in the latest commit is still in the history."""
    git = ["git", "-c", "user.email=t@example.com", "-c", "user.name=Test"]
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    f = tmp_path / "config.py"
    f.write_text(f'KEY = "{KEYS[0]}"\n', encoding="utf-8")
    subprocess.run(git + ["add", "."], cwd=tmp_path, check=True)
    subprocess.run(git + ["commit", "-qm", "oops"], cwd=tmp_path, check=True)
    f.write_text("KEY = os.environ['KEY']\n", encoding="utf-8")
    subprocess.run(git + ["add", "."], cwd=tmp_path, check=True)
    subprocess.run(git + ["commit", "-qm", "removed"], cwd=tmp_path, check=True)

    # The working tree is clean...
    assert pc.scan_text(tmp_path, []) == []
    # ...and the repository is not.
    found, ran = pc.scan_history(tmp_path)
    assert ran and found
    assert all(f["kind"].startswith("history") for f in found)
    # The detail is a stub, never the key: a report is a document too.
    assert all(KEYS[0] not in f["detail"] for f in found)


def test_history_of_a_clean_repo_is_clean(tmp_path):
    git = ["git", "-c", "user.email=t@example.com", "-c", "user.name=Test"]
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(git + ["add", "."], cwd=tmp_path, check=True)
    subprocess.run(git + ["commit", "-qm", "clean"], cwd=tmp_path, check=True)
    found, ran = pc.scan_history(tmp_path)
    assert ran and found == []


def test_a_directory_that_is_not_a_repo_reports_not_run_rather_than_clean(tmp_path):
    """**Silence from the checker is only meaningful if it ran.** A surface
    that could not be checked must not report the same thing as one that was
    checked and came back empty."""
    found, ran = pc.scan_history(tmp_path)
    assert found == [] and ran is False


# ------------------------------------------------- what this suite does NOT cover

def test_the_image_and_video_passes_are_not_exercised_here_and_that_is_stated():
    """**Not a placeholder — a boundary.** `scan_images` and `scan_video` need
    tesseract and a local vision model. Neither exists on a CI runner, so they
    are run by hand before a release. Asserting they exist keeps the gap
    visible instead of letting a green suite imply coverage it has not got."""
    assert callable(pc.scan_images)
    assert callable(pc.scan_video)
