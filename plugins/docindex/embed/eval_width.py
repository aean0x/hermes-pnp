#!/usr/bin/env python3
"""Width A/B harness: measure retrieval quality on one index, same queries.

Run against two DBs to decide a vector width, e.g.
    DOCINDEX_DB=/data/docindex/index.db     python eval_width.py
    DOCINDEX_DB=/data/docindex/index2048.db python eval_width.py

Each pair is (keyword that exists in a document, paraphrase that shares no
words with it). Ground truth = pages containing the keyword, so only meaning
can find them. Pairs whose keyword is absent from the corpus are skipped and
counted, never silently scored as misses.
"""
import os
import re
import sys
import sqlite3

sys.path.insert(0, "/data/docindex/bin")
import docvec  # noqa: E402

K = 10
POOL = 50
RERANK = "--rerank" in sys.argv

PAIRS = [
    # German
    ("Mahnung", "Schreiben weil ich eine Rechnung nicht bezahlt habe"),
    ("Kündigung", "Vertrag den ich beenden will"),
    ("Mietvertrag", "Vereinbarung ueber meine Wohnung"),
    ("Hundesteuer", "Gebuehr die ich fuer meinen Hund zahle"),
    ("Grundsteuer", "Steuer fuer mein Grundstueck"),
    ("Kfz-Versicherung", "Versicherung fuer mein Auto"),
    ("Gehaltsabrechnung", "Abrechnung meines Lohns vom Arbeitgeber"),
    ("Steuererklärung", "Formular fuer meine Steuern"),
    ("Krankenversicherung", "Versicherung die Arztkosten bezahlt"),
    ("Arbeitsvertrag", "Vertrag ueber meine Arbeit"),
    ("Rezept", "aerztliche Verordnung fuer Medikamente"),
    ("Nebenkostenabrechnung", "Abrechnung der Heizkosten"),
    ("Aufenthaltstitel", "Dokument das meinen Aufenthalt erlaubt"),
    ("Impfpass", "Heft mit meinen Impfungen"),
    ("Kontoauszug", "Liste meiner Bankbewegungen"),
    ("Arbeitslosengeld", "Geld vom Amt wenn ich keine Arbeit habe"),
    ("Rundfunkbeitrag", "Gebuehr fuer Fernsehen und Radio"),
    ("Anmeldung", "Formular um mich bei der Stadt zu registrieren"),
    ("Zeugnis", "Bescheinigung ueber meine Leistungen"),
    ("Steuer-ID", "Nummer die das Finanzamt mir gegeben hat"),
    # English
    ("passport", "document proving my nationality when I travel"),
    ("lease", "agreement for renting a home"),
    ("invoice", "request for payment for goods"),
    ("resume", "document listing my work history"),
    ("prescription", "doctors note for medicine"),
    ("statement", "list of my bank transactions"),
]


def gt_pages(db, kw: str, limit: int = 40) -> set[str]:
    rows = db.execute(
        "select distinct path from pages where pages match ? limit ?",
        (f'"{kw}"', limit),
    ).fetchall()
    return {r[0] for r in rows}


def first_hit(hits, gt):
    for i, h in enumerate(hits, 1):
        if h["path"] in gt:
            return i
    return 0


def main():
    db = docvec.connect(write=False)
    print(f"  db={os.environ.get('DOCINDEX_DB','/data/docindex/index.db')} "
          f"pages={db.execute('select count(*) from pages').fetchone()[0]} "
          f"vecs={db.execute('select count(*) from vec_map').fetchone()[0]} "
          f"rerank={'on' if RERANK else 'off'}")
    agg = {n: {"hit10": 0, "hit25": 0, "hit50": 0, "rr": 0.0, "n": 0}
           for n in ("vector", "keyword", "hybrid")}
    skipped = []
    for kw, q in PAIRS:
        gt = gt_pages(db, kw)
        if not gt:
            skipped.append(kw)
            continue
        v = docvec.vec_hits(db, q, POOL)
        k = docvec.kw_hits(db, q, POOL)
        h = docvec.rrf([v, k])
        if RERANK:
            h = docvec.rerank(q, h, 50)
        for name, hits in (("vector", v), ("keyword", k), ("hybrid", h)):
            a = agg[name]
            a["n"] += 1
            r = first_hit(hits, gt)
            if r:
                a["rr"] += 1.0 / r
                if r <= 10:
                    a["hit10"] += 1
                if r <= 25:
                    a["hit25"] += 1
                if r <= 50:
                    a["hit50"] += 1
    print(f"  pairs used: {agg['hybrid']['n']}   skipped (keyword absent): {skipped}\n")
    print(f"  {'arm':8} {'@10':>7} {'@25':>7} {'@50':>7} {'MRR':>7}")
    for name, a in agg.items():
        n = max(a["n"], 1)
        print(f"  {name:8} {a['hit10']:3}/{n:<3} {a['hit25']:3}/{n:<3} "
              f"{a['hit50']:3}/{n:<3} {a['rr']/n:7.3f}")


if __name__ == "__main__":
    main()
