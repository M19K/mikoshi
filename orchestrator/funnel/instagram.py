#!/usr/bin/env python3
"""
instagram.py — the missing half of Instagram ingestion.

`ingest.sh` has worked since 2026-08-10 and could never run on its own, because
it takes **one URL you hand it**. Nothing answered "what did these 25 accounts
post today". It was an extractor with no feed in front of it — which is why
Instagram, the source the whole priority is built around, has never ingested
a single item.

This is the feed. `gallery-dl --no-download` lists a profile without fetching
media, so discovery is cheap; only genuinely new posts cost a download.

**Instagram is first-class, not a fallback** (`Information Lifecycle.md`). It is
where practical implementation surfaces first, and it is why most of the Recall
corpus is Instagram. It is also the most fragile source in the registry, so
every design choice here is about failing softly:

  · one account failing never stops the others
  · already-seen posts are skipped before any network cost
  · a login redirect stops that account for the run rather than hammering it
  · nothing is deleted on failure; the next run simply tries again

Auth is the owner's own Chrome session, read at call time. No agent account, ever.

    python3 -m funnel.instagram list            # what is new, download nothing
    python3 -m funnel.instagram list --bucket tooling --per 6
    python3 -m funnel.instagram ingest --limit 5   # actually fetch and read them
"""
import argparse
import datetime as dt
import json
import pathlib
import subprocess
import sys

from . import fetch, registry, store

INGEST_SH = store.VAULT / "02-Projects/project-four/code/tools/ingest.sh"
MEDIA = store.ORCH / "state" / "instagram"

# Instagram rate-limits hard and answers with a login redirect rather than an
# error code. Seeing it means back off for the rest of the run.
BLOCKED_HINTS = ("login", "challenge", "429", "rate limit", "checkpoint")


def accounts(bucket=None):
    """Tier 3 handles from the registry. `Sources.md` stays the only source list."""
    out = [s for s in registry.load() if s.tier == 3]
    if bucket:
        out = [s for s in out if s.bucket == bucket]
    return out


def list_posts(handle, per=6, timeout=180):
    """Recent posts for one account. Lists only — downloads nothing.

    Returns (posts, status). `posts` are dicts with url, date and caption.
    """
    # `--no-download`, NOT `--simulate`. Simulate suppresses `--print`, so the
    # obvious combination lists nothing at all and every account reports empty.
    # `--range` counts FILES, not posts, and `--print` fires once per file — so a
    # 20-slide carousel emits the same post_url twenty times. Ask for a generous
    # file window, then collapse to unique posts below. [measured 2026-08-17]
    cmd = ["gallery-dl", "--cookies-from-browser", "chrome", "--no-download",
           "--range", f"1-{per * 12}",
           "--print", "{post_url}\t{date}\t{description}",
           f"https://www.instagram.com/{handle.lstrip('@')}/"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return [], "timeout"

    blob = (r.stdout or "") + (r.stderr or "")
    if any(h in blob.lower() for h in BLOCKED_HINTS) and not r.stdout.strip():
        return [], "blocked"

    posts, seen_urls = [], set()
    for line in (r.stdout or "").splitlines():
        if not line.startswith("http"):
            continue
        parts = line.split("\t")
        url = parts[0].strip()
        if url in seen_urls:          # same post, another slide
            continue
        seen_urls.add(url)
        posts.append({
            "url": url,
            "date": parts[1].strip() if len(parts) > 1 else "",
            "caption": (parts[2].strip() if len(parts) > 2 else "")[:400],
        })
        if len(posts) >= per:
            break
    if not posts:
        return [], "empty" if r.returncode == 0 else f"error-{r.returncode}"
    return posts, "ok"


def new_only(db, posts, source_name):
    """Drop anything already in the store. Deduping before download is the
    whole reason listing is separate from ingesting."""
    out = []
    for p in posts:
        iid = fetch.item_id(p["url"])
        if store.seen(db, iid):
            continue
        p["id"] = iid
        p["source"] = source_name
        out.append(p)
    return out


def survey(bucket=None, per=6, db=None, progress=None):
    """What is new across every account, without downloading anything."""
    db = db or store.connect()
    found, statuses, blocked = [], {}, False
    accs = accounts(bucket)
    for i, s in enumerate(accs, 1):
        if blocked:
            statuses[s.name] = "skipped-after-block"
            continue
        posts, status = list_posts(s.name, per=per)
        statuses[s.name] = status
        if status == "blocked":
            blocked = True                 # back off for the rest of the run
        found.extend(new_only(db, posts, s.name))
        if progress:
            progress(i, len(accs))
    return found, statuses, blocked


def ingest_one(post, timeout=900):
    """Hand one post to `ingest.sh` — video to keyframes plus transcript,
    carousel to slides. Returns the output directory, or None."""
    MEDIA.mkdir(parents=True, exist_ok=True)
    out = MEDIA / post["id"]
    if out.exists() and any(out.iterdir()):
        return out
    out.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(["bash", str(INGEST_SH), post["url"], str(out)],
                       capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    return out if any(out.iterdir()) else None


def read_extract(folder):
    """Whatever `ingest.sh` produced, as text the funnel can classify."""
    if not folder:
        return ""
    bits = []
    for name in ("transcript.txt", "caption.txt", "description.txt"):
        f = folder / name
        if f.exists():
            bits.append(f.read_text(encoding="utf-8", errors="ignore")[:4000])
    srt = next(folder.glob("*.srt"), None)
    if srt and not bits:
        bits.append(srt.read_text(encoding="utf-8", errors="ignore")[:4000])
    info = next(folder.glob("*.info.json"), None)
    if info:
        try:
            d = json.loads(info.read_text(encoding="utf-8"))
            bits.append(str(d.get("description") or "")[:2000])
        except (json.JSONDecodeError, OSError):
            pass
    frames = len(list((folder / "frames").glob("*.jpg"))) if (folder / "frames").exists() else 0
    slides = len(list((folder / "slides").glob("*"))) if (folder / "slides").exists() else 0
    if frames or slides:
        bits.append(f"[{frames} keyframes, {slides} slides extracted]")
    return "\n\n".join(b for b in bits if b.strip())


def to_envelope(post, text):
    return {
        "id": post["id"], "url": post["url"],
        "title": (post.get("caption") or "").split("\n")[0][:120] or f"Instagram {post['source']}",
        "author": post["source"], "source": f"Instagram {post['source']}",
        "tier": 3, "domain": "tooling", "form": "carousel",
        "published": post.get("date") or None,
        "fetched": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "body": text or post.get("caption", ""), "state": "seen",
        "entities": [], "run_id": "instagram",
    }


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("list", "ingest"):
        s = sub.add_parser(name)
        s.add_argument("--bucket", default=None, help="tooling · startup · business")
        s.add_argument("--per", type=int, default=6, help="posts to check per account")
        if name == "ingest":
            s.add_argument("--limit", type=int, default=5,
                           help="max posts to actually download this run")
    a = ap.parse_args()

    db = store.connect()
    def bar(i, n):
        print(f"  surveying {i}/{n}", end="\r", file=sys.stderr, flush=True)

    found, statuses, blocked = survey(a.bucket, per=a.per, db=db, progress=bar)
    print(file=sys.stderr)

    ok = sum(1 for v in statuses.values() if v == "ok")
    print(f"{len(found)} new posts across {ok}/{len(statuses)} accounts reachable")
    bad = {k: v for k, v in statuses.items() if v != "ok"}
    if bad:
        print("not reached: " + ", ".join(f"{k} ({v})" for k, v in list(bad.items())[:8]))
    if blocked:
        print("⚠ Instagram started redirecting to login — backed off for this run.")

    for p in found[:20]:
        print(f"  {p['source']:22} {p['date'][:10]:12} {p['caption'][:52]}")

    if a.cmd == "list":
        print("\n(list only — `ingest` downloads and reads them)")
        return

    todo = found[:a.limit]
    print(f"\ningesting {len(todo)} of {len(found)}"
          + (f" — {len(found) - len(todo)} deferred to the next run" if len(found) > len(todo) else ""))
    done = 0
    for p in todo:
        try:
            folder = ingest_one(p)
        except Exception as e:
            # one post must never take the run down; it has already cost a download
            print(f"  FAIL  {p['source']:20} {type(e).__name__}: {e}")
            continue
        text = read_extract(folder)
        env = to_envelope(p, text)
        env["category"], env["confidence"] = "tooling", 0.5
        env["reason"] = "instagram: extracted, awaiting classification"
        store.put_item(db, env)
        db.commit()
        done += 1
        print(f"  {'ok ' if text else 'thin'}  {p['source']:20} {len(text):5d} chars  {p['url']}")
    print(f"\n{done} ingested into the store, ready for the next funnel run.")


if __name__ == "__main__":
    main()
