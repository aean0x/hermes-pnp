#!/usr/bin/env python3
"""docindex - resumable full-text index over local document corpora.

Extracts text from PDFs (text layer or OCR), images, Office (zip+XML),
and legacy binary formats into a local SQLite FTS5 index so personal
document archives become searchable.

Subcommands:
  scan [roots...]      inventory files, hash, classify  (no text extraction)
  extract              run extraction/OCR on pending files (resumable)
  search QUERY         FTS5 search, prints path + page + snippet
  show PATH [--page N] dump indexed text for one file
  stats                counts by status/kind, db size

Environment: DOCINDEX_DB (default /data/docindex/index.db)
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

TOOLBOX = "/data/toolbox/bin"
WORKER_PATH = TOOLBOX + ":" + os.environ.get("PATH", "/usr/bin:/bin")
DPI = 200
LANG = "deu+eng"
MIN_TEXT_CHARS = 100      # per file: below this the text layer is considered unusable
MIN_PAGE_CHARS = 20       # per page: below this the page gets OCR'd
DB_DEFAULT = "/data/docindex/index.db"
WORK_DEFAULT = "/tmp/docindex-work"

PDF_TEXT_KINDS = {".pdf"}
IMAGE_KINDS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic", ".bmp", ".webp"}
ZIPXML_KINDS = {".docx", ".pptx", ".xlsx", ".odt", ".xps", ".dotx"}
PLAIN_KINDS = {".txt", ".md", ".json", ".csv", ".xml", ".html", ".htm", ".svg",
               ".java", ".log", ".ini", ".yaml", ".yml", ".sql", ".sh", ".py"}
LEGACY_KINDS = {".doc", ".xls", ".msg", ".accdb", ".pdn"}
SKIP_KINDS = {".zip", ".pfx", ".class", ".jar", ".exe", ".dll", ".sqlite",
              ".db", ".mp4", ".mov", ".avi", ".mkv", ".mp3", ".wav"}

# Hard secret floor. The index stores raw text and the agent can read all of it,
# so these patterns are applied on every scan and can NOT be widened by a caller.
# Scanning a wide tree (/data, a home dir) without this would sweep in keys,
# tokens and browser state.
SECRET_NAMES = ("*password*", "*passwd*", "*secret*", "*token*", "*credential*",
                "*.pfx", "*.p12", "*.pem", "*.key", "*.kdbx", "*.ovpn",
                "id_rsa*", "id_ed25519*", "id_ecdsa*", "*cookie*", "*netrc*",
                "gh-app-token", "*vnc.pass*", "*.pgpass", "known_hosts*")
SECRET_DIRS = {"browser-profile", "browser-cookies", "browser-gate", "browser-logs",
               "plaid-mcp", "secret-handoff", ".ssh", ".gnupg", ".aws", ".kube",
               ".docker", ".config", "wallet", ".bank-mcp", ".agentcard"}
PRIVATE_KEY_RE = re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----")

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
  path TEXT PRIMARY KEY,
  root TEXT,
  ext TEXT,
  size INTEGER,
  mtime REAL,
  sha256 TEXT,
  kind TEXT,
  status TEXT DEFAULT 'pending',
  method TEXT,
  npages INTEGER,
  nchars INTEGER,
  error TEXT,
  updated REAL
);
CREATE TABLE IF NOT EXISTS roots (path TEXT PRIMARY KEY, added REAL);
CREATE INDEX IF NOT EXISTS files_status ON files(status);
CREATE INDEX IF NOT EXISTS files_sha ON files(sha256);
CREATE VIRTUAL TABLE IF NOT EXISTS pages USING fts5(
  path UNINDEXED, page UNINDEXED, text,
  tokenize='unicode61 remove_diacritics 2'
);
"""


def connect(db: str) -> sqlite3.Connection:
    Path(db).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    c = sqlite3.connect(db, timeout=60)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.executescript(SCHEMA)
    return c


def run(cmd: list[str], timeout: int = 300, text: bool = True):
    env = dict(os.environ, PATH=WORKER_PATH, OMP_THREAD_LIMIT="1")
    # TESSDATA_PREFIX must not be set to an empty string: tesseract then finds no
    # language data and exits 0 with no output (silent empty OCR).
    if not env.get("TESSDATA_PREFIX"):
        env.pop("TESSDATA_PREFIX", None)
    return subprocess.run(cmd, capture_output=True, text=text, timeout=timeout, env=env)


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def classify(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext in SKIP_KINDS:
        return "skip"
    if ext in PDF_TEXT_KINDS:
        return "pdf"
    if ext in IMAGE_KINDS:
        return "image"
    if ext in ZIPXML_KINDS:
        return "zipxml"
    if ext in PLAIN_KINDS:
        return "plain"
    if ext in LEGACY_KINDS:
        return "legacy"
    return "skip"


# ---------------------------------------------------------------- extractors

def pdf_locked(path: str) -> bool:
    """True when the PDF needs a real user password (not an empty one)."""
    res = run(["pdfinfo", "-upw", "", path], timeout=120)
    blob = (res.stdout or "") + (res.stderr or "")
    return "Incorrect password" in blob or ("Password" in blob and "Pages:" not in blob)


CORRUPT_MARKERS = ("Failed to parse XRef", "try to reconstruct", "Bad 'Len",
                   "Couldn't find trailer", "Couldn't read xref")


def pdf_broken(path: str, probe_timeout: int = 30) -> bool:
    """True when poppler cannot parse the file at all.

    Such PDFs (broken xref) make poppler loop inside xref reconstruction: every
    later call hangs until its timeout, so each retry burns minutes of CPU and
    still yields nothing. Detect them with one short probe.
    """
    try:
        res = run(["pdfinfo", "-upw", "", path], timeout=probe_timeout)
    except subprocess.TimeoutExpired:
        return True
    blob = (res.stdout or "") + (res.stderr or "")
    return any(m in blob for m in CORRUPT_MARKERS) and "Pages:" not in blob


def images_salvage(path: str, workdir: Path, max_pages: int = 10) -> str:
    """Last-resort text for unparseable PDFs: OCR whatever images come out."""
    wd = Path(workdir) / f"sal{os.getpid()}"
    wd.mkdir(parents=True, exist_ok=True)
    try:
        run(["pdfimages", "-upw", "", "-all", "-f", "1", "-l", str(max_pages), path, str(wd / "im")],
            timeout=180)
        parts: list[str] = []
        for img in sorted(p for p in wd.iterdir() if p.is_file())[:max_pages]:
            try:
                parts.append(ocr_best_orientation(str(img), workdir))
            except Exception:
                continue
            finally:
                img.unlink(missing_ok=True)
        return "\n".join(p for p in parts if p.strip())
    except Exception:
        return ""
    finally:
        try:
            wd.rmdir()
        except OSError:
            pass


def pdf_page_count(path: str) -> int:
    try:
        out = run(["pdfinfo", "-upw", "", path], timeout=120).stdout
        m = re.search(r"^Pages:\s+(\d+)", out, re.M)
        return int(m.group(1)) if m else 0
    except Exception:
        return 0


def pdf_text_pages(path: str) -> list[str]:
    """Text layer, split per page on form feed."""
    try:
        out = run(["pdftotext", "-upw", "", "-layout", path, "-"], timeout=300).stdout
    except Exception:
        return []
    return out.split("\f")


def _tesseract(img: str, workdir: Path) -> str:
    """OCR one image file. A non-zero exit is a real error, not an empty page."""
    res = run(["tesseract", img, "stdout", "-l", LANG], timeout=900)
    if res.returncode != 0:
        raise RuntimeError(f"tesseract rc={res.returncode} {(res.stderr or '')[:160]}")
    return res.stdout


def _render_page(path: str, page: int, workdir: Path) -> tuple[str, list[Path]]:
    """Render one page for OCR. Falls back to extracting the embedded scan when
    pdftoppm cannot rasterize the page (corrupt XRef, odd producers)."""
    prefix = workdir / f"pg{page:04d}"
    run(["pdftoppm", "-upw", "", "-r", str(DPI), "-gray", "-png",
         "-f", str(page), "-l", str(page), path, str(prefix)], timeout=600)
    imgs = sorted(workdir.glob(f"pg{page:04d}*.png"))
    if imgs:
        return str(imgs[0]), imgs
    for flag in ("-j", "-png"):
        ex = workdir / f"emb{page:04d}"
        run(["pdfimages", "-upw", "", flag, "-f", str(page), "-l", str(page), path, str(ex)],
            timeout=600)
        cands = [c for c in sorted(workdir.glob(f"emb{page:04d}*")) if c.is_file()]
        if cands:
            biggest = max(cands, key=lambda p: p.stat().st_size)
            return str(biggest), cands
    raise RuntimeError("no image: pdftoppm and pdfimages both produced nothing")


def ocr_pdf_page(path: str, page: int, workdir: Path) -> str:
    """Render one page and OCR it. Glob output - pdftoppm zero-pads by page count."""
    img, produced = _render_page(path, page, workdir)
    try:
        return _tesseract(img, workdir)
    finally:
        for i in produced:
            try:
                i.unlink()
            except OSError:
                pass


def ocr_image(path: str, workdir: Path) -> str:
    src = path
    converted = None
    if Path(path).suffix.lower() == ".heic":
        converted = workdir / (Path(path).stem + ".png")
        res = run(["magick", path, "-colorspace", "gray", str(converted)], timeout=600)
        if res.returncode != 0 or not converted.exists():
            raise RuntimeError(f"heic convert failed rc={res.returncode} "
                               f"{(res.stderr or '')[:160]}")
        src = str(converted)
    try:
        return _tesseract(src, workdir)
    finally:
        if converted is not None:
            converted.unlink(missing_ok=True)


XML_TEXT_RE = re.compile(r"<[^>]+>")
GLYPH_RE = re.compile(r'UnicodeString="([^"]*)"')
SPACE_RE = re.compile(r"[ \t\x0b\f\xa0]{2,}")


def zipxml_text(path: str) -> str:
    """Text from OOXML/ODF/XPS containers. XPS keeps text in Glyphs attributes."""
    ext = Path(path).suffix.lower()
    parts: list[str] = []
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if ext == ".xps":
                targets = [n for n in names if n.lower().endswith(".fpage")]
            elif ext == ".xlsx":
                targets = [n for n in names if re.search(r"(sharedStrings|sheet\d*)\.xml$", n)]
            elif ext == ".pptx":
                targets = [n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)]
            elif ext in (".docx", ".dotx"):
                targets = [n for n in names if n == "word/document.xml"]
            else:
                targets = [n for n in names if n == "content.xml"]
            for n in sorted(targets):
                raw = z.read(n).decode("utf-8", "ignore")
                if ext == ".xps":
                    raw = " ".join(GLYPH_RE.findall(raw)) or raw
                parts.append(XML_TEXT_RE.sub(" ", raw))
    except Exception as exc:
        return f"__ERROR__ {exc}"
    joined = " ".join(parts)
    joined = SPACE_RE.sub(" ", joined)
    return re.sub(r"\s+", " ", joined).strip()


RASTER_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".gif"}
WORD_RE = re.compile(r"^[A-Za-zÄÖÜäöüß]{4,}$")


def word_likeness(text: str) -> float:
    """Share of tokens that look like real words. Rotated/garbled OCR scores low."""
    toks = [t for t in re.split(r"\s+", text or "") if t]
    if not toks:
        return 0.0
    return sum(1 for t in toks if WORD_RE.match(t)) / len(toks)


def ocr_best_orientation(img: str, workdir: Path) -> str:
    """OCR an image upright, flipping 180 degrees if that reads better.

    Container scans (and some scanner output) arrive upside-down; tesseract
    returns confident-looking garbage rather than an error, so compare scores.
    """
    best = _tesseract(img, workdir)
    if word_likeness(best) >= 0.25:
        return best
    flipped = Path(workdir) / f"flip{os.getpid()}.png"
    try:
        run(["magick", img, "-rotate", "180", str(flipped)], timeout=300)
        alt = _tesseract(str(flipped), workdir)
    except Exception:
        return best
    finally:
        flipped.unlink(missing_ok=True)
    return alt if word_likeness(alt) > word_likeness(best) else best


def ocr_container_images(path: str, workdir: Path) -> str:
    """OCR the images inside an OOXML/ODF/XPS container.

    Scanned documents are often packaged as a container holding only an image
    (an .xps of a scan, a docx of pasted screenshots): there is no text layer,
    but the page is readable.
    """
    parts: list[str] = []
    tmp = Path(workdir) / f"media{os.getpid()}"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist()
                     if ("/media/" in n or "Images/" in n)
                     and Path(n).suffix.lower() in RASTER_EXT]
            for n in sorted(names)[:25]:
                target = tmp / Path(n).name
                try:
                    target.write_bytes(z.read(n))
                    parts.append(ocr_best_orientation(str(target), workdir))
                except Exception:
                    continue
                finally:
                    target.unlink(missing_ok=True)
    except Exception:
        return ""
    finally:
        try:
            tmp.rmdir()
        except OSError:
            pass
    return "\n".join(p for p in parts if p.strip())


def plain_text(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            raw = fh.read(4 << 20)
    except OSError:
        return ""
    for enc in ("utf-8", "utf-16", "latin-1"):
        try:
            txt = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        return ""
    if Path(path).suffix.lower() in (".html", ".htm", ".svg", ".xml"):
        txt = XML_TEXT_RE.sub(" ", txt)
    return re.sub(r"\s+", " ", txt).strip()


PRINTABLE_RE = re.compile(rb"[\x20-\x7e\xc0-\xff]{4,}")


def legacy_text(path: str) -> str:
    """Best-effort strings recovery for legacy .doc/.xls/.msg. Labelled partial."""
    chunks: list[str] = []
    try:
        with open(path, "rb") as fh:
            raw = fh.read(8 << 20)
    except OSError:
        return ""
    words = [w.decode("latin-1", "ignore") for w in PRINTABLE_RE.findall(raw)]
    chunks = [w for w in words if len(w) >= 5]
    wide_parts = re.findall(r"(?:[\x20-\x7e]\x00){5,}", raw.decode("latin-1", "ignore"))
    chunks += [w.replace("\x00", " ") for w in wide_parts]
    return re.sub(r"\s+", " ", " ".join(chunks)).strip()


# ---------------------------------------------------------------- one file

def extract_one(args: tuple) -> dict:
    """Worker: extract text for a single file. Returns metadata + page rows."""
    path, kind, workdir = args
    res: dict = {"path": path, "status": "ok", "method": kind,
                 "npages": 0, "nchars": 0, "error": None, "pages": []}
    # Per-process scratch dir. A shared dir lets two workers render+unlink the
    # same page filename, which loses images or OCRs another document's page.
    wd = Path(workdir) / f"w{os.getpid()}"
    wd.mkdir(parents=True, exist_ok=True)
    try:
        if kind == "pdf":
            res.update(_extract_pdf(path, wd))
        elif kind == "image":
            txt = ocr_image(path, wd)
            res.update(method="ocr-image", pages=[(1, txt)] if txt else [])
        elif kind == "zipxml":
            txt = zipxml_text(path)
            if txt.startswith("__ERROR__"):
                res.update(status="failed", error=txt[:200], pages=[])
            else:
                res.update(method="zipxml")
                if not txt.strip():
                    txt = ocr_container_images(path, wd)
                    if txt.strip():
                        res.update(method="zipxml+ocr-images")
                res.update(pages=[(1, txt)] if txt.strip() else [])
        elif kind == "plain":
            if looks_like_secret(path):
                res.update(status="skipped", method="secret-guard",
                           error="private key material", pages=[])
            else:
                txt = plain_text(path)
                res.update(method="plain", pages=[(1, txt)] if txt else [])
        elif kind == "legacy":
            txt = legacy_text(path)
            res.update(method="strings-partial",
                       status="partial" if txt else "empty",
                       pages=[(1, txt)] if txt else [])
        else:
            res.update(status="skipped", pages=[])
    except subprocess.TimeoutExpired:
        res.update(status="failed", error="timeout", pages=[])
    except Exception as exc:  # noqa: BLE001
        res.update(status="failed", error=f"{type(exc).__name__}: {exc}"[:200], pages=[])
    res["pages"] = [(p, strip_b64(t)) for p, t in res["pages"]]
    res["npages"] = len(res["pages"])
    res["nchars"] = sum(len(t) for _, t in res["pages"])
    if res["status"] == "ok" and res["nchars"] == 0:
        res["status"] = "empty"
    return res


def _extract_pdf(path: str, wd: Path) -> dict:
    """Text layer first; OCR only the pages (or whole file) with no text."""
    if pdf_broken(path):
        txt = images_salvage(path, wd)
        if txt.strip():
            return {"status": "partial", "method": "images-salvage",
                    "error": "broken xref: recovered embedded images",
                    "pages": [(1, txt)]}
        return {"status": "corrupt", "method": "none",
                "error": "broken xref: poppler cannot parse", "pages": []}
    if pdf_locked(path):
        return {"status": "locked", "method": "encrypted",
                "error": "user password required", "pages": []}
    npages = pdf_page_count(path)
    page_texts = pdf_text_pages(path)
    if len(page_texts) > npages:
        page_texts = page_texts[:npages] or []
    pages: list[tuple[int, str]] = [(i + 1, t.strip()) for i, t in enumerate(page_texts)]
    if sum(len(t) for _, t in pages) < MIN_TEXT_CHARS:
        method = "ocr-pdf"
        pages = [(n, ocr_pdf_page(path, n, wd)) for n in range(1, (npages or 1) + 1)]
    else:
        method = "pdftotext"
        gaps = [n for n, t in pages if len(t) < MIN_PAGE_CHARS]
        for n in gaps:
            if n <= (npages or len(pages)):
                extra = ocr_pdf_page(path, n, wd)
                if len(extra.strip()) > len(dict(pages).get(n, "")):
                    pages = [(p, extra.strip() if p == n else t) for p, t in pages]
        if gaps:
            method = "pdftotext+ocr-gaps"
    return {"method": method, "pages": [(n, t) for n, t in pages if t.strip()]}


# ---------------------------------------------------------------- commands

def cmd_add_root(conn: sqlite3.Connection, root: str) -> str:
    root = os.path.abspath(root)
    conn.execute("INSERT OR IGNORE INTO roots(path, added) VALUES (?,?)", (root, time.time()))
    conn.commit()
    return root


def _excluded(full: str, name: str, excludes: list[str]) -> bool:
    """Excludes are directory names or fnmatch-style patterns, never absolute paths.

    The secret floor is applied first and always: a caller cannot opt out of it.
    """
    if any(part in SECRET_DIRS for part in Path(full).parts):
        return True
    lname = name.lower()
    if any(fnmatch.fnmatch(lname, pat) for pat in SECRET_NAMES):
        return True
    if any(part in excludes for part in Path(full).parts):
        return True
    return any(fnmatch.fnmatch(name, pat) for pat in excludes)


def looks_like_secret(path: str) -> bool:
    """Peek at the head of a text file: private keys must never be indexed."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(4096)
    except OSError:
        return False
    return bool(PRIVATE_KEY_RE.search(head))


B64_RUN_RE = re.compile(r"[A-Za-z0-9+/=]{200,}")


def strip_b64(text: str) -> str:
    """Drop inline base64 (data: URIs, embedded fonts and images).

    It is not language, it matches credential regexes by accident, and it
    floods the index: one archived HTML card carried megabytes of it.
    """
    return B64_RUN_RE.sub("[binary data]", text)


def cmd_scan(conn: sqlite3.Connection, roots: list[str], rehash: bool = False,
             excludes: list[str] | None = None) -> dict:
    excludes = excludes or []
    stats = {"seen": 0, "new": 0, "changed": 0, "dupe": 0, "skip": 0, "excluded": 0}
    for root in roots:
        root = cmd_add_root(conn, root)
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")
                           and not _excluded(os.path.join(dirpath, d), d, excludes)]
            for name in filenames:
                if name.startswith("."):
                    continue
                full = os.path.join(dirpath, name)
                if _excluded(full, name, excludes):
                    stats["excluded"] += 1
                    continue
                try:
                    st = os.stat(full)
                except OSError:
                    # File vanished (remote rename/delete before the next sync).
                    # Record it so a stale 'ok' row is not mistaken for bad OCR.
                    if conn.execute("SELECT 1 FROM files WHERE path=?", (full,)).fetchone():
                        conn.execute("UPDATE files SET status='missing', error='gone from mount',"
                                     " updated=? WHERE path=?", (time.time(), full))
                        conn.commit()
                    continue
                if not os.path.isfile(full):
                    continue
                stats["seen"] += 1
                kind = classify(full)
                row = conn.execute("SELECT size, mtime, status, sha256 FROM files WHERE path=?",
                                   (full,)).fetchone()
                if row and row[0] == st.st_size and abs(row[1] - st.st_mtime) < 1 and row[2] in (
                        "ok", "empty", "partial", "skipped", "duplicate", "missing", "corrupt"):
                    continue
                if kind == "skip":
                    conn.execute(
                        "INSERT OR REPLACE INTO files(path,root,ext,size,mtime,kind,status,method,updated)"
                        " VALUES (?,?,?,?,?,?, 'skipped','skip',?)",
                        (full, root, Path(full).suffix.lower(), st.st_size, st.st_mtime, kind, time.time()))
                    stats["skip"] += 1
                    continue
                digest = row[3] if (row and not rehash) else None
                if not digest:
                    try:
                        digest = sha256_of(full)
                    except OSError:
                        continue
                dupe_of = conn.execute(
                    "SELECT path FROM files WHERE sha256=? AND path<>? AND status IN "
                    "('ok','empty','partial') LIMIT 1", (digest, full)).fetchone()
                status = "duplicate" if dupe_of else "pending"
                if dupe_of:
                    stats["dupe"] += 1
                elif row and row[2] != "pending":
                    stats["changed"] += 1
                elif not row:
                    stats["new"] += 1
                conn.execute(
                    "INSERT OR REPLACE INTO files(path,root,ext,size,mtime,sha256,kind,status,"
                    "method,npages,nchars,error,updated) VALUES (?,?,?,?,?,?,?,?,"
                    "CASE WHEN ? THEN ? ELSE NULL END,0,0,NULL,?)",
                    (full, root, Path(full).suffix.lower(), st.st_size, st.st_mtime, digest,
                     kind, status, 1 if dupe_of else 0,
                     f"duplicate of {dupe_of[0]}" if dupe_of else None, time.time()))
                conn.commit()
    return stats


def pending_rows(conn: sqlite3.Connection, limit: int | None = None,
                 match: str | None = None) -> list[tuple[str, str]]:
    sql = "SELECT path, kind FROM files WHERE status='pending' AND kind<>'skip'"
    params: list = []
    if match:
        sql += " AND path LIKE ?"
        params.append(f"%{match}%")
    sql += " ORDER BY size ASC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, params).fetchall()


def store_result(conn: sqlite3.Connection, res: dict) -> None:
    conn.execute("DELETE FROM pages WHERE path=?", (res["path"],))
    if res["pages"]:
        conn.executemany("INSERT INTO pages(path, page, text) VALUES (?,?,?)",
                         [(res["path"], n, t) for n, t in res["pages"]])
    conn.execute(
        "UPDATE files SET status=?, method=?, npages=?, nchars=?, error=?, updated=? WHERE path=?",
        (res["status"], res["method"], res["npages"], res["nchars"], res["error"],
         time.time(), res["path"]))
    conn.commit()


def prune_workdir(workdir: str) -> None:
    """Clear scratch dirs of dead workers only.

    Never wipe the whole root: a second concurrent `extract` would delete the
    live run's page images mid-OCR (the collision that mis-files document text).
    """
    root = Path(workdir)
    root.mkdir(parents=True, exist_ok=True)
    for child in root.glob("w*"):
        try:
            pid = int(child.name[1:])
        except ValueError:
            continue
        if not Path(f"/proc/{pid}").exists():
            shutil.rmtree(child, ignore_errors=True)


def cmd_extract(conn: sqlite3.Connection, workers: int, limit: int | None,
                workdir: str, heartbeat=None, match: str | None = None) -> dict:
    prune_workdir(workdir)
    rows = pending_rows(conn, limit, match)
    todo = [(p, k, workdir) for p, k in rows]
    done = {"ok": 0, "empty": 0, "failed": 0, "skipped": 0, "partial": 0}
    t0 = time.time()
    if workers <= 1:
        for item in todo:
            res = extract_one(item)
            store_result(conn, res)
            done[res["status"]] = done.get(res["status"], 0) + 1
            _progress(done, len(todo), t0, res)
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(extract_one, item): item[0] for item in todo}
            for fut in as_completed(futs):
                res = fut.result()
                store_result(conn, res)
                done[res["status"]] = done.get(res["status"], 0) + 1
                _progress(done, len(todo), t0, res)
    return done


def _progress(done: dict, total: int, t0: float, res: dict) -> None:
    n = sum(done.values())
    if n % 10 == 0 or n == total or res["status"] in ("failed",):
        el = time.time() - t0
        rate = n / el if el else 0
        left = (total - n) / rate if rate else 0
        print(f"[{n}/{total}] {dict(done)} {res['status']:<8} eta {left/60:.0f}m  "
              f"{os.path.basename(res['path'])[:50]}", flush=True)


def fts_query(raw: str, join: str = " ") -> str:
    """Quote every term so user punctuation cannot break FTS5 syntax.

    User-supplied AND/OR/NOT are treated as literal words: a query made only of
    operators used to raise 'fts5: syntax error near "AND"'. Terms are combined
    with implicit AND, or explicit OR when join='OR'.
    """
    terms = [t for t in re.split(r"\s+", raw.strip()) if t]
    out = []
    for t in terms:
        clean = t.replace('"', "")
        if clean.endswith("*"):
            clean = clean[:-1]
            out.append(f'"{clean}"*')
        elif clean:
            out.append(f'"{clean}"')
    if join == "OR":
        return " OR ".join(out) or '""'
    return " ".join(out) or '""'


def cmd_search(conn: sqlite3.Connection, query: str, limit: int) -> None:
    strict = fts_query(query)
    rows = _run_fts(conn, strict, limit)
    relaxed = False
    if not rows:
        loose = fts_query(query, join="OR")
        if loose != strict:
            rows = _run_fts(conn, loose, limit)
            relaxed = bool(rows)
    if not rows:
        print("no matches")
        return
    if relaxed:
        print("(all terms together: no match - showing pages with any term)\n")
    for path, page, snip in rows:
        rel = path.replace("/data/workspace/onedrive/", "")
        print(f"\n{rel}  [page {page}]")
        print(f"  {re.sub(r'\\s+', ' ', snip)}")
    print(f"\n{len(rows)} match(es)")


def _run_fts(conn: sqlite3.Connection, match: str, limit: int):
    try:
        return conn.execute(
            "SELECT path, page, snippet(pages, 2, '<<', '>>', ' ... ', 18) FROM pages "
            "WHERE pages MATCH ? ORDER BY bm25(pages, 0.0, 0.0, 1.0) LIMIT ?",
            (match, limit)).fetchall()
    except sqlite3.OperationalError as exc:
        print(f"query error: {exc}", file=sys.stderr)
        return []


def cmd_show(conn: sqlite3.Connection, path: str, page: int | None) -> None:
    # Accept a unique path fragment: exact-path lookups are painful to type.
    if not conn.execute("SELECT 1 FROM pages WHERE path=?", (path,)).fetchone():
        hits = [h[0] for h in conn.execute(
            "SELECT DISTINCT path FROM pages WHERE path LIKE ? LIMIT 5", (f"%{path}%",)).fetchall()]
        if len(hits) == 1:
            path = hits[0]
        elif hits:
            print("ambiguous path, choose one:")
            for h in hits:
                print("  " + h)
            return
    if page:
        rows = conn.execute("SELECT page, text FROM pages WHERE path=? AND page=?",
                            (path, page)).fetchall()
    else:
        rows = conn.execute("SELECT page, text FROM pages WHERE path=? ORDER BY page",
                            (path,)).fetchall()
    if not rows:
        hits = conn.execute("SELECT path FROM files WHERE path LIKE ? LIMIT 5",
                            (f"%{path}%",)).fetchall()
        print("no indexed text; similar paths: " + str([h[0] for h in hits])
              if hits else "no indexed text for that path")
        return
    for n, text in rows:
        print(f"--- page {n} ---\n{text}")


def cmd_requeue(conn: sqlite3.Connection, statuses: list[str]) -> int:
    q = ",".join("?" * len(statuses))
    cur = conn.execute(
        f"UPDATE files SET status='pending', error=NULL WHERE status IN ({q})", statuses)
    conn.execute("DELETE FROM pages WHERE path IN (SELECT path FROM files WHERE status='pending')")
    conn.commit()
    return cur.rowcount


def cmd_stats(conn: sqlite3.Connection, db: str) -> None:
    print("roots:")
    for (r,) in conn.execute("SELECT path FROM roots ORDER BY path"):
        print(f"  {r}")
    print("\nby status:")
    for s, k, n in conn.execute(
            "SELECT status, kind, COUNT(*) FROM files GROUP BY status, kind ORDER BY 3 DESC"):
        print(f"  {s:<10} {k:<8} {n}")
    tot = conn.execute("SELECT COUNT(*), COALESCE(SUM(npages),0), COALESCE(SUM(nchars),0) "
                       "FROM files").fetchone()
    print(f"\nfiles={tot[0]} pages={tot[1]} chars={tot[2]}")
    print(f"db size={os.path.getsize(db)/1e6:.1f} MB")


def main() -> int:
    ap = argparse.ArgumentParser(prog="docindex")
    ap.add_argument("--db", default=os.environ.get("DOCINDEX_DB", DB_DEFAULT))
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan")
    p.add_argument("roots", nargs="+")
    p.add_argument("--rehash", action="store_true")
    p.add_argument("--exclude", action="append", default=[],
                   help="directory name or glob to skip (repeatable)")

    p = sub.add_parser("extract")
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--limit", type=int)
    p.add_argument("--match", help="only files whose path contains this text")
    p.add_argument("--workdir", default=WORK_DEFAULT)

    p = sub.add_parser("search")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=20)

    p = sub.add_parser("show")
    p.add_argument("path")
    p.add_argument("--page", type=int)

    p = sub.add_parser("requeue")
    p.add_argument("statuses", nargs="+")

    sub.add_parser("stats")

    args = ap.parse_args()
    conn = connect(args.db)
    if args.cmd == "scan":
        print(cmd_scan(conn, args.roots, args.rehash, args.exclude))
    elif args.cmd == "extract":
        print(cmd_extract(conn, args.workers, args.limit, args.workdir,
                          match=getattr(args, "match", None)))
    elif args.cmd == "search":
        cmd_search(conn, args.query, args.limit)
    elif args.cmd == "show":
        cmd_show(conn, args.path, args.page)
    elif args.cmd == "requeue":
        print("requeued:", cmd_requeue(conn, args.statuses))
    elif args.cmd == "stats":
        cmd_stats(conn, args.db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
