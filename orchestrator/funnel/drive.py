#!/usr/bin/env python3
"""
drive.py — documents, scoped to declared folders.

    python3 -m funnel.drive park
    python3 -m funnel.drive status
    python3 -m funnel.drive scope        # what the routine is allowed to send

**Scoped by folder, for the same reason mail is scoped by label.** A whole Drive
holds contracts, financials and other people's shared files. What belongs in a
knowledge base is the handful of folders that hold working material, so the
allowed folders are declared in `state/drive/scope.json` and everything outside
them is dropped here even if the routine sends it.

    {"folders": [{"id": "1AbC...", "name": "Research"}]}

**What the routine sends.** A session holding the Drive connector lists files
changed in those folders since the last drop, reads each, and writes to
`state/drive/`:

    {"documents": [{"id":..., "name":..., "folder_id":..., "mimeType":...,
                    "modified":..., "text":..., "url":...}]}

**A document is a snapshot, not a subscription.** Drive files change under you,
so the item id includes the modified time: an edited document comes back as a
new item and supersedes the old one through the funnel's normal ageing, rather
than silently disagreeing with what was filed months ago.
"""
import argparse
import json

from . import connectors

name = "drive"
min_body = 300

SCOPE = connectors.drop_dir("drive") / "scope.json"
# Formats with no text to read. A slide deck or a spreadsheet exported as text
# is mostly layout, and it ranks on noise.
SKIP_MIME = ("image/", "video/", "audio/", "application/zip",
             "application/vnd.google-apps.spreadsheet",
             "application/vnd.google-apps.form")


def allowed_folders() -> set:
    try:
        return {f["id"] for f in json.loads(SCOPE.read_text())["folders"]}
    except Exception:
        return set()


def envelope(r):
    folders = allowed_folders()
    if folders and r.get("folder_id") not in folders:
        # Declared scope wins over whatever arrived. An undeclared scope means
        # nothing is allowed, not everything — the safe direction to fail.
        return None
    if not folders:
        return None
    if any((r.get("mimeType") or "").startswith(m) for m in SKIP_MIME):
        return None
    title = (r.get("name") or "").strip() or "(untitled document)"
    return {
        # The modified time is part of the identity: an edited document is a
        # new snapshot that supersedes the old one, not a duplicate to skip.
        "id": connectors.item_id("drv", r.get("id"), r.get("modified")),
        "url": r.get("url") or f"drive:{r.get('id','')}",
        "title": title,
        "author": r.get("owner") or "Drive",
        "source": "Drive",
        "tier": 5,
        "domain": "document",
        "form": "document",
        "published": r.get("modified") or connectors.now_iso(),
        "body": r.get("text") or "",
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("park"); p.add_argument("--dry-run", action="store_true")
    sub.add_parser("status"); sub.add_parser("scope")
    a = ap.parse_args()
    if a.cmd == "scope":
        f = allowed_folders()
        print(f"scope file: {SCOPE}")
        print(f"{len(f)} folder(s) allowed" if f else
              "NO folders declared — nothing will be parked. Write scope.json first.")
        return
    if a.cmd == "status":
        h = connectors.health(name)
        print(f"{name}: {h['state']} — {h['why']}")
        print(f"scope: {len(allowed_folders())} declared folder(s)")
        return
    s = connectors.park(__import__("sys").modules[__name__], dry_run=a.dry_run)
    print(f"found {s['found']} · parked {s['parked']} · already seen "
          f"{s['already']} · skipped {s['skipped']}")


if __name__ == "__main__":
    main()
