#!/usr/bin/env python3
"""
run.py — the funnel, end to end.

    fetch → extract → classify → cluster → score → distil → link → file

    python3 -m funnel.run                      # full run, stages into 05-Orchestrator/staged/
    python3 -m funnel.run --limit 40           # cap the LLM stages while testing
    python3 -m funnel.run --dry-run            # fetch + classify + score, write nothing
    python3 -m funnel.run --no-promote         # stage only, do not touch 01-Knowledge Base
    python3 -m funnel.run --since 7            # widen the freshness window

Every drop is counted and named in the digest. Nothing is capped silently: if a
stage limits what it processes, the digest says so.
"""
import argparse
import datetime as dt
import sys
import time

from . import (classify, cluster, distil, embed_corpus, fetch, filer, link, llm,
               promote, registry, score, store)
from .score import select_for_filing


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def bar(label):
    def p(done, total):
        print(f"  {label} {done}/{total}", end="\r", file=sys.stderr, flush=True)
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="max items into the LLM stages")
    ap.add_argument("--since", type=int, default=3, help="freshness window in days")
    ap.add_argument("--dry-run", action="store_true", help="write no files")
    ap.add_argument("--max-file", type=int, default=40,
                    help="most entries that may enter the Knowledge Base in one run")
    ap.add_argument("--no-promote", action="store_true",
                    help="stage only; do not write into 01-Knowledge Base")
    ap.add_argument("--no-embed-corpus", action="store_true")
    a = ap.parse_args()

    started = time.time()
    run_date = dt.date.today().isoformat()
    run_id = f"{run_date}-{int(started)}"
    db = store.connect()
    log(f"funnel {run_id}  vec={db.vec}  model={'up' if llm.available() else 'DOWN — deterministic only'}")

    # 0 · the vault must be in the index before anything can link to it
    if not a.no_embed_corpus:
        log("· indexing vault")
        log(f"  {embed_corpus.run(db, verbose=False)}")

    # 1 · fetch
    sources = registry.load()
    feeds = registry.fetchable(sources)
    unreachable = registry.unreachable(sources)
    envelopes, feed_status, already = [], [], 0
    for i, s in enumerate(feeds, 1):
        entries, status = fetch.fetch(db, s, max_age_days=a.since)
        feed_status.append((s.name, status))
        for e in entries:
            iid = fetch.item_id(e["url"])
            if store.seen(db, iid):
                already += 1
                continue
            envelopes.append({
                "id": iid, "url": fetch.canonical(e["url"]), "title": e["title"] or "(untitled)",
                "author": e["author"], "source": s.name, "tier": s.tier, "domain": s.domain,
                "form": s.form, "published": e["published"].isoformat() if e["published"] else None,
                "fetched": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "body": e["body"], "run_id": run_id, "state": "seen",
            })
        print(f"  fetch {i}/{len(feeds)} {s.name[:28]:30}", end="\r", file=sys.stderr)

    # Park every fetched item IMMEDIATELY, before any judgment work.
    #
    # Conditional GET means a feed only hands over an item once: the cache
    # markers advance on fetch, so a run that dies during classification takes
    # those items with it — they are neither stored nor offered again. A killed
    # run silently cost 389 items exactly this way. Storing first makes the
    # expensive stages resumable: anything left `seen` is picked up next run.
    for e in envelopes:
        store.put_item(db, e)
    db.commit()
    ok = sum(1 for _, s in feed_status if s in ("ok", "unchanged"))
    log(f"\n· fetched {len(envelopes)} new items from {ok}/{len(feeds)} feeds "
        f"({already} already seen)")

    # The X timeline arrives as one mixed stream, so weighting has to be restored
    # per item. The handle is in each link; the registry knows its bucket.
    import re as _re
    xb = {}
    for e in envelopes:
        m = _re.match(r"https?://(?:www\.)?x\.com/([^/]+)/", e["url"])
        if not m:
            continue
        h = m.group(1)
        if h not in xb:
            xb[h] = registry.bucket_of(h)
        e["domain"] = xb[h]
        e["source"] = f"X @{h}"
    if xb:
        log(f"· X timeline: {len(xb)} accounts, bucketed for weighting")

    # Items other reaches already extracted and parked. Instagram runs on its own
    # schedule because a download costs minutes, not milliseconds, so it stores
    # first and classifies here. Without this they never reach a digest at all.
    # `state = 'seen'` is the whole condition. It previously also required
    # `category IS NULL`, which excluded every Instagram item — the extractor
    # sets a provisional category, so the filter silently skipped exactly the
    # items it existed to collect. [measured 2026-08-17]
    pending = db.execute(
        "SELECT id, url, title, author, source, tier, domain, form, published, "
        "fetched, body FROM items WHERE state = 'seen'").fetchall()
    for r in pending:
        envelopes.append({**{k: r[k] for k in r.keys()}, "run_id": run_id, "state": "seen"})
    if pending:
        log(f"· {len(pending)} items already extracted by another reach (Instagram)")

    # dedupe within the run — the same URL syndicated twice is one item
    uniq = {}
    for e in envelopes:
        uniq.setdefault(e["id"], e)
    envelopes = list(uniq.values())

    capped = 0
    if a.limit and len(envelopes) > a.limit:
        envelopes.sort(key=lambda e: e["published"] or "", reverse=True)
        capped = len(envelopes) - a.limit
        envelopes = envelopes[:a.limit]
        log(f"· capped at {a.limit} items ({capped} deferred to the next run)")

    if not envelopes:
        log("· nothing new")
        return

    # 2-3 · classify
    log("· classifying")
    classify.run(envelopes, progress=bar("classify"))

    # 4 · cluster
    log("\n· embedding + clustering")
    cluster.embed_all(envelopes, progress=bar("embed"))
    groups = cluster.run(envelopes)
    log(f"\n  {len(groups)} clusters from {len(envelopes)} items")

    # 5 · score, then gate 3 — relevance. Applied after scoring so the digest can
    # still report what was rejected, and before distil/link so the two expensive
    # stages never run on an item that was never in the domain.
    score.run(envelopes)
    off_domain = [e for e in envelopes if not e.get("relevant", True)]
    for e in off_domain:
        e["disposition"] = "drop"
        e["reason"] = "off-domain — not about AI, agents, or building with them"
    if off_domain:
        log(f"· gate 3 dropped {len(off_domain)} off-domain items")

    filed_c = [e for e in envelopes if e["disposition"] == "file"]
    digest_c = [e for e in envelopes if e["disposition"] == "digest"]
    dropped = [e for e in envelopes if e["disposition"] == "drop"]
    for e in dropped:
        # don't overwrite gate 3's reason — an off-domain item can score above
        # the floor, and "1.07 below floor" is a nonsense line in the digest
        e.setdefault("reason", None)
        if not e["reason"]:
            e["reason"] = f"score {e['score']} below floor ({e['category']})"

    # for news/industry, a cluster is duplication: keep the strongest, cite the rest
    collapsed = []
    for cid, members in groups.items():
        dupes = [m for m in members if m["disposition"] == "digest" and
                 m["category"] in ("news", "industry")]
        if len(dupes) > 1:
            keep = cluster.representative(dupes)
            for m in dupes:
                if m is not keep:
                    m["disposition"], m["reason"] = "drop", f"duplicate of “{keep['title'][:40]}”"
                    collapsed.append(m)
    digest_c = [e for e in digest_c if e["disposition"] == "digest"]
    dropped += collapsed
    log(f"· file {len(filed_c)} · digest {len(digest_c)} · drop {len(dropped)}")

    # Persist judgment BEFORE the expensive stages. Classification is ~50 minutes
    # of local model time on a full run; losing it to a kill during distillation
    # is the same mistake as losing fetched items to a kill during classification.
    if not a.dry_run:
        for e in envelopes:
            store.set_fields(db, e["id"], category=e.get("category"),
                             confidence=e.get("confidence"), score=e.get("score"),
                             cluster_id=e.get("cluster_id"))
        db.commit()

    # What may enter the Knowledge Base. Recalibrated 2026-08-18 against the real
    # distribution of 510 stored items — see the long note in `score.py`.
    #
    # The old rule sorted by score and took the top N. That looked principled and
    # was not: 80% of items share a score with another item and 22 sat on exactly
    # 0.96, so among tied items Python's stable sort handed the places to whatever
    # was fetched first. Fetch order chose the Knowledge Base.
    #
    # `select_for_filing` replaces it with a relative keep-fraction per category —
    # volume-invariant by construction, which is what adapting to volume actually
    # requires — then splits the quota by category share, then round-robins
    # across sources inside each. arXiv alone is 53% of the corpus; without the
    # per-source cap it is 53% of the Knowledge Base.
    #
    # The category split was added 2026-08-19 because the round-robin used to run
    # over every source at once with no notion of category, and the quota is far
    # smaller than the eligible pool, so the keep-fractions never bound at all.
    # The mix of what got filed was a side effect of how many feeds happened to
    # carry each kind of thing. It happened to match the stated priority and was
    # held in place by nothing. Every rejection still carries its reason.
    deferred = []
    if filed_c:
        picked, deferred = select_for_filing(filed_c, a.max_file)
        filed_c = picked
        for e in deferred:
            e["disposition"], e["state"] = "digest", "seen"
        digest_c += deferred
        if deferred:
            srcs = len({e.get("source") for e in filed_c})
            log(f"· filing {len(filed_c)} from {srcs} sources; "
                f"{len(deferred)} deferred to the next run")

    # 6-7 · distil and link — only what is actually being filed
    if filed_c:
        log("· distilling")
        distil.run(filed_c, progress=bar("distil"))
        log("\n· linking")
        link.run(db, filed_c, progress=bar("link"))
        orphans = [e for e in filed_c if e.get("novel")]
        log(f"\n  {len(filed_c) - len(orphans)} linked · {len(orphans)} flagged novel")

    # 8 · file
    staged = []
    if not a.dry_run:
        if filed_c:
            staged = filer.stage(filed_c, run_date)
        for e in envelopes:
            e["state"] = {"file": "filed", "digest": "digested", "drop": "dropped"}[e["disposition"]]
            e["entities"] = e.get("entities") or []
            e["summary"] = (e.get("entry") or {}).get("what")
            e["cluster_id"] = e.get("cluster_id")
            e["reason"] = e.get("reason") or (str(e.get("destination")) if e.get("destination") else None)
            store.put_item(db, e)
            if db.vec and e.get("entry_vec"):
                rowid = db.execute("SELECT rowid FROM items WHERE id = ?", (e["id"],)).fetchone()[0]
                db.execute("DELETE FROM vec_items WHERE item_rowid = ?", (rowid,))
                db.execute("INSERT INTO vec_items (item_rowid, embedding) VALUES (?,?)",
                           (rowid, store.serialize(e["entry_vec"])))
        db.commit()

    stats = {"fetched": len(envelopes) + capped, "feeds": len(feeds), "feeds_ok": ok,
             "already_seen": already, "capped": capped}
    path = None
    if not a.dry_run:
        path = filer.digest(run_date, stats, filed_c, digest_c, dropped,
                            feed_status, unreachable, staged)
        db.execute("INSERT INTO runs (run_id, started, finished, stats) VALUES (?,?,?,?) "
                   "ON CONFLICT(run_id) DO UPDATE SET finished=excluded.finished, stats=excluded.stats",
                   (run_id, run_date, dt.datetime.now().isoformat(timespec="seconds"), str(stats)))
        db.commit()

    # 8b · promote into the Knowledge Base. On by default — the funnel owns that
    # folder [@owner · 2026-08-16]. `--no-promote` leaves entries in staging.
    if staged and not a.no_promote:
        plan, err = promote.promote(run_date, apply=True)
        if err:
            log(f"· promote: {err}")
        else:
            counts = {}
            for _, action, _ in plan:
                counts[action] = counts.get(action, 0) + 1
            log(f"· promoted into 01-Knowledge Base: {counts}")

    log(f"· done in {time.time() - started:.0f}s"
        + (f" · digest {path.relative_to(store.VAULT)}" if path else " · dry run, nothing written"))


if __name__ == "__main__":
    main()
