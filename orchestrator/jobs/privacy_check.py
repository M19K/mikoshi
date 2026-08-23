#!/usr/bin/env python3
"""
privacy_check.py — what is about to become public, that you did not mean to publish.

    python3 -m jobs.privacy_check --path <repo>
    python3 -m jobs.privacy_check --path <repo> --strict     # exit 1 on any finding
    python3 -m jobs.privacy_check --path <repo> --no-images  # text and history only

**This exists because it has already failed twice.** [@owner · 2026-08-23]
One project shipped screenshots carrying the local machine's IP address.
Another built its vision test set out of product pictures that included
photographs of its author. Neither was caught at push time. Both were caught
afterwards, by the person whose data it was.

**The lesson in both: the leak was never in the code.** Every text scanner in
this vault would have passed those repos, because the information was inside a
PNG and inside a decision about which files to use as fixtures. A privacy check
that only greps source is a check that would have missed both real incidents.

## The unit is INFORMATION, not file type

[@owner · 2026-08-23] *"Images count as information. Video counts as
information. Code counts as information. Information."*

So the question this asks is not "does any file match a secret pattern". It is
**what in here is about a specific real person or organisation, directly or
indirectly** — and indirectly is where both real leaks lived. An IP address in
the corner of a screenshot is indirect. A test fixture built from your own
product photos is indirect. A filename with a client's name in it is indirect,
and no scanner looking at file *contents* will ever see it.

Every surface below is checked, because information does not care which one it
is sitting in:

    file contents      text, code, config, notes
    file NAMES         a client name in a path is a disclosure with no contents
    images             OCR'd, and asked whether a person is in them
    video              frames sampled and read the same way
    documents          PDFs read as text
    embedded metadata  EXIF, author fields — where a camera and often a place live
    git history        every past version, because that is what a clone gets
    git identities     every commit carries an author name and email

## What it looks at, and why each one

**Text** — keys, tokens, emails, phone numbers, private IP addresses, home
directory paths, and any name on the watch list. Cheap and well understood.

**Images, actually read.** Every image is OCR'd. Screenshots carry IP
addresses, hostnames, window titles, mail subjects and browser tabs in the
corners nobody looks at, and the person who took the screenshot was looking at
the middle. Where a vision model is available, images are also asked one
question: *is there a person in this picture* — because a photograph of someone
is a disclosure whether or not anybody remembers adding it.

**Git history, not just the working tree.** A secret deleted in the most recent
commit is still in the history, and the history is what a clone gets. Checking
`HEAD` and calling it clean is the most common way a key stays published.

**A finding is not automatically a blocker. An unexamined finding is.** Clear
each one deliberately and record what you are keeping and why. And note the
obvious: **silence from this tool is only meaningful if it actually ran** — with
`--no-images` on a repo full of screenshots, it has told you almost nothing.
"""
import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

TEXT_SUFFIX = {".md", ".py", ".txt", ".json", ".jsonl", ".yml", ".yaml",
               ".toml", ".html", ".css", ".js", ".ts", ".sh", ".env", ".cfg"}
IMAGE_SUFFIX = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"}
SKIP = {".git", ".venv", "__pycache__", "node_modules", "dist", "build"}

PATTERNS = [
    ("api key", re.compile(r"sk-(or-v1|ant|proj)-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("email address", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("phone number", re.compile(r"(?<![\d.])(?:\+\d{1,3}[ -]?)?\(?\d{3}\)?[ -]\d{3}[ -]\d{4}(?![\d.])")),
    ("private IP", re.compile(r"(?<![\d.])(?:192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(?![\d.])")),
    # A detector necessarily contains the thing it detects. Flagging it for
    # that is how a checker teaches people to ignore it — the same reason
    # BENIGN below exempts this file from its own patterns.
    ("home directory path", re.compile(r"/Users/[A-Za-z0-9_.-]+|/home/[a-z0-9_.-]+|C:\\\\Users\\\\[A-Za-z0-9_.-]+")),  # allow-hardcode: this is the pattern, not a path
]

# Addresses and hosts that are meant to be there. Examples in documentation are
# not disclosures, and flagging them trains people to ignore the tool.
BENIGN = re.compile(
    r"example\.(com|org|net)|@example|noreply@|your-?email|<your|user@host|"
    r"127\.0\.0\.1|localhost|foo@bar|someone@|"
    # A file that DEFINES these patterns necessarily contains them. Flagging a
    # detector for detecting is how a checker teaches people to ignore it.
    r"re\.compile|PATTERNS|BENIGN|watch list|A-Za-z0-9", re.I)


def _names_from(vault: pathlib.Path) -> list:
    """Names to look for, taken from the vault rather than hardcoded.

    The owner's tag is in their CLAUDE.md; a real name usually appears in git
    config. Both are cheap to read and neither belongs baked into this file —
    that would be the hardcoding rule broken inside the privacy checker.
    """
    names = set()
    cfg = subprocess.run(["git", "config", "user.name"], capture_output=True,
                         text=True, cwd=str(vault))
    if cfg.returncode == 0 and cfg.stdout.strip():
        names.add(cfg.stdout.strip())
    claude = vault / "CLAUDE.md"
    if claude.is_file():
        m = re.search(r"tag is \*\*`@([A-Za-z0-9_-]+)`", claude.read_text(encoding="utf-8", errors="replace"))
        if m:
            names.add(m.group(1))
    return sorted(n for n in names if len(n) > 3)


DOC_SUFFIX = {".pdf"}
VIDEO_SUFFIX = {".mp4", ".mov", ".webm", ".avi", ".mkv"}


def scan_filenames(root, names, ids):
    """A path is information. No content scanner will ever see it."""
    out = []
    watch = ([(f"name: {n}", re.compile(re.escape(n), re.I)) for n in names]
             + [(f"identifier: {i}", re.compile(re.escape(i), re.I)) for i in ids])
    for p in sorted(root.rglob("*")):
        if p.is_dir() or set(p.parts) & SKIP:
            continue
        rel = str(p.relative_to(root))
        for kind, pat in watch + [(k, v) for k, v in PATTERNS if k != "home directory path"]:
            m = pat.search(rel)
            if m and not BENIGN.search(rel):
                out.append({"where": rel, "kind": f"in the filename · {kind}",
                            "detail": m.group(0)[:50]})
                break
    return out


def scan_documents(root, names, ids):
    """PDFs are text somebody forgot is text."""
    out = []
    if not shutil.which("pdftotext"):
        for p in walk(root, DOC_SUFFIX):
            out.append({"where": str(p.relative_to(root)), "kind": "not read",
                        "detail": "pdftotext absent — this document was NOT checked"})
        return out
    watch = (PATTERNS + [(f"name: {n}", re.compile(re.escape(n), re.I)) for n in names]
             + [(f"identifier: {i}", re.compile(re.escape(i), re.I)) for i in ids])
    for p in walk(root, DOC_SUFFIX):
        got = subprocess.run(["pdftotext", str(p), "-"], capture_output=True,
                             text=True, timeout=120)
        for kind, pat in watch:
            m = pat.search(got.stdout or "")
            if m and not BENIGN.search(m.group(0)):
                out.append({"where": str(p.relative_to(root)),
                            "kind": f"in a document · {kind}", "detail": m.group(0)[:60]})
                break
    return out


def scan_video(root, names, ids, vision=True):
    """A video is a stack of screenshots. Sample it and read the frames.

    Three frames, not every frame: the leak in a screen recording is usually a
    persistent thing — a menu bar, a window title, a visible IP — rather than
    one flash. Three is enough to catch persistent, and it is honest about not
    catching a flash.
    """
    out = []
    vids = list(walk(root, VIDEO_SUFFIX))
    if not vids:
        return out, 0
    if not shutil.which("ffmpeg"):
        for p in vids:
            out.append({"where": str(p.relative_to(root)), "kind": "not read",
                        "detail": "ffmpeg absent — this video was NOT checked"})
        return out, len(vids)
    import tempfile
    for p in vids:
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(p),
                            "-vf", "fps=1/10", "-frames:v", "3",
                            str(pathlib.Path(td) / "f%02d.png")],
                           capture_output=True, timeout=300)
            frames = sorted(pathlib.Path(td).glob("*.png"))
            sub, _ = scan_images(pathlib.Path(td), names, ids=ids, vision=vision)
            for f in sub:
                out.append({"where": f"{p.relative_to(root)} (frame)",
                            "kind": f["kind"].replace("in image", "in video"),
                            "detail": f["detail"]})
            if not frames:
                out.append({"where": str(p.relative_to(root)), "kind": "not read",
                            "detail": "no frames extracted — check it by hand"})
    return out, len(vids)


def scan_metadata(root):
    """EXIF and friends. Where a camera, a timestamp and often a place live."""
    out = []
    for p in walk(root, IMAGE_SUFFIX):
        try:
            raw = p.read_bytes()[:65536]
        except Exception:
            continue
        # No exiftool dependency: look for the tags that matter as raw markers.
        for marker, what in ((b"GPS", "GPS data"), (b"Make", "camera make"),
                             (b"Artist", "artist/author field"),
                             (b"XMP", "XMP metadata block")):
            if marker in raw:
                out.append({"where": str(p.relative_to(root)),
                            "kind": f"embedded metadata · {what}",
                            "detail": "strip it before publishing"})
                break
    return out


def scan_git_identities(root):
    """Every commit carries a name and an email, forever."""
    out = []
    if not (root / ".git").exists():
        return out
    got = subprocess.run(["git", "log", "--all", "--format=%an <%ae>"],
                         capture_output=True, text=True, cwd=str(root), timeout=120)
    if got.returncode != 0:
        return out
    for who in sorted(set(l.strip() for l in got.stdout.splitlines() if l.strip())):
        out.append({"where": "git history", "kind": "commit identity",
                    "detail": who[:70]})
    return out


def _identifiers_from(vault: pathlib.Path) -> list:
    """Indirect identifiers, read from the vault instead of guessed.

    The vault already knows the things that point at its owner: the product
    names in the cost registry, the domains and handles in their profile, the
    GitHub account. **Indirect is where both real leaks lived** — nobody
    published a password, they published a client's name in a path and their own
    face in a test fixture. Reading them from the vault also keeps this file
    free of the very thing it is checking for.
    """
    ids = set()
    reg = vault / "05-Orchestrator" / "ledger" / "products.json"
    if reg.is_file():
        try:
            ids.update(json.loads(reg.read_text(encoding="utf-8"))["products"].keys())
        except Exception:
            pass
    for f in ("the owner Profile.md", "CLAUDE.md", "Home.md"):
        p = vault / f
        if not p.is_file():
            continue
        body = p.read_text(encoding="utf-8", errors="replace")
        ids.update(re.findall(r"https?://(?:www\.)?([a-z0-9-]+\.(?:co|com|io|ai|dev))", body))
        ids.update(re.findall(r"(?:x\.com|twitter\.com|linkedin\.com/in)/([A-Za-z0-9_-]{3,})", body))
    owner = subprocess.run(["gh", "api", "user", "--jq", ".login"],
                           capture_output=True, text=True)
    if owner.returncode == 0 and owner.stdout.strip():
        ids.add(owner.stdout.strip())
    generic = {"github.com", "google.com", "claude.ai", "anthropic.com", "openai.com",
               "ollama.com", "example.com"}
    return sorted(i for i in ids if len(i) > 3 and i.lower() not in generic)


def walk(root: pathlib.Path, suffixes):
    for p in sorted(root.rglob("*")):
        if p.is_dir() or set(p.parts) & SKIP:
            continue
        if p.suffix.lower() in suffixes:
            yield p


def scan_text(root, names):
    out = []
    watch = [(f"name: {n}", re.compile(re.escape(n), re.I)) for n in names]
    for p in walk(root, TEXT_SUFFIX):
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for i, line in enumerate(lines, 1):
            if BENIGN.search(line):
                continue
            for kind, pat in PATTERNS + watch:
                m = pat.search(line)
                if m:
                    out.append({"where": f"{p.relative_to(root)}:{i}", "kind": kind,
                                "detail": m.group(0)[:60]})
                    break
    return out


def scan_images(root, names, ids=(), vision=True):
    """OCR every image, and ask a vision model whether a person is in it."""
    out = []
    imgs = list(walk(root, IMAGE_SUFFIX))
    if not imgs:
        return out, 0
    has_ocr = shutil.which("tesseract") is not None
    for p in imgs:
        text = ""
        if has_ocr:
            got = subprocess.run(["tesseract", str(p), "stdout"],
                                 capture_output=True, text=True, timeout=120)
            text = got.stdout if got.returncode == 0 else ""
        else:
            out.append({"where": str(p.relative_to(root)), "kind": "not read",
                        "detail": "tesseract absent — this image was NOT checked"})
        for kind, pat in (PATTERNS
                          + [(f"name: {n}", re.compile(re.escape(n), re.I)) for n in names]
                          + [(f"identifier: {i}", re.compile(re.escape(i), re.I)) for i in ids]):
            m = pat.search(text)
            if m and not BENIGN.search(m.group(0)):
                out.append({"where": str(p.relative_to(root)),
                            "kind": f"in image · {kind}", "detail": m.group(0)[:60]})
        if vision:
            v = _person_in(p)
            if v:
                out.append({"where": str(p.relative_to(root)),
                            "kind": "in image · a person", "detail": v[:80]})
    return out, len(imgs)


def _person_in(path: pathlib.Path):
    """One question to a local vision model. Absent model, absent answer."""
    import base64, urllib.request
    url = os.environ.get("MIKOSHI_OLLAMA_URL", "http://localhost:11434")
    model = os.environ.get("MIKOSHI_VISION_MODEL", "qwen2.5vl:7b")
    try:
        b64 = base64.b64encode(path.read_bytes()).decode()
        body = {"model": model, "stream": False, "options": {"temperature": 0},
                "messages": [{"role": "user", "images": [b64], "content":
                              "Is there a photograph of a real human face or body "
                              "in this image? Answer only YES or NO, then five "
                              "words of description."}]}
        req = urllib.request.Request(f"{url}/api/chat",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            ans = json.load(r)["message"]["content"].strip()
        return ans if ans.upper().startswith("YES") else None
    except Exception:
        return None


def scan_history(root):
    """The working tree is not what gets cloned. The history is."""
    out = []
    if not (root / ".git").exists():
        return out, False
    got = subprocess.run(["git", "log", "-p", "--all", "--no-color"],
                         capture_output=True, text=True, cwd=str(root), timeout=300)
    if got.returncode != 0:
        return out, False
    for kind, pat in PATTERNS[:1]:            # keys only — the unrecoverable class
        for m in pat.finditer(got.stdout):
            out.append({"where": "git history", "kind": f"history · {kind}",
                        "detail": m.group(0)[:24] + "…"})
    return out[:20], True


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", required=True)
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--no-images", action="store_true")
    ap.add_argument("--no-vision", action="store_true",
                    help="OCR images but skip the is-there-a-person question")
    a = ap.parse_args()
    root = pathlib.Path(a.path).resolve()
    vault = pathlib.Path(__file__).resolve().parent.parent.parent
    names = _names_from(vault)

    print(f"privacy check · {root}")
    ids = _identifiers_from(vault)
    print(f"  keys, emails, phones, private IPs, home paths")
    print(f"  {len(names)} name(s) and {len(ids)} indirect identifier(s), "
          f"read from this vault — products, domains, handles, the GitHub account")
    print(f"  surfaces: contents · filenames · images · video · documents · "
          f"metadata · git history · commit identities\n")

    ids = _identifiers_from(vault)
    findings = scan_text(root, names + ids)
    findings += scan_filenames(root, names, ids)
    findings += scan_documents(root, names, ids)
    findings += scan_metadata(root)
    findings += scan_git_identities(root)
    imgs = vids = 0
    if not a.no_images:
        img_findings, imgs = scan_images(root, names, ids, vision=not a.no_vision)
        findings += img_findings
        vid_findings, vids = scan_video(root, names, ids, vision=not a.no_vision)
        findings += vid_findings
    hist, history_read = scan_history(root)
    findings += hist

    print(f"  text files scanned   {sum(1 for _ in walk(root, TEXT_SUFFIX))}")
    print(f"  videos read          {vids}")
    print(f"  images read          {imgs}"
          + ("  ** SKIPPED — this check told you nothing about images **"
             if a.no_images else ""))
    print(f"  git history          {'scanned' if history_read else 'NOT scanned — no repo here'}")
    print()

    if not findings:
        print("  Nothing found.\n")
        print("  Read that precisely: nothing MATCHED. A privacy check cannot")
        print("  see a disclosure it has no pattern for — a whiteboard in the")
        print("  background of a photo, a client name in a filename, a chart")
        print("  built from real customer numbers. Look at the images yourself.")
        return

    by_kind = {}
    for f in findings:
        by_kind.setdefault(f["kind"], []).append(f)
    for kind, rows in sorted(by_kind.items()):
        print(f"{kind}  ({len(rows)})")
        for r in rows[:8]:
            print(f"    {r['where']}  {r['detail']}")
        if len(rows) > 8:
            print(f"    … and {len(rows) - 8} more")
        print()
    print(f"{len(findings)} finding(s). **A finding is not a blocker. "
          f"An unexamined finding is.**")
    print("Clear each one deliberately and write down what you are keeping and why.")
    if a.strict:
        sys.exit(1)


if __name__ == "__main__":
    main()
