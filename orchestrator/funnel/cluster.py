#!/usr/bin/env python3
"""
cluster.py — Stage 4. Group items that are about the same thing.

**A cluster means two opposite things depending on category, and that is the
point** (Information Lifecycle → "Redundancy means the opposite thing here"):

  · news / industry — five outlets on one story is duplication. Collapse to one
    line carrying five citations.
  · tooling — five people posting the same MCP in a week is an adoption signal
    and the strongest buy-indication available. Never collapsed. The count is
    the finding, so the members and their sources are kept.

Greedy single-pass clustering over local embeddings. O(n²) on a few hundred
items a day is microseconds; a real clustering library would be a dependency
bought for nothing.
"""
import hashlib
import math

from . import store

THRESHOLD = 0.74  # cosine; tuned so "same tool" clusters and "same topic" does not


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def embed_all(envelopes, progress=None):
    for i, env in enumerate(envelopes, 1):
        if env.get("vec") is None:
            env["vec"] = store.embed(f"{env['title']}\n\n{(env.get('body') or '')[:2000]}")
        if progress:
            progress(i, len(envelopes))
    return envelopes


def run(envelopes, threshold: float = THRESHOLD):
    """Assign cluster_id to every envelope. Returns {cluster_id: [envelopes]}."""
    clusters = []  # list of [members]
    for env in envelopes:
        v = env.get("vec")
        if v is None:
            clusters.append([env])
            continue
        best, best_sim = None, threshold
        for c in clusters:
            head = c[0].get("vec")
            if head is None:
                continue
            s = cosine(v, head)
            if s >= best_sim:
                best, best_sim = c, s
        if best is None:
            clusters.append([env])
        else:
            best.append(env)

    grouped = {}
    for c in clusters:
        cid = hashlib.sha256("|".join(sorted(e["id"] for e in c)).encode()).hexdigest()[:10]
        for e in c:
            e["cluster_id"] = cid
            e["cluster_size"] = len(c)
            e["cluster_sources"] = sorted({m["source"] for m in c})
        grouped[cid] = c
    return grouped


def representative(members):
    """The member that speaks for a cluster: highest confidence, then longest body."""
    return max(members, key=lambda e: (e.get("confidence") or 0, len(e.get("body") or "")))
