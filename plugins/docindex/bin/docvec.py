#!/usr/bin/env python3
"""Vector arm for the document index.

Design constraints (deliberate):
  * NO resident service. A SQLite file plus this CLI. Idle cost is zero.
  * Embeddings come from voyageai/voyage-4 on OpenRouter, the same model GBrain
    uses. Model + width are resolved from the GBrain variables
    (GBRAIN_EMBEDDING_MODEL / GBRAIN_EMBEDDING_DIMENSIONS, then
    ~/.gbrain/config.json), so re-pointing GBrain's embedding provider moves
    this index with it instead of drifting into a different vector space.
  * Vectors live in the existing index.db beside the FTS5 table, so keyword
    and vector hits fuse in one query instead of across two stores.

  docvec embed [--limit N] [--workers W]   embed pages whose text changed
  docvec search "query" [--k 10] [--mode hybrid|vector|keyword] [--rerank]
  docvec stats

Requests are batched ACROSS pages (96 inputs per call, several in flight).
One call per page would make provider latency, not the model, the bottleneck.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import struct
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DB = Path(os.environ.get("DOCINDEX_DB", "/data/docindex/index.db"))
API_BASE = os.environ.get("OPENROUTER_BASE", "https://openrouter.ai/api/v1")
GBRAIN_CONFIG = Path(os.environ.get("GBRAIN_CONFIG", "/home/hermes/.gbrain/config.json"))
DEFAULT_MODEL, DEFAULT_DIMS = "voyageai/voyage-4", 1024


def _openrouter_model(spec: str) -> str:
    """`openrouter:voyageai/voyage-4` (or a bare id) -> `voyageai/voyage-4`.

    GBrain names a model provider:model. This tool only speaks OpenRouter, so
    another provider is a hard error, never a silent embed in the wrong space.
    """
    provider, sep, name = spec.partition(":")
    if not sep:
        return spec
    if provider != "openrouter":
        sys.exit(f"docvec calls OpenRouter only; {spec!r} is not an openrouter: target "
                 f"(set DOCVEC_MODEL to an OpenRouter id, or teach docvec that provider)")
    return name


def _space() -> tuple[str, int, str]:
    """Model, width and provenance, following GBrain so the two cannot drift.

    Precedence: DOCVEC_MODEL / DOCVEC_DIMS (explicit override) >
    GBRAIN_EMBEDDING_MODEL / GBRAIN_EMBEDDING_DIMENSIONS (the gbrain serve
    unit's variables) > ~/.gbrain/config.json > the built-in default. A width
    that disagrees with an existing DB is caught by connect(), which trusts the
    schema over this value.
    """
    cfg: dict = {}
    if GBRAIN_CONFIG.exists():
        try:
            cfg = json.loads(GBRAIN_CONFIG.read_text())
        except (OSError, ValueError):
            cfg = {}
    env = os.environ
    if env.get("DOCVEC_MODEL") or env.get("DOCVEC_DIMS"):
        source = "DOCVEC_* override"
    elif env.get("GBRAIN_EMBEDDING_MODEL") or env.get("GBRAIN_EMBEDDING_DIMENSIONS"):
        source = "GBRAIN_EMBEDDING_* (gbrain serve unit)"
    elif cfg.get("embedding_model") or cfg.get("embedding_dimensions"):
        source = f"gbrain config {GBRAIN_CONFIG}"
    else:
        source = "built-in default"
    model = (env.get("DOCVEC_MODEL") or env.get("GBRAIN_EMBEDDING_MODEL")
             or cfg.get("embedding_model") or DEFAULT_MODEL)
    dims = (env.get("DOCVEC_DIMS") or env.get("GBRAIN_EMBEDDING_DIMENSIONS")
            or cfg.get("embedding_dimensions") or DEFAULT_DIMS)
    return _openrouter_model(str(model)), int(dims), source


MODEL, DIMS, SPACE_SOURCE = _space()
# The width a loaded database actually uses. `connect()` overwrites this from
# the schema, so querying a 2048-dim DB works without matching DOCVEC_DIMS -
# trusting the env default instead produced "expected 2048 but received 1024".
ACTIVE_DIMS = DIMS
CHUNK_CHARS = 1200
CHUNK_OVERLAP = 200
BATCH = 96
GROUP_PAGES = 200
MAX_RETRY = 5


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
def api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key
    for f in (Path.home() / ".hermes/.env", Path("/data/.hermes/.env"),
              Path("/home/hermes/.hermes/.env")):
        if f.exists():
            for line in f.read_text().splitlines():
                if line.startswith("OPENROUTER_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    cfg = Path("/home/hermes/.gbrain/config.json")
    if cfg.exists():
        k = json.loads(cfg.read_text()).get("openrouter_api_key")
        if k:
            return k
    sys.exit("no OpenRouter API key found (env, .env, or gbrain config)")


_INPUT_TYPE = {"document": "document", "query": "query"}   # adapted on first 400


def _post(path: str, payload: dict, timeout: int = 120) -> dict:
    last = None
    for attempt in range(MAX_RETRY):
        req = urllib.request.Request(
            API_BASE + path,
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {api_key()}",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode(errors="replace")
            last = f"{e.code} {detail}"
            if e.code in (429, 500, 502, 503, 504, 529):
                time.sleep(min(2 ** attempt * 2, 30))
                continue
            if e.code == 400 and "input_type" in detail:
                _INPUT_TYPE.clear()          # provider rejects it: drop and retry
                continue
            raise SystemExit(f"OpenRouter error {last}") from None
        except Exception as e:                # noqa: BLE001 - retry transport errors
            last = f"{type(e).__name__}: {e}"
            time.sleep(min(2 ** attempt * 2, 30))
    raise SystemExit(f"embedding failed after {MAX_RETRY} tries: {last}")


def _blob(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def embed_batch(texts: list[str], kind: str, dims: int | None = None) -> list[bytes]:
    payload = {"model": MODEL, "input": texts, "dimensions": dims or DIMS,
               "encoding_format": "float"}
    if _INPUT_TYPE:
        payload["input_type"] = _INPUT_TYPE[kind]
    d = _post("/embeddings", payload)
    rows = sorted(d["data"], key=lambda r: r.get("index", 0))
    return [_blob(r["embedding"]) for r in rows]


def embed_many(texts: list[str], kind: str, workers: int,
               dims: int | None = None) -> list[bytes]:
    """Batch across the whole list, several batches in flight, order preserved."""
    batches = [texts[i:i + BATCH] for i in range(0, len(texts), BATCH)]
    if not batches:
        return []
    if workers <= 1:
        return [b for batch in batches for b in embed_batch(batch, kind, dims)]
    out: list[list[bytes] | None] = [None] * len(batches)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(embed_batch, b, kind, dims): i for i, b in enumerate(batches)}
        for fut in futs:
            out[futs[fut]] = fut.result()
    return [b for chunk in out for b in (chunk or [])]


# --------------------------------------------------------------------------- #
# schema
# --------------------------------------------------------------------------- #
def db_dims(db: sqlite3.Connection) -> int | None:
    """Width declared by the existing vec0 table, or None if it does not exist."""
    row = db.execute("select sql from sqlite_master where name='vec_chunks'").fetchone()
    if not row or not row[0]:
        return None
    m = re.search(r"float\[(\d+)\]", row[0])
    return int(m.group(1)) if m else None


def connect(write: bool = True) -> sqlite3.Connection:
    global ACTIVE_DIMS
    import sqlite_vec
    db = sqlite3.connect(str(DB), timeout=60)
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.execute("pragma journal_mode=wal")
    found = db_dims(db)
    if not write:
        if found:
            ACTIVE_DIMS = found
        return db
    # a provider/dimension switch invalidates every stored vector
    cur = db.execute("select sql from sqlite_master where name='vec_chunks'").fetchone()
    if cur and f"float[{DIMS}]" not in cur[0]:
        print(f"dimension change: dropping old vectors ({cur[0].split('float[')[1][:8]}... -> {DIMS})")
        db.execute("drop table if exists vec_chunks")
        db.execute("delete from vec_map")
        db.execute("delete from vec_pages")
    ACTIVE_DIMS = DIMS
    db.execute("""create table if not exists vec_pages(
                    page_rowid integer primary key, hsh text, n_chunks integer,
                    dims integer, model text, updated real)""")
    db.execute("""create table if not exists vec_map(
                    chunk_id integer primary key autoincrement,
                    page_rowid integer, chunk_no integer)""")
    db.execute("create index if not exists vec_map_page on vec_map(page_rowid)")
    db.execute(f"""create virtual table if not exists vec_chunks using vec0(
                    embedding float[{DIMS}] distance_metric=cosine)""")
    db.commit()
    return db


def chunks_of(text: str) -> list[str]:
    return [text[i:i + CHUNK_CHARS] for i in range(0, len(text), CHUNK_CHARS - CHUNK_OVERLAP)]


def page_hash(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# embed (backfill + incremental)
# --------------------------------------------------------------------------- #
def pending_pages(db: sqlite3.Connection, limit: int | None) -> list[tuple[int, str, str]]:
    """Pages never embedded, or whose text changed since the last run."""
    rows = db.execute("select rowid, path, text from pages where length(text) > 60").fetchall()
    done = {r[0]: r[1] for r in db.execute("select page_rowid, hsh from vec_pages")}
    todo = [r for r in rows if done.get(r[0]) != page_hash(r[2])]
    return todo[:limit] if limit else todo


def cmd_embed(args: argparse.Namespace) -> int:
    db = connect()
    todo = pending_pages(db, args.limit)
    total_bytes = sum(len(r[2].encode()) for r in todo)
    print(f"pages to embed: {len(todo)} (~{total_bytes/1e6:.1f} MB, model={MODEL}, dims={DIMS})")
    if not todo:
        print("nothing to do")
        return 0

    t0, n_pages, n_chunks = time.time(), 0, 0
    for gi in range(0, len(todo), GROUP_PAGES):
        group = todo[gi:gi + GROUP_PAGES]
        per_page = [(rid, path, chunks_of(text)) for rid, path, text in group]
        flat = [(rid, k, c) for rid, _p, parts in per_page for k, c in enumerate(parts)]
        blobs = embed_many([c for _, _, c in flat], "document", args.workers)
        if len(blobs) != len(flat):
            sys.exit(f"provider returned {len(blobs)} vectors for {len(flat)} inputs")

        by_page: dict[int, list[tuple[int, bytes]]] = {}
        for (rid, k, _c), blob in zip(flat, blobs):
            by_page.setdefault(rid, []).append((k, blob))
        for rid, path, parts in per_page:
            db.execute("delete from vec_chunks where rowid in "
                       "(select chunk_id from vec_map where page_rowid=?)", (rid,))
            db.execute("delete from vec_map where page_rowid=?", (rid,))
            for k, blob in sorted(by_page.get(rid, [])):
                cur = db.execute("insert into vec_map(page_rowid, chunk_no) values (?,?)", (rid, k))
                db.execute("insert into vec_chunks(rowid, embedding) values (?,?)",
                           (cur.lastrowid, blob))
            text = next(t for r, _p, t in group if r == rid)
            db.execute("insert or replace into vec_pages(page_rowid, hsh, n_chunks, dims, "
                       "model, updated) values (?,?,?,?,?,?)",
                       (rid, page_hash(text), len(parts), DIMS, MODEL, time.time()))
            n_pages += 1
            n_chunks += len(parts)
        db.commit()
        el = time.time() - t0
        eta = (len(todo) - n_pages) / max(n_pages / el, 1e-6)
        print(f"  {n_pages}/{len(todo)} pages, {n_chunks} chunks, {el/60:.1f} min elapsed, "
              f"ETA {eta/60:.1f} min", flush=True)
    print(f"done: {n_pages} pages, {n_chunks} chunks in {(time.time()-t0)/60:.1f} min")
    return 0


# --------------------------------------------------------------------------- #
# search
# --------------------------------------------------------------------------- #
def fts_terms(query: str) -> str:
    return " ".join(f'"{w}"' for w in query.split() if w.strip())


def vec_hits(db: sqlite3.Connection, query: str, k: int):
    qv = embed_many([query], "query", 1, dims=ACTIVE_DIMS)[0]
    rows = db.execute(
        """select m.page_rowid, c.distance from vec_chunks c
           join vec_map m on m.chunk_id = c.rowid
           where c.embedding match ? and k = ?
           order by c.distance""", (qv, k)).fetchall()
    out = []
    for page_rowid, dist in rows:
        r = db.execute("select path, page, text from pages where rowid=?",
                       (page_rowid,)).fetchone()
        if r:
            out.append({"path": r[0], "page": r[1], "text": r[2], "score": float(dist)})
    return out


def kw_hits(db: sqlite3.Connection, query: str, k: int):
    terms = fts_terms(query)
    if not terms:
        return []
    rows = db.execute("""select path, page, text, bm25(pages) from pages
                         where pages match ? order by bm25(pages) limit ?""",
                      (terms, k)).fetchall()
    return [{"path": r[0], "page": r[1], "text": r[2], "score": float(r[3])} for r in rows]


def rrf(lists, k: int = 60):
    score: dict[tuple[str, int], float] = {}
    keep: dict[tuple[str, int], dict] = {}
    for lst in lists:
        for rank, hit in enumerate(lst):
            key = (hit["path"], hit["page"])
            score[key] = score.get(key, 0.0) + 1.0 / (k + rank + 1)
            keep.setdefault(key, hit)
    return [{**keep[key], "score": s} for key, s in sorted(score.items(), key=lambda x: -x[1])]


def rerank(query: str, hits: list[dict], top_n: int = 50):
    """Cross-encoder pass. True hits often sit at fused ranks 10-50, so the
    pool handed to the reranker must be wider than the list we finally show."""
    if not hits:
        return hits
    pool = hits[:top_n]
    docs = [h["text"][:3000] for h in pool]
    d = _post("/rerank", {"model": "voyageai/rerank-2.5", "query": query,
                          "documents": docs, "top_n": len(docs)})
    order = sorted(d["results"], key=lambda r: -r.get("relevance_score", 0))
    return [{**pool[r["index"]], "score": r.get("relevance_score", 0.0)} for r in order]


def cmd_search(args: argparse.Namespace) -> int:
    db = connect(write=False)
    if args.mode == "vector":
        hits = vec_hits(db, args.query, args.k)
    elif args.mode == "keyword":
        hits = kw_hits(db, args.query, args.k)
    else:
        hits = rrf([vec_hits(db, args.query, args.k), kw_hits(db, args.query, args.k)])
    if args.rerank:
        try:
            hits = rerank(args.query, hits)
        except SystemExit as e:
            print(f"(rerank unavailable: {e})", file=sys.stderr)
    shown = hits[:args.k]
    print(f"mode={args.mode} hits={len(shown)}")
    for h in shown:
        print(f"\n[{h['score']:.4f}] {h['path']}  p{h['page']}")
        print(f"    {' '.join(h['text'].split())[:220]}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    db = connect(write=False)
    pages = db.execute("select count(*) from pages where length(text) > 60").fetchone()[0]
    done = db.execute("select count(*) from vec_pages").fetchone()[0]
    chunks = db.execute("select count(*) from vec_map").fetchone()[0]
    print(f"pages with text : {pages}")
    print(f"pages embedded  : {done} ({100*done/max(pages,1):.1f}%)")
    print(f"vectors         : {chunks} (model={MODEL}, dims={DIMS})")
    print(f"space source    : {SPACE_SOURCE}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="vector arm of the document index")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("embed")
    e.add_argument("--limit", type=int)
    e.add_argument("--workers", type=int, default=6)
    e.set_defaults(func=cmd_embed)
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--k", type=int, default=20)
    s.add_argument("--mode", default="hybrid", choices=["hybrid", "vector", "keyword"])
    s.add_argument("--rerank", action="store_true")
    s.set_defaults(func=cmd_search)
    st = sub.add_parser("stats")
    st.set_defaults(func=cmd_stats)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
