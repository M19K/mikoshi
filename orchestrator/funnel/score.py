#!/usr/bin/env python3
"""
score.py — Stage 5. One number deciding what files, what only appears in the
digest, and what dies.

Every score decomposes into named terms and the terms are printed. A score you
cannot explain is a score nobody trusts — same reason `vault_check.py` is
deterministic.

The weights encode the stated priority, not a guess:
tooling > workflow > concept > industry > news.
"""
from datetime import datetime, timezone

CATEGORY_WEIGHT = {"tooling": 1.00, "workflow": 0.85, "concept": 0.80,
                   "industry": 0.45, "news": 0.20}

# File above this. Below it, an item is digest-only or dropped.
# **Measured against the whole corpus, 2026-08-22 — 2,184 scored items.** The
# board carried "recalibrate this against the real distribution" for days. Done,
# and the answer is that the floor cannot be recalibrated into usefulness,
# because the problem is not its height.
#
#   what it rejects today   34 of 1,478 filing-category items — 2.3%
#   the gap it sits in      best news 0.220 · best industry 0.498 · lowest
#                           filing-category item 0.360, and only 12 filing
#                           items score at or below the best industry item
#   so it separates         news and industry from the rest, which the category
#                           weights already did. Inside a filing category it
#                           does almost nothing.
#
# **The reason raising it cannot work, and this is the finding:** `concept` has
# no resolution. 594 of 724 concepts clear 0.75 and only 160 clear 0.80 — 434
# items live inside one twentieth of the scale. Any floor is therefore either a
# no-op or a cliff, with nothing in between to tune. Tooling is broader (0.480
# to 1.698) but shares the shape.
#
# So a better number does not exist to be found, and picking one would be the
# guess this vault's second rule forbids. **The real work is giving the score
# within-category variance**, which is a change to how it is computed, not to
# where the line sits. Left at 0.62 deliberately: an honest no-op is better than
# a cliff nobody can predict, and the cap plus CATEGORY_SHARE are what actually
# decide the corpus today.
FILE_FLOOR = 0.62
DIGEST_FLOOR = 0.30


def recency(published) -> float:
    if not published:
        return 0.5
    if isinstance(published, str):
        try:
            published = datetime.fromisoformat(published)
        except ValueError:
            return 0.5
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - published).total_seconds() / 86400
    return 1.0 if age <= 1 else 0.85 if age <= 3 else 0.7 if age <= 7 else 0.5


def score(env: dict) -> dict:
    cat = env.get("category", "news")
    terms = {
        "category": CATEGORY_WEIGHT.get(cat, 0.2),
        "confidence": 0.6 + 0.4 * float(env.get("confidence") or 0.5),
        "recency": recency(env.get("published")),
        "source": 1.12 if "tooling" in (env.get("domain") or "").lower() else 1.0,
    }

    # independent sources, not item count — one outlet posting three times about
    # its own launch is not three people adopting anything
    n = len(env.get("cluster_sources") or []) or 1
    if cat in ("tooling", "workflow", "concept"):
        # adoption signal — more independent sources is stronger, not redundant
        terms["adoption"] = min(1.0 + 0.14 * (n - 1), 1.6)
    else:
        # one story told five times is still one story
        terms["adoption"] = 1.0

    # a named repo or install line is the difference between "a tool exists"
    # and "here is how you run it"
    sig = set(env.get("signals") or [])
    terms["actionable"] = 1.10 if sig & {"repo", "install", "mcp"} else 1.0

    total = 1.0
    for v in terms.values():
        total *= v
    env["score_terms"] = terms
    env["score"] = round(min(total, 2.0), 4)
    env["disposition"] = ("file" if env["score"] >= FILE_FLOOR and cat != "news"
                          else "digest" if env["score"] >= DIGEST_FLOOR else "drop")
    return env


# --- selection -------------------------------------------------------------
#
# Measured 2026-08-18 over 510 stored items. The absolute floor was not the
# whole defect and "it does not scale with volume" was not the whole diagnosis.
# Three things were wrong, and the third is the one that actually chose wrongly:
#
# 1 · THE CORPUS CHANGED, NOT THE FLOOR. arXiv cs.AI supplies 268 of 510 items
#     — 53% of everything — at avg 0.86, higher than most feeds. The run that
#     passed 24 of 101 predates it; the run that passed 297 of 409 is that one
#     feed arriving. A floor calibrated before a firehose exists passes almost
#     everything once it does.
#
# 2 · THE SCORE HAS ALMOST NO RESOLUTION. It is a product of terms that are
#     nearly all constant on a fresh item: recency is 1.0 for anything under a
#     day, and source/actionable/adoption are binary. 80% of items share a score
#     with another item; 22 sit on exactly 0.96.
#
# 3 · SO THE CAP CHOSE BY FETCH ORDER. Python's sort is stable, so among 22 tied
#     items the ones that filed were whichever arrived first. That is how
#     "Does Mark Zuckerberg really believe AI is 'for everyone'?" filed at 0.82
#     while "Comment SKILL to get Agent Skills" sat deferred at 0.98.
#
# The fix is to stop asking one number to do a job it cannot do. Selection is
# now diversity-first: a relative keep-fraction per category (volume-invariant
# by construction, which is what "scales with volume" actually requires), then
# round-robin across sources so no single feed can take the quota.

# Fraction of a category's run population eligible to file. Relative, so the
# meaning does not drift when volume quadruples.
#
# MEASURED 2026-08-19, and the number matters: on the 729-item run this leaves
# **99 eligible** out of 423 above the floor. So the standing worry that "only
# the per-run cap of 40 stops a dump" is not what the code does — remove the cap
# and 99 file, not 423. The cap is not the lever, and neither is FILE_FLOOR,
# which passes 97% of concept, 95% of tooling and 90% of workflow while
# excluding industry and news *by construction*, since CATEGORY_WEIGHT already
# caps their maximum possible score below it. The floor separates categories,
# which the weights already did, and almost nothing within one.
CATEGORY_KEEP = {"tooling": 0.30, "workflow": 0.40, "concept": 0.15}

# No single source may take more than this share of one run's filing quota.
# arXiv is 53% of the corpus; without this it is 53% of the Knowledge Base.
MAX_SOURCE_SHARE = 0.25

# Share of the run's filing quota each category gets.
#
# **Why this exists.** Until now the quota was handed out by a round-robin over
# SOURCES with no notion of category, and because the quota (40) is far smaller
# than the eligible pool (99), CATEGORY_KEEP never bound at all — it was
# computed and then made no difference. The category mix of what got filed was
# therefore a side effect of how many feeds happened to carry each category.
# Measured on the 2026-08-19 run: 34 tooling, 4 concept, 2 workflow, out of 28
# sources each handed one or two places.
#
# That outcome is roughly right and it was arrived at for no reason. It is held
# in place by nothing: add four more arXiv-shaped concept feeds tomorrow and the
# mix swings, with no rule anywhere objecting. The stated priority — practical
# tooling first, ecosystem as context for it — deserves to be a number that is
# read, not an accident that happens to agree.
#
# **Set to preserve the measured behaviour**, deliberately: this change makes
# the mix explicit without altering what gets filed today. The ratio itself is
# @owner's call, since what counts as signal is his; it is one line to move.
CATEGORY_SHARE = {"tooling": 0.85, "concept": 0.10, "workflow": 0.05}


def select_for_filing(candidates, quota):
    """Choose what files. Returns (picked, deferred). Deterministic.

    Every rejection carries a reason, because a silent cap is the failure mode
    this whole stage exists to avoid.
    """
    from collections import defaultdict
    from math import ceil

    eligible, deferred = [], []
    by_cat = defaultdict(list)
    for e in candidates:
        by_cat[e.get("category", "news")].append(e)

    for cat, items in by_cat.items():
        items.sort(key=lambda e: (-e["score"], str(e.get("id", ""))))
        frac = CATEGORY_KEEP.get(cat)
        if frac is None:
            for e in items:
                e["reason"] = f"{cat} never files"
            deferred += items
            continue
        keep = max(1, ceil(len(items) * frac))
        eligible += items[:keep]
        for e in items[keep:]:
            e["reason"] = (f"outside the top {int(frac * 100)}% of {cat} "
                           f"this run ({len(items)} candidates)")
        deferred += items[keep:]

    # The quota is split by category FIRST, then round-robined across sources
    # inside each category. Doing it the other way round — one round-robin over
    # every source — is what made the category mix an accident of how many feeds
    # carry each kind of thing. Both fairness rules still hold: no source takes
    # more than MAX_SOURCE_SHARE, and inside a category every source is offered
    # a place before any source is offered a second.
    per_source = max(1, int(quota * MAX_SOURCE_SHARE))
    picked = []
    leftovers = []

    cat_quota = {c: int(quota * f) for c, f in CATEGORY_SHARE.items()}
    # integer division loses a place or two; give them to the highest share
    short = quota - sum(cat_quota.values())
    if short > 0 and cat_quota:
        top = max(CATEGORY_SHARE, key=CATEGORY_SHARE.get)
        cat_quota[top] += short

    by_cat_eligible = defaultdict(list)
    for e in eligible:
        by_cat_eligible[e.get("category", "news")].append(e)

    # Per-source counts are kept for the WHOLE run, not per call, because
    # round_robin runs once per category and then again for leftovers. Counting
    # inside the call let arXiv take 35 places against a cap of 25 — measured,
    # while writing this. The cap exists so one feed that is 53% of the corpus
    # cannot be 53% of the Knowledge Base; a cap that resets between passes is
    # not a cap.
    used = defaultdict(int)

    def round_robin(items, want):
        """Offer every source a place before any source gets a second."""
        by_src = defaultdict(list)
        for e in items:
            by_src[e.get("source") or "unknown"].append(e)
        for v in by_src.values():
            v.sort(key=lambda e: (-e["score"], str(e.get("id", ""))))
        order = sorted(by_src)          # stable and explainable; never fetch order
        idx = {s: 0 for s in order}
        got = []
        while len(got) < want:
            progressed = False
            for s in order:
                if len(got) >= want:
                    break
                if idx[s] >= len(by_src[s]) or used[s] >= per_source:
                    continue
                got.append(by_src[s][idx[s]])
                idx[s] += 1
                used[s] += 1
                progressed = True
            if not progressed:
                break
        taken = {id(e) for e in got}
        rest = []
        for s in order:
            for e in by_src[s]:
                if id(e) not in taken:
                    e["reason"] = (f"{s} already took its {per_source} for this run"
                                   if used[s] >= per_source
                                   else "eligible but outside this category's share "
                                        f"of today's quota of {quota}")
                    rest.append(e)
        return got, rest

    for cat in sorted(by_cat_eligible, key=lambda c: -CATEGORY_SHARE.get(c, 0)):
        want = cat_quota.get(cat, 0)
        got, rest = round_robin(by_cat_eligible[cat], want)
        picked += got
        leftovers += rest

    # A category that could not fill its share must not waste the places. Offer
    # them back, highest share first, rather than quietly filing fewer than the
    # quota — a silent shortfall is the same class of bug as a silent cap.
    if len(picked) < quota and leftovers:
        already = {id(e) for e in picked}
        spare = [e for e in leftovers if id(e) not in already]
        extra, _ = round_robin(spare, quota - len(picked))
        picked += extra
        got_ids = {id(e) for e in extra}
        leftovers = [e for e in leftovers if id(e) not in got_ids]

    deferred += leftovers
    return picked, deferred


def run(envelopes):
    for env in envelopes:
        score(env)
    return envelopes


def explain(env: dict) -> str:
    return " × ".join(f"{k}:{v:.2f}" for k, v in (env.get("score_terms") or {}).items())
