# RSSHub — the bridge for sources with no feed

Some sources publish nothing an RSS reader can use. RSSHub turns their web pages
into feeds, running entirely on this machine.

```bash
cd 05-Orchestrator/rsshub
docker compose up -d          # start
curl -s localhost:1200/anthropic/news | head    # check
docker compose logs -f        # watch
docker compose down           # stop
```

## What it unlocks

| Source | Route | Login needed |
|---|---|---|
| Anthropic news | `/anthropic/news` | **No — working now** |
| X, per account | `/twitter/user/<handle>` | Yes, your cookie |
| X, a list | `/twitter/list/<id>` | Yes, your cookie |

Anthropic publishes no RSS at all — `/rss.xml`, `/news/rss.xml` and `/feed.xml`
are all 404 and the page carries no autodiscovery. This route is the only way to
follow it without a human checking the page.

## The X step, which is yours

X shut off free API access, so RSSHub reads it as a logged-in browser would.
That needs two cookie values from your own session. **No agent reads or handles
them** — copy `.env.example` to `.env`, paste, and restart.

Without `.env` the X routes return 503 and everything else keeps working. The
failure is contained, not total.

## Why 88 accounts is one route, not 88

Each handle is a separate fetch and X rate-limits hard. Put the accounts in a
single private X list and the whole tier collapses to `/twitter/list/<id>` —
one request per run instead of eighty-eight. Until then the funnel polls the
tooling bucket only, which is the 30 that carry the priority.
