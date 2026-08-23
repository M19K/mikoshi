---
tags: [orchestrator, sources]
created: <YYYY-MM-DD>
---

# Sources

**The funnel reads this file to know what to fetch. It is empty on purpose.**

Nothing is ingested until you say what to read. An empty file is a valid state —
the funnel will tell you there is nothing declared and stop, rather than
guessing at feeds you never asked for.

> **Verify a feed by fetching it and counting items, never by getting a 200.**
> A domain that looks right can serve a live feed for an entirely different
> publication, and that quietly poisons the corpus in a way nothing downstream
> can detect. One source in the vault this came from returned 200 with 17 live
> items and was an unrelated Australian magazine.

## RSS

Delete the example row. It is there to show the shape, not to be used.

| Name | URL | Category | Verified |
|---|---|---|---|
| _Example Blog_ | _https://example.com/feed.xml_ | _tooling_ | _replace me_ |

## YouTube

| Channel | Channel ID | Category |
|---|---|---|

## Social

**Both of these need something on your machine and neither is on by default.**

| Platform | What it needs | Status |
|---|---|---|
| X | a self-hosted RSSHub at `localhost:1200` | off unless you run one |
| Instagram | `gallery-dl` plus a logged-in Chrome profile it can read cookies from | off unless both exist |

## Newsletters, mail, meetings, documents

**These are not fetched by a script and cannot be.** They are read by a
scheduled *session* that already holds those connections, which is what avoids
an API key existing anywhere. See `05-Orchestrator/routines/` for the prompts
that do it, and `funnel.connectors status` for whether anything is arriving.

| Tier | Scope you must set |
|---|---|
| Newsletters | a dedicated intake address — never a personal inbox |
| Mail | one label you apply by hand; the label is the consent |
| Meetings | nothing to set; personal-looking titles are dropped |
| Drive | folders declared in `state/drive/scope.json` — **undeclared means nothing is read, not everything** |
