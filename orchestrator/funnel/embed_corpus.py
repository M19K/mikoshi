#!/usr/bin/env python3
"""
embed_corpus.py — put the vault itself in the vector index.

This is the prerequisite for Stage 6 linking: an entry can only be filed with
links if something exists to link it to. Incremental — a note is re-embedded
only when its content hash changes, so the daily cost after the first run is
roughly zero.

    python3 -m funnel.embed_corpus          # incremental
    python3 -m funnel.embed_corpus --rebuild
"""
import argparse
import hashlib
import pathlib
import sys

from . import store

VAULT = store.VAULT
# `Entities/` is excluded deliberately. Entity pages are a navigational index,
# not prose to retrieve: 171 of them share the same template sentence, so in
# embedding space they are near-duplicates that cluster close to any query and
# crowd out the note that actually answers it. Reach an entity by its name
# (exact match, which is how it was built), not by similarity. [2026-08-16]
# `SKIP_DIRS` and `SKIP_STEMS` lived here and were dead: nothing has read
# them since `notes()` began delegating to `store.searchable`, which is the
# one definition both retrievers use. Removed 2026-08-27 rather than left
# 'available' — a constant kept alive by nothing is a thing the next reader
# has to disprove. [H-057]


def notes():
    """Single source of truth — see `store.searchable`."""
    return iter(store.searchable(VAULT))


def title_of(path: pathlib.Path, text: str) -> str:
    # A `#` inside a fenced block is a shell comment, not a heading. Reading one
    # as an H1 gave `delta/Live Status.md` the title "then the same for
    # beta, then:" and wrote it as the display text of a wikilink.
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced and line.startswith("# "):
            return line[2:].strip()
    return path.stem


def run(db, rebuild: bool = False, verbose: bool = True) -> dict:
    if not db.vec:
        return {"error": "sqlite-vec not loaded"}
    if rebuild:
        db.execute("DELETE FROM vec_notes")
        db.execute("DELETE FROM notes")

    paths = list(notes())

    # Drop rows for notes that were deleted or have become excluded. Without
    # this the index only ever grows, and a file removed from the scan keeps
    # answering queries from a stale vector.
    live = {str(p.relative_to(VAULT)) for p in paths}
    stale = [r["id"] for r in db.execute("SELECT id, path FROM notes")
             if r["path"] not in live]
    for nid in stale:
        for cid in [r["id"] for r in db.execute("SELECT id FROM chunks WHERE note_id = ?", (nid,))]:
            db.execute("DELETE FROM vec_chunks WHERE chunk_id = ?", (cid,))
        db.execute("DELETE FROM chunks WHERE note_id = ?", (nid,))
        db.execute("DELETE FROM vec_notes WHERE note_id = ?", (nid,))
        db.execute("DELETE FROM notes WHERE id = ?", (nid,))

    added = skipped = failed = chunked = 0
    for i, p in enumerate(paths, 1):
        rel = str(p.relative_to(VAULT))
        text = p.read_text(encoding="utf-8", errors="ignore")
        h = hashlib.sha256(text.encode()).hexdigest()[:16]
        row = db.execute("SELECT id, hash FROM notes WHERE path = ?", (rel,)).fetchone()
        if row and row["hash"] == h:
            skipped += 1
            continue

        vec = store.embed(f"{title_of(p, text)}\n\n{text}")
        if vec is None:
            failed += 1
            continue
        db.execute(
            "INSERT INTO notes (path, title, mtime, hash) VALUES (?,?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET title=excluded.title, mtime=excluded.mtime, "
            "hash=excluded.hash",
            (rel, title_of(p, text), p.stat().st_mtime, h))
        nid = db.execute("SELECT id FROM notes WHERE path = ?", (rel,)).fetchone()["id"]
        db.execute("DELETE FROM vec_notes WHERE note_id = ?", (nid,))
        db.execute("INSERT INTO vec_notes (note_id, embedding) VALUES (?,?)",
                   (nid, store.serialize(vec)))

        # chunk-level vectors — the note vector is an averaged topic vector and
        # cannot represent one claim inside a long note
        for cid in [r["id"] for r in db.execute(
                "SELECT id FROM chunks WHERE note_id = ?", (nid,))]:
            db.execute("DELETE FROM vec_chunks WHERE chunk_id = ?", (cid,))
        db.execute("DELETE FROM chunks WHERE note_id = ?", (nid,))
        for ord_, piece in enumerate(store.chunk(text)):
            cvec = store.embed(piece)
            if cvec is None:
                continue
            db.execute("INSERT INTO chunks (note_id, ord, text) VALUES (?,?,?)",
                       (nid, ord_, piece))
            cid = db.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
            db.execute("INSERT INTO vec_chunks (chunk_id, embedding) VALUES (?,?)",
                       (cid, store.serialize(cvec)))
            chunked += 1
        added += 1
        if verbose:
            print(f"  {i}/{len(paths)} embedded {rel[:60]}", end="\r", file=sys.stderr)
    db.commit()
    if verbose:
        print(file=sys.stderr)
    return {"notes": len(paths), "embedded": added, "unchanged": skipped,
            "failed": failed, "pruned": len(stale), "chunks": chunked}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true")
    a = ap.parse_args()
    db = store.connect()
    print(run(db, rebuild=a.rebuild))
