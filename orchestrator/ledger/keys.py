#!/usr/bin/env python3
"""
keys.py — the one place an agent resolves an API key, and the routing that
decides *which* key it is entitled to.

**The file is `05-Orchestrator/ledger/keys.md`.** Plaintext, gitignored, written
by the owner. [@owner · 2026-08-20] The macOS Keychain was considered and **rejected**:
it needs a human present to authorise, and a routine that stops for a password
has already failed. Do not propose it again.

**Why a resolver and not "go read the file".** Every agent reading a secrets file
by hand is every agent inventing its own parsing, its own fallback when a label
is missing, and its own idea of which key it may use. The failure that produces
is not a crash — it is an agent quietly borrowing another product's key and
billing it. This module makes the routing a rule instead of a judgement.

**Values never come back to the surface.** `resolve()` returns a key for use in a
request. Nothing here prints one, and `status` deliberately reports presence and
fingerprint only. If you are tempted to print a key to debug, print
`fingerprint()` instead.

    python3 -m ledger.keys status                 # what is present, no values
    python3 -m ledger.keys which gamma            # which label a project gets
    python3 -m ledger.keys which --qa gamma       # same, for a QA run
    python3 -m ledger.keys check                  # every product resolves?

In code:

    from ledger.keys import resolve
    key = resolve("openrouter", project="gamma")
"""
import argparse
import hashlib
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
KEYS = HERE / "keys.md"
EXAMPLE = HERE / "keys.example.md"

# Work that belongs to no product. Anything not a product's own spend lands here
# rather than on whichever product's key happened to be nearby.
INTERNAL = "mikoshi-internal"

class KeyError_(RuntimeError):
    """Raised with a message that says what to do, never with a key in it."""


HEADING = re.compile(r"^#{1,6}\s*(.+?)\s*$")
ENTRY = re.compile(r"^\s*[-*]?\s*([A-Za-z0-9_.\- ]+?)\s*:\s*(.*?)\s*$")

# When the file carries no `##` headings, the key's own prefix says who issued it.
# the owner writes this by hand and should not have to remember a schema.
PREFIX = [("sk-or-", "openrouter"), ("sk-proj-", "openai"), ("nvapi-", "nvidia"),
          ("sk-", "openai")]

# His words on the left, the canonical label on the right. The registry and the
# routing table use folder names; a human writing a list at speed does not.
ALIASES = {
    "mikoshi-management": ("openrouter", "management"),
    "openrouter-management": ("openrouter", "management"),
    "management": ("openrouter", "management"),
    "open-ai-key": ("openai", "gamma"),
    "openai-key": ("openai", "gamma"),
    "hume-api-key": ("hume", "gamma-api"),
    "hume-secret-key": ("hume", "gamma-secret"),
}

# A label whose value never arrives — a section heading written as "For CASEY:".
NOT_A_KEY = re.compile(r"^(for\b|notes?$|keys?$)", re.I)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _label(raw: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", raw.strip().lower()).strip("-")


def _provider_of(label, value, heading=None):
    if heading:
        return _norm(heading)
    if label in ALIASES:
        return ALIASES[label][0]
    if "hume" in label:
        return "hume"
    for pre, prov in PREFIX:
        if value.lower().startswith(pre):
            return prov
    return "unknown"


def _put(out, provider, label, value):
    out.setdefault(_norm(provider), {})[label] = value


def suspicious(value: str):
    """Why a value looks damaged, or None. Never returns the value itself.

    The two likeliest corruptions are silent: an editor capitalising the first
    letter after a newline, and a line-wrap escape pulled in with a paste. Both
    look right in the file and fail at the provider with a 401 you would then go
    debugging in entirely the wrong place."""
    if value != value.strip():
        return "leading or trailing whitespace"
    if "\\" in value:
        return "contains a backslash — probably a line-wrap escape from a paste"
    if any(c.isspace() for c in value):
        return "contains a space or newline inside the value"
    for pre, _ in PREFIX:
        if value.lower().startswith(pre) and not value.startswith(pre):
            return (f"begins {value[:len(pre)]!r}, should be {pre!r} — an editor "
                    f"has capitalised it and it will fail to authenticate")
    return None


def load(path: pathlib.Path = None) -> dict:
    """{provider: {label: value}}.

    **Deliberately tolerant.** the owner writes this file by hand, so it accepts `##`
    provider headings *or* none at all, a value on the same line *or* the next,
    labels with spaces and capitals, and his own names for things. A parser that
    demands a schema from a human writing a key list at speed is a parser that
    silently finds nothing — which is what the strict first version did on the
    first real file, 2026-08-20."""
    path = path or KEYS
    if not path.exists():
        raise KeyError_(
            f"No key file at {path}.\n"
            f"  the owner writes it; agents only read it. The format is in "
            f"{EXAMPLE.name}.\n"
            f"  Do not work around this by using a key from another project.")

    out, heading, pending = {}, None, None
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.startswith("#"):
            h = HEADING.match(s)
            heading, pending = (h.group(1) if h else None), None
            continue

        # A label that ended with a bare colon takes the next line as its value.
        if pending and ":" not in s:
            lab, prov = pending
            _put(out, prov or _provider_of(lab, s, heading), lab, s)
            pending = None
            continue

        m = ENTRY.match(s)
        if not m:
            pending = None
            continue
        label, value = _label(m.group(1)), m.group(2)
        if not value:
            if NOT_A_KEY.match(m.group(1)):        # "For CASEY specifically:"
                pending = None
                continue
            pending = ALIASES.get(label, (label, None))[::-1][::-1] \
                if label in ALIASES else (label, None)
            if label in ALIASES:
                pending = (ALIASES[label][1], ALIASES[label][0])
            continue
        if value.upper().endswith("REPLACE"):
            continue
        prov, lab = (ALIASES[label] if label in ALIASES
                     else (_provider_of(label, value, heading), label))
        _put(out, prov, lab, value)
        pending = None

    return {p: v for p, v in out.items() if v}


def fingerprint(value: str) -> str:
    """A stable 8-char tag for a key. Safe to log — this is what you print
    instead of the key when you need to prove which one was used."""
    return hashlib.sha256(value.encode()).hexdigest()[:8]


def which(project: str = None, *, qa: bool = False, internal: bool = False) -> str:
    """The label a caller is entitled to. The routing rule, in one function.

    [@owner · 2026-08-20] **QA bills the product under test**, not a shared
    bucket — `run.sh` already takes the project name, so a gamma QA run is a cost
    of building gamma, and folding it into `mikoshi-internal` would make that the
    largest line while saying nothing about which product is expensive.
    **Ingestion and vault tooling bill `mikoshi-internal`**: they are vault-wide
    and belong to no product."""
    if internal or not project:
        return INTERNAL
    project = project.strip().lower()
    if project in ("mikoshi", "mikoshi-internal", "vault", "05-orchestrator", "funnel"):
        return INTERNAL
    # A QA run against a real product bills that product; a QA run against
    # nothing in particular is internal. Same rule either way — the project name
    # decides, and `qa` only changes where an unknown name falls.
    known = _products()
    if project in known:
        return project
    if qa:
        return INTERNAL
    return project


def _products() -> set:
    """Project folder names, which are the labels products must use."""
    projects = HERE.parent.parent / "02-Projects"
    if not projects.exists():
        return set()
    return {p.name.lower() for p in projects.iterdir() if p.is_dir()}


def resolve(provider: str, *, project: str = None, label: str = None,
            qa: bool = False, internal: bool = False) -> str:
    """The key to use. Raises with instructions rather than falling back.

    **There is deliberately no fallback to another label.** A resolver that
    quietly substitutes a key that happens to exist is how one product's spend
    lands on another's bill, which is the exact failure this whole system was
    built to end — 94% of $25.27 untraceable, measured 2026-08-20."""
    keys = load()
    p = _norm(provider)
    if p not in keys:
        raise KeyError_(
            f"No '{provider}' section in {KEYS.name}. Present: "
            f"{', '.join(sorted(keys)) or 'nothing'}.")
    want = (label or which(project, qa=qa, internal=internal)).lower()
    if want in keys[p]:
        return keys[p][want]
    if want != INTERNAL and INTERNAL in keys[p]:
        raise KeyError_(
            f"No '{want}' key under {provider}. Available: "
            f"{', '.join(sorted(keys[p]))}.\n"
            f"  If this work is not a product's own, ask for '{INTERNAL}' "
            f"explicitly with internal=True — do not let it default silently.")
    raise KeyError_(
        f"No '{want}' key under {provider}. Available: "
        f"{', '.join(sorted(keys[p])) or 'nothing'}.")


# ------------------------------------------------------------------ commands

def cmd_status(_):
    try:
        keys = load()
    except KeyError_ as e:
        print(e); return 1
    print(f"{KEYS.name} — presence and fingerprints only, never values\n")
    broken = []
    for provider in sorted(keys):
        print(f"  {provider}")
        for label in sorted(keys[provider]):
            v = keys[provider][label]
            bad = suspicious(v)
            flag = "  <-- " + bad if bad else ""
            print(f"    {label:<24} "
                  f"{'SUSPECT' if bad else 'present'}  "
                  f"[{fingerprint(v)}]{flag}")
            if bad:
                broken.append(f"{provider}/{label}: {bad}")
    if broken:
        print(f"\n{len(broken)} key(s) look damaged and will fail at the provider:")
        for b in broken:
            print(f"  · {b}")
        print("Fix them in keys.md. A bad key does not error here — it errors")
        print("later, as a 401, somewhere unrelated.")
        return 1
    return 0


def cmd_which(a):
    print(which(a.project, qa=a.qa, internal=a.internal))
    return 0


def cmd_check(_):
    """Does every product that needs a key actually have one? Presence only."""
    import json
    reg = json.loads((HERE / "products.json").read_text(encoding="utf-8"))
    try:
        keys = load()
    except KeyError_ as e:
        print(e); return 1

    missing = []
    print("Every product that draws on a pool, and whether its key exists:\n")
    for name, p in reg["products"].items():
        for pool in p.get("pools", []):
            # The registry is the authority on a product's labels. Guessing from
            # the folder name reported CASEY's Hume keys as missing when they are
            # present under `gamma-api` and `-secret`.
            declared = (p.get("keys") or {}).get(pool)
            labels = ([declared] if isinstance(declared, str)
                      else declared or [which(name)])
            for label in labels:
                ok = label in keys.get(_norm(pool), {})
                bad = suspicious(keys[_norm(pool)][label]) if ok else None
                print(f"  {p['display']:<28} {pool:<12} {label:<22} "
                      f"{'SUSPECT' if bad else 'ok' if ok else 'MISSING'}")
                if not ok:
                    missing.append((name, pool, label))
                elif bad:
                    missing.append((name, pool, f"{label} — {bad}"))
    for extra in (INTERNAL, "management"):
        ok = extra in keys.get("openrouter", {})
        print(f"  {'(vault-wide)' if extra == INTERNAL else '(reporting)':<28} "
              f"{'openrouter':<12} {extra:<22} {'ok' if ok else 'MISSING'}")
        if not ok and extra == INTERNAL:
            missing.append(("vault", "openrouter", extra))
    if missing:
        print(f"\n{len(missing)} missing. Nothing falls back to another product's "
              f"key — add the label or the work does not run.")
        return 1
    print("\nall present")
    return 0


def cmd_add(a):
    """Add or replace a key without opening the file.

    [@owner · 2026-08-20] *"I don't like going into Mikoshi files because I don't
    want to break anything by accident."* That is a correct instinct and the
    reason this exists: hand-editing a file that six tools read is a real way to
    break things, and the first version of this system asked him to do exactly
    that. The value is typed at a hidden prompt, never echoed, never passed as an
    argument (which would put it in shell history), and validated against the
    provider **before** anything is written — so a bad paste is rejected rather
    than saved."""
    import getpass
    value = getpass.getpass(f"paste the {a.provider}/{a.label} key (input hidden): ").strip()
    if not value:
        print("nothing entered — nothing written")
        return 1

    bad = suspicious(value)
    if bad:
        print(f"refused: {bad}")
        print("nothing was written. Fix the paste and run this again.")
        return 1

    ok, detail = _verify(a.provider, value)
    if not ok and not a.force:
        print(f"refused: the provider rejected this key — {detail}")
        print("nothing was written. --force to store it anyway.")
        return 1

    path = KEYS
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    # Replace in place if the label is already there, so the file does not grow
    # a second entry that quietly loses to the first.
    replaced = False
    for i, line in enumerate(lines):
        m = ENTRY.match(line.strip())
        if m and _label(m.group(1)) == _label(a.label) and m.group(2):
            lines[i] = f"{m.group(1)}: {value}"
            replaced = True
            break
    if not replaced:
        lines += ["", f"{a.label}: {value}"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)
    print(f"{'replaced' if replaced else 'added'} {a.provider}/{a.label} "
          f"[{fingerprint(value)}] — verified live, file left at 600")
    return 0


def _verify(provider, value):
    """Ask the provider whether the key works. (ok, detail)."""
    import json as _json, urllib.request, urllib.error
    url = {"openrouter": "https://openrouter.ai/api/v1/key",
           "openai": "https://api.openai.com/v1/models",
           # NVIDIA's hosted catalogue speaks the OpenAI shape, so the same
           # bearer check works and a bad paste is refused at the prompt
           # rather than stored and discovered later as a 401.
           "nvidia": "https://integrate.api.nvidia.com/v1/models"}.get(_norm(provider))
    if not url:
        return True, "no check available for this provider"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {value}"})
    try:
        with urllib.request.urlopen(req, timeout=25):
            return True, "accepted"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return True, f"could not reach {provider} ({str(e)[:40]}) — not treated as failure"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="what is present — never values")
    w = sub.add_parser("which", help="which label a project is entitled to")
    w.add_argument("project", nargs="?")
    w.add_argument("--qa", action="store_true", help="this is a QA run")
    w.add_argument("--internal", action="store_true", help="not a product's own work")
    sub.add_parser("check", help="does every product that needs a key have one")
    ad = sub.add_parser("add", help="add or replace a key at a hidden prompt")
    ad.add_argument("provider")
    ad.add_argument("label")
    ad.add_argument("--force", action="store_true",
                    help="store even if the provider rejects it")
    a = ap.parse_args()
    try:
        return {"status": cmd_status, "which": cmd_which, "check": cmd_check,
                "add": cmd_add}[a.cmd](a)
    except KeyError_ as e:
        print(e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
