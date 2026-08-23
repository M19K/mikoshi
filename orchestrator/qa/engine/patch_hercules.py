#!/usr/bin/env python3
"""
patch_hercules.py — runs INSIDE the image build. One fix, loudly guarded.

**What it fixes.** Hercules assumes the planner agent's whole final reply is a
JSON object and strips only ```json fences before calling json.loads(). Models
routinely prefix their own agent name ("planner_agent: "), or put a sentence
before the block. The parse then throws, `runner_result` stays empty, and
build_junit_xml() writes the literal words "Runtime Failure" into the JUnit XML.

That string is a parser complaint. It reads as a test result, and there is
nothing else in the XML to tell the two apart.

**Measured, not assumed.** project-three, 2026-08-19: 10 of 11 scenarios
lost this way, every one of which the agent had actually driven to completion
and judged. The 11th did not emit the prefix, and its XML carries a full,
correct, quoted verdict.

**Why the guard matters more than the patch.** This edits upstream source by
exact string match. If a future Hercules changes that block, the match fails and
THE BUILD FAILS — noisily, with the current source printed. A patch that
silently stops applying is how you get a fixed-looking pipeline that regressed.
"""
import pathlib
import sys

TARGET = pathlib.Path("/testzeus-hercules/testzeus_hercules/__main__.py")

OLD = '''            json_content = s_rr.replace("```json\\n", "").replace("\\n```", "").strip()'''
NEW = '''            json_content = _mikoshi_extract_json(s_rr)'''

ANCHOR = "from testzeus_hercules.utils.logger import logger\n"

HELPER = '''

def _mikoshi_extract_json(text: str) -> str:
    """Return the first balanced top-level JSON object found in `text`.

    Added by Mikoshi. Upstream stripped ```json fences and required the entire
    reply to be JSON; anything the model said around the block — most often
    echoing its own agent name as a prefix — turned a completed, judged scenario
    into the words "Runtime Failure". Scanning for the first balanced object is
    string-quote aware, so a brace inside a JSON string does not close it.
    """
    s = text.strip()
    start = s.find("{")
    if start == -1:
        return s
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[start : i + 1]
    return s[start:]

'''


def die(msg: str) -> None:
    sys.stderr.write(
        "\n==============================================================\n"
        "MIKOSHI HERCULES PATCH FAILED TO APPLY\n"
        f"{msg}\n"
        "The upstream file changed. Re-read it, re-derive the patch, and do\n"
        "NOT ship an image whose fix silently stopped applying.\n"
        "==============================================================\n"
    )
    sys.exit(1)


def main() -> None:
    if not TARGET.is_file():
        die(f"{TARGET} does not exist.")
    src = TARGET.read_text(encoding="utf-8")

    if "_mikoshi_extract_json" in src:
        print("patch already applied — nothing to do")
        return
    if src.count(OLD) != 1:
        die(f"expected exactly 1 occurrence of the parse line, found {src.count(OLD)}:\n  {OLD}")
    if src.count(ANCHOR) != 1:
        die(f"expected exactly 1 occurrence of the import anchor, found {src.count(ANCHOR)}")

    src = src.replace(ANCHOR, ANCHOR + HELPER, 1).replace(OLD, NEW, 1)
    TARGET.write_text(src, encoding="utf-8")

    check = TARGET.read_text(encoding="utf-8")
    if "_mikoshi_extract_json(s_rr)" not in check or "def _mikoshi_extract_json" not in check:
        die("wrote the file but the patch is not present on re-read.")
    print("patched: tolerant JSON extraction for the planner's final answer")


if __name__ == "__main__":
    main()
