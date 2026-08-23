#!/usr/bin/env python3
"""
store.py — the funnel's memory. SQLite + sqlite-vec, one file, no server.

Three things live here:
  1. every item ever seen, so a second run never re-processes the first run's work
  2. per-feed HTTP cache headers, so polling is conditional and cheap
  3. two vector indexes — vault notes, and distilled items — for linking at file time

Deliberately not Postgres: 573 vectors and one user. See Information Lifecycle
→ "Why SQLite and not Postgres + pgvector".
"""
import os
import json
import pathlib
import re
import sqlite3
import urllib.error
import urllib.request

ORCH = pathlib.Path(__file__).resolve().parent.parent
VAULT = ORCH.parent
DB_PATH = ORCH / "state" / "mikoshi.db"

# Overridable because not everyone runs Ollama on this machine, on this
# port, or wants this model. Hardcoding any of the three is the difference
# between "works here" and "works". [no-hardcoding rule, @owner · 2026-07-16]
OLLAMA = os.environ.get("MIKOSHI_OLLAMA_URL", "http://localhost:11434")
EMBED_MODEL = os.environ.get("MIKOSHI_EMBED_MODEL", "nomic-embed-text")
EMBED_DIMS = 768
# bump whenever the model or the task prefixes change — it invalidates the index
EMBED_VERSION = "nomic-v1-prefixed"

# What counts as searchable. BOTH retrievers must use this — when the keyword
# side searched 272 files and the vector side held 70, rank fusion was comparing
# positions over different populations. Defined once, here, for that reason.
#
# `CLAUDE.md` is indexed deliberately. It used to be skipped as "scaffolding"
# back when rules were spread over five files; it is now the single source of
# every rule in the vault, and the evals showed it was the most-wanted document
# and completely unsearchable. `AGENTS.md`/`GEMINI.md` stay out — they are
# seven-line pointers with no content of their own. [measured 2026-08-17]
CORPUS_SKIP_DIRS = {".obsidian", ".git", ".claude", "code", "03-Archive",
                    "state", "staged", "digests", "Entities", "kb-backups",
                    "__pycache__"}
CORPUS_SKIP_STEMS = {"AGENTS", "GEMINI"}


def searchable(vault=None):
    """Every note either retriever is allowed to see."""
    root = vault or VAULT
    return [p for p in sorted(root.rglob("*.md"))
            if not any(x in CORPUS_SKIP_DIRS for x in p.parts)
            and p.stem not in CORPUS_SKIP_STEMS]


SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id          TEXT PRIMARY KEY,   -- sha256 of the canonical url
    url         TEXT,
    title       TEXT,
    author      TEXT,
    source      TEXT,               -- registry name, e.g. "Simon Willison"
    tier        INTEGER,
    domain      TEXT,               -- registry domain string
    form        TEXT,               -- article | video | newsletter | carousel | thread
    published   TEXT,
    fetched     TEXT,
    body        TEXT,
    category    TEXT,
    confidence  REAL,
    entities    TEXT,               -- json list of tools/models/people named
    cluster_id  TEXT,
    score       REAL,
    summary     TEXT,               -- distilled entry
    state       TEXT,               -- seen | classified | dropped | digested | filed
    reason      TEXT,               -- why it was dropped, or where it was filed
    run_id      TEXT
);
CREATE INDEX IF NOT EXISTS items_state ON items(state);
CREATE INDEX IF NOT EXISTS items_run   ON items(run_id);

CREATE TABLE IF NOT EXISTS feeds (
    url           TEXT PRIMARY KEY,
    etag          TEXT,
    last_modified TEXT,
    last_fetched  TEXT,
    last_status   TEXT,
    items_seen    INTEGER DEFAULT 0,
    -- reliability. A feed that fails is retried inside the run; a feed that
    -- keeps failing across runs is quarantined rather than retried forever,
    -- because a permanently dead feed costs a timeout every run and buries the
    -- one that broke today in a wall of ones that broke a month ago.
    fail_streak   INTEGER DEFAULT 0,
    quarantined   TEXT
);

CREATE TABLE IF NOT EXISTS notes (
    id      INTEGER PRIMARY KEY,
    path    TEXT UNIQUE,            -- vault-relative
    title   TEXT,
    mtime   REAL,
    hash    TEXT
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS chunks (
    id      INTEGER PRIMARY KEY,
    note_id INTEGER,
    ord     INTEGER,
    text    TEXT
);
CREATE INDEX IF NOT EXISTS chunks_note ON chunks(note_id);

CREATE TABLE IF NOT EXISTS runs (
    run_id   TEXT PRIMARY KEY,
    started  TEXT,
    finished TEXT,
    stats    TEXT
);
"""

VEC_SCHEMA = f"""
CREATE VIRTUAL TABLE IF NOT EXISTS vec_notes USING vec0(
    note_id INTEGER PRIMARY KEY,
    embedding float[{EMBED_DIMS}] distance_metric=cosine);
CREATE VIRTUAL TABLE IF NOT EXISTS vec_items USING vec0(
    item_rowid INTEGER PRIMARY KEY,
    embedding float[{EMBED_DIMS}] distance_metric=cosine);
CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(
    chunk_id INTEGER PRIMARY KEY,
    embedding float[{EMBED_DIMS}] distance_metric=cosine);
"""


class Conn(sqlite3.Connection):
    """sqlite3.Connection has no __dict__; subclass so `db.vec` can be set."""
    vec = False


def connect(path: pathlib.Path = DB_PATH) -> sqlite3.Connection:
    """Open the store, loading sqlite-vec. Vector tables are optional —
    the funnel still runs (without linking) if the extension is missing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, factory=Conn)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    try:
        import sqlite_vec
        db.enable_load_extension(True)
        sqlite_vec.load(db)
        db.enable_load_extension(False)
        _migrate_vec(db)
        db.executescript(VEC_SCHEMA)
        db.vec = True
    except Exception:
        db.vec = False
    db.commit()
    return db


def _migrate_vec(db):
    """Vectors are only comparable to vectors made the same way.

    Two things invalidate the index and neither announces itself:
      · `CREATE TABLE IF NOT EXISTS` keeping an older vector schema — which is
        how the index sat on L2 distance while the code assumed cosine
      · a change of embedding model or task prefix

    Both are detected here, and both force a re-embed rather than quietly
    returning nonsense distances.
    """
    stale = False
    for name in ("vec_notes", "vec_items", "vec_chunks"):
        row = db.execute("SELECT sql FROM sqlite_master WHERE name = ?", (name,)).fetchone()
        if row and "distance_metric=cosine" not in (row["sql"] or ""):
            db.execute(f"DROP TABLE {name}")
            stale = True

    row = db.execute("SELECT value FROM meta WHERE key = 'embed_version'").fetchone()
    if (row["value"] if row else None) != EMBED_VERSION:
        for name in ("vec_notes", "vec_items", "vec_chunks"):
            db.execute(f"DROP TABLE IF EXISTS {name}")
        db.execute("INSERT INTO meta (key, value) VALUES ('embed_version', ?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (EMBED_VERSION,))
        stale = True

    if stale:
        db.execute("UPDATE notes SET hash = NULL")


# ── embeddings ──────────────────────────────────────────────────────────────

def embed(text: str, kind: str = "document", timeout: int = 120):
    """One local embedding. Nothing leaves the machine.

    `nomic-embed-text` is trained with task prefixes and returns noticeably
    flatter neighbourhoods without them: corpus text is `search_document:`,
    a lookup is `search_query:`. Both sides must agree, so the index and the
    linker use this one function.

    Ollama returns a 500 when the input exceeds the embedding model's context,
    and character count is a poor proxy for tokens — a file of URLs and @handles
    is several times denser than prose. Shrink and retry rather than dropping the
    note, which is how `Sources.md` and `Models.md` silently missed the index.
    """
    prefix = "search_query: " if kind == "query" else "search_document: "
    for limit in (6000, 3000, 1500, 700):
        payload = json.dumps({"model": EMBED_MODEL, "prompt": prefix + text[:limit]}).encode()
        req = urllib.request.Request(
            f"{OLLAMA}/api/embeddings", data=payload,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)["embedding"]
        except urllib.error.HTTPError as e:
            if e.code != 500:
                return None
        except (urllib.error.URLError, KeyError, TimeoutError, OSError):
            return None
    return None


def serialize(vec) -> bytes:
    """float32 blob, the format vec0 stores."""
    import struct
    return struct.pack(f"{len(vec)}f", *vec)


def nearest_notes(db, vec, k: int = 5):
    """Nearest vault notes to a vector. Returns [(path, title, distance)]."""
    if not getattr(db, "vec", False) or vec is None:
        return []
    rows = db.execute(
        "SELECT note_id, distance FROM vec_notes "
        "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
        (serialize(vec), k)).fetchall()
    out = []
    for r in rows:
        n = db.execute("SELECT path, title FROM notes WHERE id = ?",
                       (r["note_id"],)).fetchone()
        if n:
            out.append((n["path"], n["title"], r["distance"]))
    return out


# ── item helpers ────────────────────────────────────────────────────────────

def seen(db, item_id: str) -> bool:
    return db.execute("SELECT 1 FROM items WHERE id = ?", (item_id,)).fetchone() is not None


def put_item(db, env: dict):
    cols = ("id url title author source tier domain form published fetched body "
            "category confidence entities cluster_id score summary state reason run_id").split()
    # `env.get`, never `env[c]`. This used to index directly for "entities" and
    # raised KeyError on any envelope that omitted it — which silently killed the
    # Instagram ingest loop after it had already downloaded the media.
    vals = []
    for c in cols:
        v = env.get(c)
        if c == "entities" and not isinstance(v, str):
            v = json.dumps(v or [])
        vals.append(v)
    db.execute(
        f"INSERT INTO items ({','.join(cols)}) VALUES ({','.join('?' * len(cols))}) "
        f"ON CONFLICT(id) DO UPDATE SET "
        + ",".join(f"{c}=excluded.{c}" for c in cols if c != "id"),
        vals)


def set_fields(db, item_id: str, **fields):
    if not fields:
        return
    sets = ",".join(f"{k}=?" for k in fields)
    db.execute(f"UPDATE items SET {sets} WHERE id = ?",
               [*fields.values(), item_id])


CHUNK_CHARS = 1200
CHUNK_OVERLAP = 200


def chunk(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP):
    """Split on paragraph boundaries, with overlap so a claim spanning a break
    survives in at least one window.

    Whole-note embeddings are an averaged topic vector: a 15 KB note about ten
    things points at none of them. This is the fix for that, and it is why
    `Dependencies.md` ranked 43rd for a question its own table answers."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    out, cur = [], ""
    for para in paras:
        if len(cur) + len(para) + 2 <= size:
            cur = f"{cur}\n\n{para}" if cur else para
            continue
        if cur:
            out.append(cur)
            cur = (cur[-overlap:] + "\n\n" + para) if overlap else para
        else:
            for i in range(0, len(para), size):
                out.append(para[i:i + size])
            cur = ""
    if cur:
        out.append(cur)
    return out or [text[:size]]


def nearest_chunks(db, vec, k: int = 30):
    """Best chunks, collapsed to parent notes. A note keeps its best chunk's rank."""
    if not getattr(db, "vec", False) or vec is None:
        return []
    rows = db.execute(
        "SELECT chunk_id, distance FROM vec_chunks "
        "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
        (serialize(vec), k)).fetchall()
    seen, out = set(), []
    for r in rows:
        c = db.execute("SELECT note_id, text FROM chunks WHERE id = ?",
                       (r["chunk_id"],)).fetchone()
        if not c:
            continue
        n = db.execute("SELECT path, title FROM notes WHERE id = ?", (c["note_id"],)).fetchone()
        if not n or n["path"] in seen:
            continue
        seen.add(n["path"])
        out.append((n["path"], n["title"], r["distance"], c["text"]))
    return out
