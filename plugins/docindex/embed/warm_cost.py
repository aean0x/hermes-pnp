#!/usr/bin/env python3
"""Is the KNN cost cold-cache only, and how fast is the disk under it?"""
import os
import resource
import subprocess
import sys
import time

sys.path.insert(0, "/data/docindex/bin")
import docvec  # noqa: E402

print(subprocess.run(["free", "-m"], capture_output=True, text=True).stdout.strip())

for path in ("/data/docindex/index.db", "/data/docindex/index2048.db"):
    size = os.path.getsize(path) / 1e9
    t0 = time.time()
    with open(path, "rb") as fh:
        while fh.read(8 << 20):
            pass
    dt = time.time() - t0
    print(f"\n{os.path.basename(path)}: {size:.2f} GB, raw read {dt:.1f}s "
          f"({size/dt*1000:.0f} MB/s, cache-limited)")

    docvec.DB = docvec.Path(path)
    db = docvec.connect(write=False)
    dims = docvec.ACTIVE_DIMS
    v = docvec.embed_many(["Hundesteuer"], "query", 1, dims=dims)[0]
    for i in range(3):
        t0 = time.time()
        rows = db.execute(
            """select m.page_rowid, c.distance from vec_chunks c
               join vec_map m on m.chunk_id = c.rowid
               where c.embedding match ? and k = ? order by c.distance""",
            (v, 20)).fetchall()
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        print(f"  knn run {i+1}: {time.time()-t0:6.2f}s  (rows={len(rows)}, peakRSS={rss:.0f}MB, dims={dims})")
