"""
The source registry is markdown a human maintains, so every test here is a
piece of markdown someone could plausibly write.

**The regression that motivates the file.** On 2026-08-23 CI on three
platforms reported `0/0 feeds, nothing new` and exited 0 on a vault that
ingested nothing — because the shipped starter registry's example row parsed
as a real source, so the empty-registry guard never fired. A healthy-looking
run over an empty corpus is the worst failure this system can have: it is
indistinguishable from a quiet day.
"""
import pathlib

import pytest

from funnel import registry


def write(tmp_path, body: str) -> pathlib.Path:
    p = tmp_path / "Sources.md"
    p.write_text(body, encoding="utf-8")
    return p


REAL = """
## Tier 1 — Web

| Source | Feed | Domain |
|---|---|---|
| Simon Willison | `https://simonwillison.net/atom/everything/` | tooling |
| ~~Dead Blog~~ | `https://dead.invalid/feed` | tooling |

## Tier 2 — YouTube

| Channel | Channel id | Domain |
|---|---|---|
| Fireship | `UCsBjURrPoezykLs9EqgamOA` | tooling |
"""


def test_tier_one_and_two_parse(tmp_path):
    srcs = registry.load(write(tmp_path, REAL))
    names = [s.name for s in srcs]
    assert "Simon Willison" in names
    assert "Fireship" in names

    web = next(s for s in srcs if s.name == "Simon Willison")
    assert web.tier == 1 and web.platform == "web" and web.form == "article"
    assert web.url == "https://simonwillison.net/atom/everything/"

    yt = next(s for s in srcs if s.name == "Fireship")
    assert yt.tier == 2 and yt.platform == "youtube"
    # A channel id is not a URL. The registry's job is to turn it into one.
    assert yt.url == registry.YT_FEED.format("UCsBjURrPoezykLs9EqgamOA")


def test_struck_through_rows_are_retired_not_fetched(tmp_path):
    """`~~like this~~` is how a human records "we checked, it is not a feed".

    Ignoring it means re-fetching a URL the registry already knows is dead,
    every run, forever."""
    srcs = registry.load(write(tmp_path, REAL))
    assert not any("Dead Blog" in s.name for s in srcs)


def test_a_registry_of_only_placeholders_refuses_instead_of_reporting_zero(tmp_path):
    """The 2026-08-23 regression, pinned.

    The starter registry must not look like a working one. `SystemExit`
    carrying `NO-SOURCES:` is the contract CI matches on — deliberately a
    stable token rather than the wording, because the first version of this
    check asserted the prose and broke when the message was improved."""
    body = """
## Tier 1 — Web

| Source | Feed | Domain |
|---|---|---|
| Example Feed | `https://example.com/feed.xml` | tooling |
"""
    with pytest.raises(SystemExit) as e:
        registry.load(write(tmp_path, body))
    assert "NO-SOURCES:" in str(e.value)


def test_a_placeholder_beside_a_real_source_does_not_block_the_run(tmp_path):
    """The guard is about an *entirely* unconfigured registry. Someone who has
    added one real feed and left the example above it is configured."""
    body = REAL + """
| Example Feed | `https://example.com/feed.xml` | tooling |
"""
    srcs = registry.load(write(tmp_path, body))
    assert all("example.com" not in (s.url or "") for s in srcs)
    assert any(s.name == "Simon Willison" for s in srcs)


def test_a_missing_registry_says_what_to_do(tmp_path):
    """The README's own first command used to raise FileNotFoundError here."""
    with pytest.raises(SystemExit) as e:
        registry.load(tmp_path / "nope.md")
    msg = str(e.value)
    assert "NO-SOURCES:" in msg
    assert "templates/Sources.md" in msg


def test_a_non_tier_heading_closes_the_tier(tmp_path):
    """`## Needs action` and `## Notes on method` carry tables that are not
    sources. Reading them as sources is how a findings table becomes a feed
    list."""
    body = REAL + """
## Needs action

| Source | Feed | Domain |
|---|---|---|
| Not A Feed | `https://notafeed.invalid/x` | tooling |
"""
    srcs = registry.load(write(tmp_path, body))
    assert not any(s.name == "Not A Feed" for s in srcs)


def test_an_h3_inside_a_tier_closes_it_too(tmp_path):
    body = """
## Tier 1 — Web

| Source | Feed | Domain |
|---|---|---|
| Simon Willison | `https://simonwillison.net/atom/everything/` | tooling |

### Checked and rejected

| Source | Feed | Domain |
|---|---|---|
| Wrong Publication | `https://wrong.invalid/feed` | tooling |
"""
    srcs = registry.load(write(tmp_path, body))
    assert [s.name for s in srcs] == ["Simon Willison"]


def test_tier_one_rows_without_a_url_are_skipped(tmp_path):
    """Tier 1 is "fetchable over plain HTTP". A row with a note where the feed
    should be is a note, and fetching it would raise somewhere unrelated."""
    body = """
## Tier 1 — Web

| Source | Feed | Domain |
|---|---|---|
| Simon Willison | `https://simonwillison.net/atom/everything/` | tooling |
| Paywalled Thing | no public feed | business |
"""
    srcs = registry.load(write(tmp_path, body))
    assert [s.name for s in srcs] == ["Simon Willison"]


def test_header_and_separator_rows_are_not_sources(tmp_path):
    srcs = registry.load(write(tmp_path, REAL))
    assert not any(s.name in ("Source", "Channel") for s in srcs)
    assert not any(set(s.name) <= set("-: ") for s in srcs)


def test_fetchable_and_unreachable_partition_every_source(tmp_path):
    """Nothing may be quietly dropped: a source the funnel cannot reach is
    *reported* as unreachable, never omitted."""
    body = REAL + """
## Tier 5 — Newsletters

| Source | Status |
|---|---|
| Import AI | ✅ subscribed |
| Stratechery | ❌ paid, no forwarding |
"""
    srcs = registry.load(write(tmp_path, body))
    reachable = {s.name for s in registry.fetchable(srcs)}
    blocked = {s.name for s in registry.unreachable(srcs)}
    # Every row lands on exactly one side. `fetchable()` may additionally add
    # the X timeline, which is a route rather than a row, so compare against
    # the registry's own names.
    assert reachable.isdisjoint(blocked)
    assert {s.name for s in srcs} <= (reachable | blocked)
    assert blocked >= {"Import AI", "Stratechery"}
    assert reachable >= {"Simon Willison", "Fireship"}


def test_tier_five_status_decides_reach_not_presence(tmp_path):
    body = """
## Tier 5 — Newsletters

| Source | Status |
|---|---|
| Import AI | ✅ subscribed |
| Stratechery | ❌ paid, no forwarding |
"""
    srcs = registry.load(write(tmp_path, body))
    by = {s.name: s for s in srcs}
    assert by["Import AI"].reach == "agentmail"
    assert by["Stratechery"].reach == "blocked"


def test_the_rsshub_address_is_overridable(monkeypatch):
    """No-hardcoding rule, checked rather than trusted: not everyone runs
    RSSHub on this machine, on this port."""
    import importlib
    monkeypatch.setenv("MIKOSHI_RSSHUB_URL", "http://rsshub.internal:1200")
    r = importlib.reload(registry)
    try:
        assert r.X_TIMELINE.startswith("http://rsshub.internal:1200")
    finally:
        monkeypatch.delenv("MIKOSHI_RSSHUB_URL", raising=False)
        importlib.reload(registry)
