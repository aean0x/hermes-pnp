#!/usr/bin/env python3
"""Grounded recall eval over the FULL index: keyword vs vector vs hybrid.

Ground truth = pages that contain a known keyword (found via FTS5).
Query = a paraphrase that does NOT contain that keyword, so only semantics
can find it. Reports recall@10 and MRR per arm, plus per-query detail, so a
claim like "semantic search helps" is backed by numbers.

  eval_full.py
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/data/docindex/bin")
import docvec  # noqa: E402

K = 10
RERANK = "--rerank" in sys.argv
POOL = 25          # candidates per arm before fusion

# (ground-truth keyword that IS in the documents, paraphrased query that is NOT)
PAIRS = [
    ("Hundesteuer", "Gebuehr die ich fuer meinen Vierbeiner zahlen muss"),
    ("Grundsteuer", "Abgabe fuer mein Grundstueck an die Gemeinde"),
    ("Behandlungsausweis", "Karte fuer die aerztliche Behandlung von meiner Krankenkasse"),
    ("Mahnung", "Schreiben weil ich eine Rechnung nicht bezahlt habe"),
    ("Kuendigung", "Ich beende meinen Vertrag"),
    ("Mietvertrag", "Vereinbarung ueber das Wohnen gegen Miete"),
    ("Gehaltsabrechnung", "was ich monatlich verdiene"),
    ("Steuerbescheid", "Schreiben vom Finanzamt ueber meine Steuern"),
    ("Kfz-Versicherung", "Absicherung fuer mein Auto"),
    ("Arbeitszeugnis", "Bewertung meiner Arbeit vom Chef"),
    ("Rechnung", "Aufforderung Geld zu ueberweisen"),
    ("Termin", "wann ich irgendwo erscheinen muss"),
    # English pairs (the archive is bilingual)
    ("passport", "the document that proves my nationality when I travel"),
    ("payslip", "statement showing the money my employer paid me"),
    ("lease", "written agreement about renting a home"),
    ("receipt", "proof that I already paid"),
]


def report(name: str, hits: list[dict], gt: set[str]) -> tuple[bool, int | None]:
    rank = next((i + 1 for i, h in enumerate(hits) if h["path"] in gt), None)
    return (rank is not None), rank


def main() -> int:
    db = docvec.connect(write=False)
    n_pages = db.execute("select count(*) from pages where length(text) > 60").fetchone()[0]
    n_vec = db.execute("select count(*) from vec_pages").fetchone()[0]
    print(f"index: {n_pages} pages with text, {n_vec} pages embedded\n")

    tot = {"vector": 0, "keyword": 0, "hybrid": 0}
    mrr = {"vector": 0.0, "keyword": 0.0, "hybrid": 0.0}
    print(f"{'ground truth':<20} {'vector':<8} {'keyword':<8} {'hybrid':<8} best-arm-detail")
    for kw, q in PAIRS:
        gt = {r[0] for r in db.execute(
            "select distinct path from pages where pages match ? limit 5", (f'"{kw}"',))}
        if not gt:
            print(f"{kw:<20} (keyword absent from corpus - pair skipped)")
            continue
        v = docvec.vec_hits(db, q, POOL)
        k = docvec.kw_hits(db, q, POOL)
        h = docvec.rrf([v, k])
        if RERANK:
            h = docvec.rerank(q, h, 50)
        h = h[:K]

        res = {}
        for name, hits in (("vector", v[:K]), ("keyword", k[:K]), ("hybrid", h[:K])):
            hit, rank = report(name, hits, gt)
            res[name] = (hit, rank)
            tot[name] += hit
            if rank:
                mrr[name] += 1.0 / rank
        detail = " ".join(f"{n[:3]}{'OK' if res[n][0] else '--'}{res[n][1] or ''}"
                          for n in ("vector", "keyword", "hybrid"))
        print(f"{kw:<20} "
              f"{'HIT' if res['vector'][0] else 'miss':<8} "
              f"{'HIT' if res['keyword'][0] else 'miss':<8} "
              f"{'HIT' if res['hybrid'][0] else 'miss':<8} {detail}")

    n = sum(1 for kw, _ in PAIRS
            if db.execute("select 1 from pages where pages match ? limit 1",
                          (f'"{kw}"',)).fetchone())
    print(f"\nqueries evaluated: {n}")
    for name in ("vector", "keyword", "hybrid"):
        print(f"  {name:<8} recall@{K} {tot[name]}/{n}  MRR {mrr[name]/max(n,1):.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
