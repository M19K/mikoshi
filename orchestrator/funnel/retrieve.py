#!/usr/bin/env python3
"""
retrieve.py — hybrid retrieval: BM25 + vector, fused by RRF, then reranked.

*(2026-08-16.)* `Information Lifecycle.md` has said retrieval
is "hybrid — keyword, vector, graph, merged" since 2026-08-15, but nothing merged
them. grep ran, vectors ran, and a human read both. This is the merge.

**Reciprocal rank fusion.** Each retriever votes with `1/(k + rank)`. A note both
retrievers rank highly beats a note either one loves alone, and no score
normalisation is needed between two incomparable scales. k=60 is the standard
constant from the original RRF paper.

**Reranking is OFF by default, because it was measured and it loses.**
The strong option is a hosted cross-encoder (Voyage `rerank-2.5`) — fast and
accurate, and a network call with a key behind it.
The nearest local equivalent is asking the model to score the shortlist, and on
the 20-question eval set that costs **18x the time to make top-1 worse**:

    fusion only     65% first · 85% in top five ·  17s
    with reranking  60% first · 90% in top five · 301s

Five points of recall for eighteen times the wait, and five points of precision
given up to get it. Use `--rerank` when a single answer matters more than speed;
otherwise fusion alone is the better tool. [measured 2026-08-17]

    python3 -m funnel.retrieve "how does macOS bind screen recording grants"
    python3 -m funnel.retrieve "sqlite-vec" --rerank -k 5
"""
import argparse
import math
import re
import sys
from collections import Counter, defaultdict

from . import llm, store

# RRF's canonical k=60 comes from TREC-scale runs over thousands of documents.
# At 20 candidates per retriever the gap between 1/(60+1) and 1/(60+20) is tiny,
# so "appears mid-list in both" beats "ranked first in one" — which put Home.md
# above the note that actually answered the query. A small k restores rank
# position as the dominant signal. [measured 2026-08-16]
RRF_K = 8
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._-]{1,}")

# Must match `embed_corpus.SKIP_DIRS`. RRF fuses two rankings by position, so
# the retrievers have to be ranking the same set — when BM25 searched 272 files
# and the vector index held 70, fusion was comparing ranks over different
# populations and the merged order was worse than either input.
SKIP_PARTS = {"code", "03-Archive", ".git", ".obsidian", "state", "staged",
              "kb-backups", "__pycache__", "digests", "Entities", "funnel", "jobs"}


# Removed from the QUERY only, never from documents — document statistics stay
# intact, but a natural-language question like "which vector store did we choose
# and why" is 6 parts noise to 2 parts signal, and scoring the noise ranked the
# answer 8th. [measured 2026-08-16]
STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "than", "that", "this",
    "these", "those", "is", "are", "was", "were", "be", "been", "being", "do",
    "does", "did", "doing", "have", "has", "had", "will", "would", "should",
    "can", "could", "may", "might", "must", "we", "our", "us", "i", "my", "you",
    "your", "it", "its", "he", "she", "they", "them", "their", "of", "in", "on",
    "at", "to", "for", "with", "by", "from", "as", "about", "into", "over",
    "what", "which", "who", "whom", "whose", "when", "where", "why", "how",
    "all", "any", "both", "each", "few", "more", "most", "other", "some",
    "such", "no", "nor", "not", "only", "own", "same", "so", "too", "very",
    "just", "get", "got", "use", "used", "using", "there", "here",
}


def tokens(text, drop_stopwords=False):
    out = TOKEN_RE.findall(text.lower())
    return [t for t in out if t not in STOPWORDS] if drop_stopwords else out


def corpus():
    """Every note, tokenised once. Same set the vector index holds — see
    `store.searchable`. These two drifting apart broke fusion once already."""
    docs = {}
    for p in store.searchable():
        try:
            docs[str(p.relative_to(store.VAULT))] = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
    return docs


def bm25(query, docs, k1=1.5, b=0.75, top=20):
    """Standard BM25. Exact terms, so it never silently drops a keyword match."""
    q = tokens(query, drop_stopwords=True) or tokens(query)
    toks = {path: tokens(text) for path, text in docs.items()}
    lengths = {p: len(t) for p, t in toks.items()}
    avg = sum(lengths.values()) / max(len(lengths), 1)
    df = Counter()
    tf = {}
    for path, t in toks.items():
        c = Counter(t)
        tf[path] = c
        for term in set(q) & c.keys():
            df[term] += 1
    n = len(toks)
    scored = []
    for path, c in tf.items():
        s = 0.0
        for term in q:
            if term not in c:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            freq = c[term]
            s += idf * (freq * (k1 + 1)) / (freq + k1 * (1 - b + b * lengths[path] / avg))
        if s > 0:
            scored.append((s, path))
    scored.sort(reverse=True)
    return [p for _, p in scored[:top]]


def vector(db, query, top=20, want_text=False):
    """Chunk-level search, collapsed to parent notes.

    Whole-note vectors put `Dependencies.md` at rank 43 for a question its own
    table answers, because one vector for a 15 KB note points at nothing in
    particular. Chunks fixed that. [measured 2026-08-16]
    """
    v = store.embed(query, kind="query")
    hits = store.nearest_chunks(db, v, k=top * 4)[:top]
    if want_text:
        return [(path, text) for path, _title, _d, text in hits]
    return [path for path, _title, _d, _text in hits]


def rrf(*rankings, k=RRF_K):
    """Reciprocal rank fusion. Rank position only — no score normalisation."""
    scores = defaultdict(float)
    for ranking in rankings:
        for rank, path in enumerate(ranking, 1):
            scores[path] += 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: -kv[1])


def best_window(text, query, width=700):
    """The passage that actually matched, not the top of the file.

    Feeding a reranker the first N characters of a vault note shows it YAML
    frontmatter and a heading, so it scores every candidate zero and the whole
    stage silently does nothing. A cross-encoder has to see the passage the
    retrievers hit.
    """
    flat = re.sub(r"\s+", " ", text)
    terms = set(tokens(query, drop_stopwords=True))
    if not terms or len(flat) <= width:
        return flat[:width]
    best_pos, best_hits = 0, -1
    step = max(width // 4, 100)
    for pos in range(0, max(len(flat) - width, 1), step):
        window = flat[pos:pos + width].lower()
        hits = sum(window.count(t) for t in terms)
        if hits > best_hits:
            best_pos, best_hits = pos, hits
    prefix = "…" if best_pos else ""
    return prefix + flat[best_pos:best_pos + width]


RERANK_SYSTEM = "You score how well a document answers a query. Answer only with JSON."

RERANK_TEMPLATE = """Query: {query}

Score each document 0-10 for how directly it answers that query. A document that
merely mentions the topic scores low; one that answers it scores high.

Return ONLY: {{"scores":[{{"i":<index>,"score":<0-10>}}]}}

DOCUMENTS:
{docs}"""


def rerank(query, candidates, docs, top=5):
    """The cross-encoder stand-in: one model pass over query + shortlist together."""
    shortlist = candidates[:top * 2]
    if not shortlist:
        return []
    block = []
    for i, (path, _s) in enumerate(shortlist):
        body = best_window(docs.get(path, ""), query)
        block.append(f"[{i}] {path}\n    {body}")
    # The budget has to scale with the shortlist. A fixed 400 truncated the JSON
    # mid-object on ten candidates, which parsed as an empty score set and
    # silently degraded every result to zero.
    got = llm.chat(RERANK_TEMPLATE.format(query=query, docs="\n\n".join(block)),
                   RERANK_SYSTEM, max_tokens=60 * len(shortlist) + 400)
    scores = {}
    if isinstance(got, dict):
        for s in got.get("scores", []) or []:
            try:
                scores[int(s["i"])] = float(s["score"])
            except (KeyError, TypeError, ValueError):
                continue
    if not scores:
        # model down or unparseable — keep fusion order and its real scores,
        # never a column of zeros that looks like a confident nil result
        return shortlist[:top]
    ranked = sorted(range(len(shortlist)), key=lambda i: -scores.get(i, 0))
    return [(shortlist[i][0], scores.get(i, 0)) for i in ranked[:top]]


def search(query, k=5, do_rerank=False, db=None):
    db = db or store.connect()
    docs = corpus()
    # the matching chunk is a better rerank window than anything re-derived
    passages = dict(vector(db, query, want_text=True))
    fused = rrf(bm25(query, docs), list(passages))
    docs = {**docs, **{p: t for p, t in passages.items() if p in docs}}
    if not do_rerank:
        return [(p, round(s, 4)) for p, s in fused[:k]], docs
    return rerank(query, fused, docs, top=k), docs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="+")
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--rerank", action="store_true",
                    help="score the shortlist with the model. Slower; see module docstring.")
    ap.add_argument("--show-stages", action="store_true")
    a = ap.parse_args()
    q = " ".join(a.query)
    db = store.connect()

    if a.show_stages:
        docs = corpus()
        b, v = bm25(q, docs), vector(db, q)
        print("BM25:  " + ", ".join(x[:52] for x in b[:5]))
        print("VEC:   " + ", ".join(x[:52] for x in v[:5]))
        print("RRF:   " + ", ".join(p[:52] for p, _ in rrf(b, v)[:5]))
        print()

    results, _ = search(q, k=a.k, do_rerank=a.rerank, db=db)
    label = "reranked" if a.rerank else "fused"
    print(f"{label} top {len(results)} for: {q}\n")
    for path, score in results:
        print(f"  {score:6.3f}  {path}")


if __name__ == "__main__":
    main()
