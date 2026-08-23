# **Windows consoles default to cp1252 and cannot encode the characters this
# codebase prints** — the log separator `·`, the em dash, and the `→` in every
# "here is the fix" line. On 2026-08-23 CI showed `record.py` dying on its own
# arrow, which meant the WRITE PATH was broken on Windows while every other
# check passed. Done at package import so a new script in here inherits it
# rather than having to remember; fixing twenty entry points one at a time is
# how the twenty-first gets missed.
import sys as _sys

for _s in (_sys.stdout, _sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass
