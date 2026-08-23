---
tags: [orchestrator, sources, example]
created: <YYYY-MM-DD>
---

# Sources — example registry

**This is an example, not a recommendation.** The funnel reads this file to know
what to fetch. Replace every row with your own; the shapes are what matter.

**Verify a feed by fetching it and counting items, never by getting a 200.**
A domain that looks right can return a live feed for an entirely different
publication, and that quietly poisons the corpus.

## RSS

| Name | URL | Category | Notes |
|---|---|---|---|
| Example Blog | https://example.com/feed.xml | tooling | verified <date>, 20 items |

## YouTube

| Channel | Channel ID | Category |
|---|---|---|
| Example | UC................ | tooling |

## Social

| Platform | Route | Notes |
|---|---|---|
| X | self-hosted RSSHub, Following timeline | one request covers everyone you follow |
| Instagram | account handles via the ingest script | first-class, not a fallback |

## Newsletters

| Inbox | How it is read |
|---|---|
| <address> | a scheduled session holds the mail connector and drops bodies to `state/newsletters/` |
