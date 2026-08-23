#!/usr/bin/env python3
"""
registry.py — read the source list out of `Sources.md`.

There is no second copy of the feed list. `Sources.md` is the registry a human
maintains and the registry the funnel reads; a config file duplicating it would
drift the first time the owner added a feed. Markdown on disk is the protocol.

Tiers 1 and 2 are fetchable with nothing but HTTP. Tiers 3-5 are parsed too, so
the funnel can report what it could not reach instead of silently omitting it.
"""
import os
import pathlib
import re

ORCH = pathlib.Path(__file__).resolve().parent.parent
SOURCES_MD = ORCH / "Sources.md"

YT_FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
# Self-hosted RSSHub, started 2026-08-17. Turns sources that publish no feed
# into ones that do. See `05-Orchestrator/rsshub/`.
# Self-hosted by whoever wants the X tier, and nowhere by default.
RSSHUB = os.environ.get("MIKOSHI_RSSHUB_URL", "http://localhost:1200")
X_ROUTE = RSSHUB + "/twitter/user/{}"

# Which X buckets the funnel actually polls. Each handle is a separate request
# and X rate-limits hard, so only the bucket carrying the stated priority is
# fetched. Put all 88 in one private X list and this collapses to one route.
# Empty on purpose. One route — the Following timeline — covers all 88 accounts
# in a single request, so polling handles individually is pure rate-limit risk
# for no extra coverage. Put a handle here only to follow it more closely than
# the timeline allows. [2026-08-17]
X_BUCKETS_POLLED = set()
X_TIMELINE = RSSHUB + "/twitter/home_latest"
TIER_RE = re.compile(r"^##\s+Tier\s+(\d+)\s*[—-]\s*(.+)$")
ROW_RE = re.compile(r"^\|(.+)\|\s*$")
CODE_RE = re.compile(r"`([^`]+)`")
BOLD_RE = re.compile(r"\*\*(.*?)\*\*")


STRUCK_RE = re.compile(r"^~~.*~~$")


def _clean(cell: str) -> str:
    return BOLD_RE.sub(r"\1", cell).strip()


def _retired(name: str) -> bool:
    """`~~Struck through~~` marks an entry as dead. Honour it — otherwise the
    funnel keeps fetching a URL the registry has already recorded as not a feed."""
    return bool(STRUCK_RE.match(name))


def _cells(line: str):
    m = ROW_RE.match(line)
    if not m:
        return None
    return [_clean(c) for c in m.group(1).split("|")]


class Source(dict):
    """A row of the registry. `reach` says how the funnel can get to it."""
    __getattr__ = dict.get


PLACEHOLDER = ("example.com", "example.org", "replace me", "<your")


def _is_placeholder(src) -> bool:
    """A row that exists to show the shape, not to be fetched.

    The shipped starter registry carries one, and it PARSED AS A SOURCE — so
    the empty-registry guard never fired and the funnel reported "0/0 feeds,
    nothing new", which reads as a healthy run on a vault that ingests nothing.
    Caught by CI on three platforms, 2026-08-23.
    """
    blob = " ".join(str(v) for v in dict(src).values()).lower()
    return any(t in blob for t in PLACEHOLDER)


def load(path: pathlib.Path = SOURCES_MD):
    """Every source in the registry, in file order."""
    if not path.is_file():
        raise SystemExit(
            f"NO-SOURCES: no source registry at {path}.\n\n"
            f"Nothing is ingested until you say what to read — that is "
            f"deliberate.\nCopy `templates/Sources.md` there, or re-run "
            f"`init.py` to see the shape.")
    tier, bucket, out = None, '', []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            t = TIER_RE.match(line)
            # a non-Tier H2 closes the current tier — "Needs action" and
            # "Notes on method" carry tables that are not sources
            tier = int(t.group(1)) if t else None
            continue
        if line.startswith("### "):
            # an H3 inside a tier is commentary, and commentary carries tables
            # too (the not-a-feed findings). Close the tier rather than read them.
            tier = None
            continue
        if tier is None:
            continue
        cells = _cells(line)

        if tier in (1, 2) and cells and len(cells) >= 3:
            name, mid, domain = cells[0], cells[1], cells[2]
            if (not name or name in ("Source", "Channel") or set(name) <= set("-: ")
                    or _retired(name)):
                continue
            token = (CODE_RE.search(mid).group(1) if CODE_RE.search(mid) else mid).strip()
            if tier == 1 and not token.startswith("http"):
                continue
            out.append(Source(
                name=name, tier=tier, domain=domain, reach="http",
                url=token if tier == 1 else YT_FEED.format(token),
                platform="web" if tier == 1 else "youtube",
                form="article" if tier == 1 else "video"))

        elif tier in (3, 4) and line.startswith("**") and "**" in line[2:]:
            # The bold label above each handle block names the bucket. It does
            # not end in `**` — these read `**Tooling & implementation —
            # PRIMARY** — 30`, so match the closing marker, not the line end.
            label = BOLD_RE.search(line).group(1).lower()
            bucket = ("tooling" if "tooling" in label else
                      "frontier" if "frontier" in label else
                      "startup" if "startup" in label else "business")
            continue

        elif tier in (3, 4) and line.startswith("`"):
            # handle blocks: `a` · `b` · `c`
            for h in CODE_RE.findall(line):
                handle = h.lstrip("@")
                polled = tier == 4 and bucket in X_BUCKETS_POLLED
                out.append(Source(
                    name=h, tier=tier, domain=bucket, bucket=bucket,
                    url=X_ROUTE.format(handle) if polled else "",
                    platform="instagram" if tier == 3 else "x",
                    form="carousel" if tier == 3 else "thread",
                    reach="http" if polled else ("ingest.sh" if tier == 3 else "rsshub")))

        elif tier == 5 and cells and len(cells) >= 2:
            name, status = cells[0], cells[1]
            if not name or name == "Source" or set(name) <= set("-: "):
                continue
            out.append(Source(
                name=name, tier=5, domain="newsletter", url="",
                platform="email", form="newsletter",
                reach="agentmail" if status.startswith("✅") else "blocked",
                note=status))
    real = [x for x in out if not _is_placeholder(x)]
    if not real:
        raise SystemExit(
            f"NO-SOURCES: {path} declares only placeholders.\n\n"
            f"That is the starting state, not a fault. Replace the example row "
            f"with a real feed\nand verify it by fetching and counting items: "
            f"a 200 and a plausible title are\nnot a feed. Then run again.")
    return real


def timeline_source():
    """The whole X tier as one request."""
    return Source(name="X — following timeline", tier=4, domain="tooling",
                  bucket="mixed", url=X_TIMELINE, platform="x", form="thread",
                  reach="http")


def bucket_of(handle):
    """Which bucket an X handle belongs to, for weighting timeline items."""
    h = handle.lower().lstrip("@")
    for s in load():
        if s.tier == 4 and s.name.lower().lstrip("@") == h:
            return s.bucket
    return "startup"


def fetchable(sources=None):
    """Everything reachable over plain HTTP, plus the X timeline as one route."""
    srcs = sources or load()
    out = [s for s in srcs if s.reach == "http"]
    if any(s.tier == 4 for s in srcs):
        out.append(timeline_source())
    return out


def unreachable(sources=None):
    """Everything the funnel knows about but cannot pull itself. Reported, never hidden."""
    return [s for s in (sources or load()) if s.reach != "http"]


if __name__ == "__main__":
    srcs = load()
    for tier in sorted({s.tier for s in srcs}):
        rows = [s for s in srcs if s.tier == tier]
        reach = sorted({s.reach for s in rows})
        print(f"tier {tier}: {len(rows):3d} sources  reach={','.join(reach)}")
    print(f"\nfetchable now: {len(fetchable(srcs))}")
