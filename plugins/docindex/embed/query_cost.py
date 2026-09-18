#!/usr/bin/env python3
"""Isolate the cost of a vector query: one API call vs the local KNN scan.

Brute-force KNN pulls every vector through memory, so a wider index costs RAM
and latency on a small box even when the API fee is identical.
"""
import os
import resource
import sys
import time

sys.path.insert(0, "/data/docindex/bin")
import docvec  # noqa: E402

for db_path in ("/data/docindex/index.db", "/data/docindex/index2048.db"):
    if not os.path.exists(db_path):
        continue
    docvec.DB = docvec.Path(db_path)
    try:
        db = docvec.connect(write=False)
    except Exception as exc:  # noqa: BLE001
        print(f"{db_path}: open failed: {exc}")
        continue
    dims = docvec.ACTIVE_DIMS
    n = db.execute("select count(*) from vec_map").fetchone()[0]
    t0 = time.time()
    v = docvec.embed_many(["Hundesteuer"], "query", 1, dims=dims)[0]
    api = time.time() - t0
    t0 = time.time()
    rows = db.execute(
        """select m.page_rowid, c.distance from vec_chunks c
           join vec_map m on m.chunk_id = c.rowid
           where c.embedding match ? and k = ? order by c.distance""",
        (v, 20)).fetchall()
    knn = time.time() - t0
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print(f"{os.path.basename(db_path)}: dims={dims} vectors={n} "
          f"api={api:.2f}s knn_k20={knn:.2f}s rows={len(rows)} peakRSS={rss:.0f}MB")
