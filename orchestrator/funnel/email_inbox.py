#!/usr/bin/env python3
"""
email_inbox.py — mail, scoped to one label, never the whole mailbox.

    python3 -m funnel.email_inbox park
    python3 -m funnel.email_inbox status

**The scope is the design.** A personal mailbox is not a knowledge base, it is
someone's private life — and the vault's standing rule is professional-facing by
default. So this reads exactly one thing: mail the owner has labelled
`mikoshi` in Gmail. Labelling is the consent. Nothing else is ever sent, and if
the routine sends more than that, the filter below drops it.

This is deliberately different from `funnel.newsletters`, which reads a
dedicated intake address that receives nothing but subscriptions. That inbox is
safe to read whole because it was created for the purpose. A personal mailbox
never is.

**What the routine sends.** A session holding the Gmail connector searches
`label:mikoshi` since the last drop and writes to `state/email/`:

    {"messages": [{"id":..., "subject":..., "from":..., "date":...,
                   "body":..., "labels":[...]}]}

**Bodies are untrusted.** Anyone can send mail. Nothing here follows an
instruction found in one; it stores text for the classifier to judge.
"""
import argparse
import re

from . import connectors, fetch

name = "email"
min_body = 200

REQUIRED_LABEL = "mikoshi"
# Correspondence about a person rather than a subject. These never enter the
# corpus even if they carry the label by accident.
PERSONAL = re.compile(r"\b(payslip|salary|bank|invoice|medical|visa|i-?20|"
                      r"immigration|passport|tax return|insurance claim)\b", re.I)


def envelope(r):
    labels = [str(x).lower() for x in (r.get("labels") or [])]
    if REQUIRED_LABEL not in labels:
        # The consent is the label. No label, no ingestion — enforced here and
        # not left to whatever the routine happened to query.
        return None
    subject = (r.get("subject") or "").strip() or "(no subject)"
    if PERSONAL.search(subject):
        return None
    body = r.get("body") or r.get("text") or ""
    if len(body.strip()) < min_body and r.get("html"):
        body = fetch.detext(r["html"])
    frm = r.get("from") or "unknown"
    m = re.match(r"\s*(.+?)\s*<", frm)
    who = (m.group(1) if m else frm).strip().strip('"')
    return {
        "id": connectors.item_id("mail", r.get("id"), subject, r.get("date")),
        "url": r.get("url") or f"gmail:{r.get('id','')}",
        "title": subject,
        "author": who,
        "source": "Email",
        "tier": 5,
        "domain": "email",
        "form": "email",
        "published": r.get("date") or connectors.now_iso(),
        "body": body,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("park"); p.add_argument("--dry-run", action="store_true")
    sub.add_parser("status")
    a = ap.parse_args()
    if a.cmd == "status":
        h = connectors.health(name)
        print(f"{name}: {h['state']} — {h['why']}")
        print(f"scope: only mail labelled '{REQUIRED_LABEL}'")
        return
    s = connectors.park(__import__("sys").modules[__name__], dry_run=a.dry_run)
    print(f"found {s['found']} · parked {s['parked']} · already seen "
          f"{s['already']} · skipped {s['skipped']}")


if __name__ == "__main__":
    main()
