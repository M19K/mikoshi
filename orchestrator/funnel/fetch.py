#!/usr/bin/env python3
"""
fetch.py — Stage 1 (Reach) and Stage 2 (Read) for everything with a feed.

stdlib only. Conditional GET via stored ETag / Last-Modified, so a daily poll of
39 feeds costs almost nothing after the first run.

Two failures are named rather than swallowed, because Sources.md records both as
real and the funnel must not report them as "0 new items":
  · not-a-feed  — the URL returns HTML (a page, or a Cloudflare challenge)
  · blocked     — 403/429 to a plain client; needs the browser path
"""
import hashlib
import html
import re
import urllib.error
import urllib.parse
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
NS = {"atom": "http://www.w3.org/2005/Atom",
      "media": "http://search.yahoo.com/mrss/",
      "content": "http://purl.org/rss/1.0/modules/content/",
      "dc": "http://purl.org/dc/elements/1.1/",
      "yt": "http://www.youtube.com/xml/schemas/2015"}

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"[ \t]*\n\s*\n\s*", re.M)
TRACKING = re.compile(r"^(utm_|ref_?$|source$|mc_|fbclid|gclid)", re.I)


def canonical(url: str) -> str:
    """Strip tracking and fragments so the same link from two feeds hashes alike."""
    try:
        u = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return url.strip()
    q = [(k, v) for k, v in urllib.parse.parse_qsl(u.query) if not TRACKING.match(k)]
    path = u.path.rstrip("/") or "/"
    return urllib.parse.urlunsplit((u.scheme, u.netloc.lower(), path,
                                    urllib.parse.urlencode(q), ""))


def item_id(url: str) -> str:
    return hashlib.sha256(canonical(url).encode()).hexdigest()[:16]


def detext(s: str) -> str:
    """HTML → plain text. Feeds ship escaped markup in <description> constantly."""
    if not s:
        return ""
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", s)
    return WS_RE.sub("\n\n", html.unescape(TAG_RE.sub(" ", s))).strip()


def _date(s: str):
    if not s:
        return None
    s = s.strip()
    for parse in (parsedate_to_datetime,
                  lambda x: datetime.fromisoformat(x.replace("Z", "+00:00"))):
        try:
            d = parse(s)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None


def _text(el, *paths):
    for p in paths:
        found = el.find(p, NS)
        if found is not None:
            if found.text and found.text.strip():
                return found.text
            href = found.get("href")
            if href:
                return href
    return ""


def parse(xml_bytes: bytes):
    """RSS 2.0 and Atom, one code path. Returns raw dicts."""
    root = ET.fromstring(xml_bytes)
    entries = root.findall(".//item") or root.findall(".//atom:entry", NS)
    out = []
    for e in entries:
        link = _text(e, "link", "atom:link[@rel='alternate']", "atom:link", "guid")
        title = detext(_text(e, "title", "atom:title"))
        body = detext(_text(e, "content:encoded", "description", "atom:content",
                            "atom:summary", "media:group/media:description"))
        author = detext(_text(e, "dc:creator", "author/name", "atom:author/atom:name",
                              "author"))
        pub = _date(_text(e, "pubDate", "atom:published", "atom:updated", "dc:date"))
        if link:
            out.append({"url": link.strip(), "title": title, "body": body,
                        "author": author, "published": pub})
    return out


# Reliability, added 2026-08-18.
#
# A network fetch fails for two completely different reasons and the old code
# treated them the same: a timeout is worth trying again in four seconds, a 403
# is worth trying again never. Retrying everything wastes a run; retrying
# nothing loses a feed to one bad moment.
#
# And a feed that has failed every run for a fortnight is not news. It costs a
# timeout on every run and it buries the feed that broke *today* under a wall of
# ones that broke a month ago, which is how a health report stops being read.
# After GIVE_UP_AFTER consecutive failures a feed is quarantined: skipped, and
# reported once as quarantined rather than as a fresh failure. Any success
# clears the streak immediately.

RETRY_STATUSES = ("timeout", "error", "http-5")   # prefix match; transient
RETRIES = 2                                        # 3 attempts total
BACKOFF = 4                                        # seconds, doubling
GIVE_UP_AFTER = 5                                  # consecutive failing runs
QUARANTINE_DAYS = 7


def _transient(status: str) -> bool:
    return any(status.startswith(p) for p in RETRY_STATUSES)


def _terminal(status: str) -> bool:
    """Reached the server and got a definite answer. Not worth another attempt."""
    return status in ("ok", "unchanged", "not-a-feed", "unparseable", "blocked")


def fetch(db, source, max_age_days: int = 3, timeout: int = 30, _attempt: int = 0):
    """One feed. Returns (entries, status). Updates the feed's cache headers.

    Retries transient failures with backoff; quarantines a feed that has failed
    GIVE_UP_AFTER runs in a row.
    """
    row = db.execute(
        "SELECT etag, last_modified, fail_streak, quarantined FROM feeds WHERE url = ?",
        (source.url,)).fetchone()

    if row and row["quarantined"] and _attempt == 0:
        try:
            since = (datetime.now(timezone.utc)
                     - datetime.fromisoformat(row["quarantined"])).days
        except (ValueError, TypeError):
            since = QUARANTINE_DAYS + 1
        if since < QUARANTINE_DAYS:
            return [], "quarantined"
        db.execute("UPDATE feeds SET quarantined = NULL, fail_streak = 0 WHERE url = ?",
                   (source.url,))
        db.commit()
    req = urllib.request.Request(source.url, headers={
        "User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/xml, */*"})
    if row:
        if row["etag"]:
            req.add_header("If-None-Match", row["etag"])
        if row["last_modified"]:
            req.add_header("If-Modified-Since", row["last_modified"])

    status, entries, etag, lastmod = "ok", [], None, None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            etag, lastmod = r.headers.get("ETag"), r.headers.get("Last-Modified")
            ctype = (r.headers.get("Content-Type") or "").lower()
        head = raw[:400].lstrip().lower()
        if head.startswith(b"<!doctype html") or head.startswith(b"<html") or "text/html" in ctype:
            status = "not-a-feed"
        else:
            try:
                entries = parse(raw)
            except ET.ParseError:
                status = "unparseable"
    except urllib.error.HTTPError as e:
        status = "unchanged" if e.code == 304 else (
            "blocked" if e.code in (401, 403, 405, 429) else f"http-{e.code}")
    except Exception as e:
        status = f"error:{type(e).__name__}"

    # Retry transient failures in-run. A run that gives up on the first timeout
    # loses the feed's items entirely, because conditional GET hands them over
    # once — that is a recorded failure mode in this vault, not a hypothetical.
    if _transient(status) and _attempt < RETRIES:
        time.sleep(BACKOFF * (2 ** _attempt))
        return fetch(db, source, max_age_days, timeout, _attempt + 1)

    # Decide the streak before the UPSERT so `status` is final when it is stored,
    # and apply it after, so the row is guaranteed to exist. Doing the UPDATE
    # first silently lost every feed's *first* failure: on a brand-new feed there
    # is no row to update, and the INSERT that follows recreates it at 0.
    if _terminal(status):
        new_streak, quarantine = 0, None
    else:
        new_streak = (row["fail_streak"] if row and row["fail_streak"] else 0) + 1
        quarantine = (datetime.now(timezone.utc).isoformat(timespec="seconds")
                      if new_streak >= GIVE_UP_AFTER else None)
        if quarantine:
            status = f"quarantined-after-{new_streak}"

    if max_age_days and entries:
        floor = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        entries = [e for e in entries if not e["published"] or e["published"] >= floor]

    db.execute(
        "INSERT INTO feeds (url, etag, last_modified, last_fetched, last_status, items_seen) "
        "VALUES (?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET "
        "etag=COALESCE(excluded.etag, feeds.etag), "
        "last_modified=COALESCE(excluded.last_modified, feeds.last_modified), "
        "last_fetched=excluded.last_fetched, last_status=excluded.last_status, "
        "items_seen=feeds.items_seen+excluded.items_seen",
        (source.url, etag, lastmod, datetime.now(timezone.utc).isoformat(timespec="seconds"),
         status, len(entries)))
    db.execute("UPDATE feeds SET fail_streak = ?, quarantined = ? WHERE url = ?",
               (new_streak, quarantine, source.url))
    return entries, status
