#!/usr/bin/env python3
"""Post-run verification for docindex: coverage, statuses, and search sanity.

Run when a full extraction run reports finished. Exits non-zero if the index
still has unprocessed or failed files.

Sanity terms are document identifiers, so they stay out of this repo: point
DOCINDEX_VERIFY_TERMS_FILE at a JSON list of [term, label] pairs, or set
DOCINDEX_VERIFY_TERMS to the same JSON. With neither set the search check is
skipped and the coverage/status checks still run.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

DB = os.environ.get("DOCINDEX_DB", "/data/docindex/index.db")
TERMINAL = ("ok", "empty", "partial", "duplicate", "skipped", "locked")


def _parse_terms(blob: str) -> list:
    """Accept [["term", "label"], ...] or [{"term": ..., "label": ...}, ...]."""
    try:
        data = json.loads(blob)
    except ValueError:
        print("verify: sanity terms are not valid JSON; skipping the search check")
        return []
    terms = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            terms.append((str(item[0]), str(item[1])))
        elif isinstance(item, dict) and item.get("term"):
            terms.append((str(item["term"]), str(item.get("label") or item["term"])))
    return terms


def load_terms() -> list:
    """Sanity pairs from the configured file, else inline JSON, else none."""
    path = os.environ.get("DOCINDEX_VERIFY_TERMS_FILE")
    if path:
        try:
            with open(path) as fh:
                return _parse_terms(fh.read())
        except OSError as exc:
            print(f"verify: cannot read {path} ({exc}); skipping the search check")
            return []
    inline = os.environ.get("DOCINDEX_VERIFY_TERMS")
    if inline:
        return _parse_terms(inline)
    print("verify: no sanity terms configured (DOCINDEX_VERIFY_TERMS_FILE); "
          "skipping the search check")
    return []


CHECKS = load_terms()


def main() -> int:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    ok = True
    print(f"database: {DB}  ({os.path.getsize(DB) / 1e6:.1f} MB)")

    status = dict(con.execute("select status, count(*) from files group by status"))
    print("\nstatus:", status)
    pending = status.get("pending", 0)
    failed = status.get("failed", 0)
    if pending:
        print(f"  !! {pending} file(s) still pending")
        ok = False
    if failed:
        print(f"  !! {failed} file(s) failed:")
        for p, e in con.execute("select path, error from files where status='failed' limit 10"):
            print(f"     {p}: {(e or '')[:110]}")
        ok = False
    if status.get("locked"):
        print(f"  note: {status['locked']} password-protected file(s) cannot be indexed")

    files, pages, chars = con.execute(
        "select count(*), coalesce(sum(npages),0), coalesce(sum(nchars),0) from files").fetchone()
    pidx = con.execute("select count(*) from pages").fetchone()[0]
    print(f"\nfiles={files}  pages_in_files={pages}  chars={chars:,}  fts_rows={pidx}")

    print("\nsearch sanity:")
    if not CHECKS:
        print("  (no terms configured)")
    for term, label in CHECKS:
        n = con.execute("select count(*) from pages where pages match ?", (f'"{term}"',)).fetchone()[0]
        mark = "ok " if n else "MISS"
        if not n:
            ok = False
        print(f"  [{mark}] {label:<42} {n} page hit(s) for {term!r}")

    print("\ncoverage of image-only scans (the original gap):")
    rows = con.execute(
        "select coalesce(method,'unknown'), count(*) from files where kind='pdf' "
        "group by 1 order by 2 desc")
    for m, n in rows:
        print(f"  {m:<22} {n}")
    ocr_files = con.execute(
        "select count(*) from files where method like 'ocr-%' or method like '%+ocr%'"
        " or method='images-salvage'").fetchone()[0]
    ocr_pages = con.execute(
        "select count(*) from pages where path in (select path from files where method"
        " like 'ocr-%' or method like '%+ocr%' or method='images-salvage')").fetchone()[0]
    print(f"  -> {ocr_files} files needed OCR ({ocr_pages} pages transcribed)")
    for p, e in con.execute("select path, error from files where status='corrupt'"):
        print(f"  note: unparseable PDF, skipped: {p.split('/')[-1]} ({(e or '')[:60]})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
