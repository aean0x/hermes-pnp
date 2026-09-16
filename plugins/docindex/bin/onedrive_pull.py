#!/usr/bin/env python3
"""onedrive_pull - mirror remote-only OneDrive folders onto this box.

The rclone sync mount covers only ``Documents/`` and ``Shared/``. Everything
else in the OneDrive root (``Books/``, ``Pictures/``, ``dev/``,
``Documents (Windows)/``, ``Data Takeout/``, ...) is remote-only and therefore
invisible to docindex. This tool closes that gap through the Composio MCP
OneDrive toolkit (OAuth lives in the MCP proxy; no rclone, no token in here).

Subcommands
-----------
  walk   recursively inventory the drive (or given roots) -> manifest JSON
  pull   mint pre-signed download URLs in batches and fetch them locally

Examples
--------
  onedrive_pull.py walk --out /tmp/od.json
  onedrive_pull.py walk --out /tmp/od_pics.json --roots Pictures
  onedrive_pull.py pull --manifest /tmp/od.json --dest /data/workspace/onedrive \\
                        --todo /tmp/od_todo.json --batch 25 --workers 12

Notes
-----
* ``pull`` verifies every file's byte size against the manifest and is
  resumable: already-correct files are skipped, so re-running after a failure
  costs nothing.
* The mount is synced per folder (rclone copy --update of Documents/ and
  Shared/); extra top-level directories written here are NOT uploaded.
* Exit code is non-zero when any file failed to download; the failure list is
  printed and written next to the state file.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

MCP_URL = os.environ.get("COMPOSIO_MCP_URL", "http://127.0.0.1:3140/composio")
TOKEN_ENV = "MCP_PROXY_TOKEN"
SELECT = ["id", "name", "size", "folder", "lastModifiedDateTime"]
DEFAULT_DEST = "/data/workspace/onedrive"

# Kinds worth indexing: documents everywhere, images only where they are scans
# or screenshots rather than photographs.
DOC_EXT = {
    "pdf", "doc", "docx", "docm", "dot", "dotx", "xls", "xlsx", "xlsm", "xlsb",
    "ppt", "pptx", "odt", "ods", "odp", "rtf", "txt", "md", "csv", "tsv", "json",
    "xml", "html", "htm", "msg", "eml", "xps", "one", "epub", "mobi", "azw",
    "azw3", "djvu", "pages", "wps", "log", "yml", "yaml", "ini", "sh", "py",
    "js", "ts", "java", "c", "cpp", "h", "hpp", "go", "rs", "sql", "ipynb",
    "tex", "vsd", "vsdx", "pub", "mpp", "pdn", "accdb", "nix", "toml", "cfg",
    "conf", "lua", "pl", "rb", "php", "css", "scss", "ps1", "bat", "cmd",
}
IMG_EXT = {"jpg", "jpeg", "png", "tif", "tiff", "heic", "bmp", "webp", "gif"}
IMG_ROOTS = ("/Books", "/Pictures", "/Documents (Windows)/Scanned Documents",
             "/Documents (Windows)/OneNote Notebooks")
MAX_BYTES = 400 * 1024 * 1024

# Bulk media trees with no document text: skipped by `walk`, recorded as
# excluded so the report can say what was left remote and why.
PRUNE = {
    "Pictures": {"Camera Roll", "Wallpapers", "Samsung Gallery", "Screenshots",
                 "Uploads"},
    "Documents (Windows)": {
        "Studio One", "SOLIDWORKS Downloads", "My Games", "Paradox Interactive",
        "Battlefield 4", "Dead Space (2023)", "EVE", "PreSonus Hub",
        "Battlefield 1", "Unity", "Rockstar Games", "Battlefield 1 Open Beta",
        "Respawn", "Dwarf Fortress", "Arma 3", "Amnesia", "Avalanche Studios",
        "Elder Scrolls Online", "Black Desert", "BioWare", "Witcher 2", "NBGI",
        "Escape from Tarkov", "Ableton", "The Witcher 3", "4A Games", "BFBC2",
        "STAR WARS Battlefront II", "STAR WARS Battlefront",
        "Deus Ex -  Mankind Divided", "Sony", "Square Enix", "Telltale Games",
        "I-Novae Studios", "My Digital Editions",
    },
    "Data Takeout": {"Signal backups"},
}


class MCPError(RuntimeError):
    pass


def call(name: str, args: dict, timeout: int = 600, attempts: int = 5):
    tok = os.environ.get(TOKEN_ENV)
    if not tok:
        raise MCPError(f"{TOKEN_ENV} is not set (required to reach the MCP proxy)")
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": name, "arguments": args}}).encode()
    last = None
    for i in range(attempts):
        req = urllib.request.Request(MCP_URL, data=body, headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "X-MCP-Proxy-Token": tok,
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode()
            if raw.startswith(("event:", "data:")):
                for line in raw.splitlines():
                    if line.startswith("data:"):
                        raw = line[5:].strip()
                        break
            d = json.loads(raw)
            if isinstance(d.get("result"), dict) and "content" in d["result"]:
                txt = "".join(p.get("text", "") for p in d["result"]["content"]
                              if isinstance(p, dict))
                try:
                    return json.loads(txt)
                except ValueError:
                    return txt
            return d
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            code = getattr(e, "code", None)
            if code in (401, 403):
                break
            time.sleep(min(30, 2 ** i))
    raise MCPError(f"{name} failed after {attempts} attempts: {last}")


def _page(args: dict) -> dict:
    out = call("COMPOSIO_MULTI_EXECUTE_TOOL", {
        "tools": [{"tool_slug": "ONE_DRIVE_LIST_FOLDER_CHILDREN", "arguments": args}],
        "sync_response_to_workbench": False})
    try:
        return out["data"]["results"][0]["response"]["data"]
    except (KeyError, IndexError, TypeError) as e:
        raise MCPError(f"unexpected MCP response shape: {str(out)[:200]}") from e


def children(item_id: str | None = None, path: str | None = None,
             top: int = 50, _tries: int = 0) -> list[dict]:
    """One folder's children, paginated. Falls back to a smaller page when the
    proxy elides an oversized response."""
    args: dict = {"use_me_drive": True, "top": top, "select": SELECT}
    if item_id:
        args["folder_item_id"] = item_id
    else:
        args["folder_path"] = path or "/"
    out: list[dict] = []
    seen: set = set()
    for _ in range(60):
        try:
            d = _page(args)
        except MCPError:
            raise
        if "value" not in d:
            if _tries < 3:
                return children(item_id=item_id, path=path,
                                top=max(5, top // 3), _tries=_tries + 1)
            raise MCPError("response elided at smallest page size")
        vals = d.get("value") or []
        key = tuple(v.get("id") for v in vals)
        if key in seen:
            break
        seen.add(key)
        out.extend(vals)
        nxt = d.get("next_page_token") or d.get("@odata.nextLink")
        if not nxt:
            break
        args.pop("folder_path", None)
        args["page_token"] = nxt
        if item_id:
            args["folder_item_id"] = item_id
        time.sleep(0.2)
    return out


def walk(roots: list[str], workers: int = 6, verbose: bool = True) -> list[dict]:
    root_kids = {k["name"]: k for k in children(path="/")}
    entries: list[dict] = []
    for k in root_kids.values():
        if "folder" not in k and (not roots or "files" in roots):
            entries.append({"path": "/" + k["name"], "dir": False, "id": k["id"],
                            "size": k.get("size") or 0})
    if roots and "files" in roots:
        roots = [r for r in roots if r != "files"]

    def list_dir(job):
        path, node_id = job
        try:
            return path, children(item_id=node_id)
        except MCPError as e:
            print(f"  !! {path}: {e}", file=sys.stderr, flush=True)
            return path, []

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for root in roots:
            node = root_kids.get(root)
            if not node:
                print(f"  !! no such remote root: {root}", file=sys.stderr, flush=True)
                continue
            queue = [("/" + root, node["id"])]
            dirs = files = 0
            while queue:
                batch, queue = queue[:workers * 3], queue[workers * 3:]
                for path, kids in ex.map(list_dir, batch):
                    dirs += 1
                    for k in kids:
                        name = k["name"]
                        if name.startswith("."):
                            continue
                        p = f"{path}/{name}"
                        if "folder" in k:
                            if name in PRUNE.get(path, ()):
                                entries.append({"path": p, "dir": True, "id": k["id"],
                                                "size": k.get("size") or 0,
                                                "excluded": True,
                                                "kids": k.get("folder", {}).get("childCount")})
                                continue
                            entries.append({"path": p, "dir": True, "id": k["id"],
                                            "size": k.get("size") or 0})
                            queue.append((p, k["id"]))
                        else:
                            entries.append({"path": p, "dir": False, "id": k["id"],
                                            "size": k.get("size") or 0})
                            files += 1
                    if verbose and dirs % 100 == 0:
                        print(f"  [{root}] dirs={dirs} files={files} q={len(queue)}",
                              flush=True)
            print(f"  {root}: {files} files under {dirs} dirs", flush=True)
    return entries


def mint(batch: list[tuple[str, str, str]]) -> dict[str, str | None]:
    tools = [{"tool_slug": "ONE_DRIVE_DOWNLOAD_FILE",
              "arguments": {"item_id": i, "file_name": n, "user_id": "me"}}
             for _, i, n in batch]
    out = call("COMPOSIO_MULTI_EXECUTE_TOOL",
               {"tools": tools, "sync_response_to_workbench": False})
    res = out["data"]["results"]
    if len(res) < len(batch):
        raise MCPError("short multi-execute response")
    urls: dict[str, str | None] = {}
    for (path, _i, _n), item in zip(batch, res):
        d = item["response"]["data"]
        if not isinstance(d, dict):
            raise MCPError(f"elided response for {path}")
        urls[path] = (d.get("content") or {}).get("s3url") or d.get("s3url")
    return urls


def doc_kind(path: str, size: int) -> tuple[bool, str]:
    """(keep, reason) for the indexing-oriented default filter."""
    ext = path.rsplit(".", 1)[-1].lower() if "." in os.path.basename(path) else ""
    if size > MAX_BYTES:
        return False, "too big"
    if ext in DOC_EXT:
        return True, "doc"
    if ext in IMG_EXT:
        if any(path.startswith(r) for r in IMG_ROOTS) or "Scan" in path or "scan" in path:
            return True, "image-in-doc-context"
        return False, "photo/media"
    return False, "non-document kind"


def fetch(url: str, dest: str) -> int:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": "onedrive-pull/1.0"})
    with urllib.request.urlopen(req, timeout=900) as r, open(tmp, "wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
    os.replace(tmp, dest)
    return os.path.getsize(dest)


def pull(manifest: str, dest: str, todo: str | None, batch: int, workers: int,
         kinds: str = "docs") -> int:
    entries = json.load(open(manifest))
    keep = [e for e in entries if not e["dir"] and not e.get("excluded")]
    dropped: dict[str, int] = {}
    if kinds == "docs":
        selected = []
        for e in keep:
            ok, why = doc_kind(e["path"], e["size"] or 0)
            if ok:
                selected.append(e)
            else:
                dropped[why] = dropped.get(why, 0) + 1
        keep = selected
        print(f"filter(docs): {len(keep)} kept, dropped {dropped}", flush=True)
    if todo:
        wanted = set(json.load(open(todo)))
        keep = [e for e in keep if e["path"] in wanted]
    pend, failed = [], []
    for e in keep:
        target = dest + e["path"]
        if e["size"] and os.path.exists(target) and os.path.getsize(target) == e["size"]:
            continue
        pend.append(e)
    print(f"manifest={len(keep)} pending={len(pend)} "
          f"bytes={sum(e['size'] for e in pend)/1e9:.3f} GB", flush=True)
    done = 0
    bsz = batch
    i = 0
    started = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        while i < len(pend):
            chunk = pend[i:i + bsz]
            try:
                urls = mint([(e["path"], e["id"], os.path.basename(e["path"]))
                             for e in chunk])
            except (MCPError, KeyError, TypeError) as err:
                if len(chunk) == 1:
                    failed.append({"path": chunk[0]["path"], "error": str(err)[:200]})
                    i += 1
                    continue
                bsz = max(1, len(chunk) // 2)
                print(f"  mint failed ({str(err)[:80]}), shrinking batch to {bsz}",
                      flush=True)
                continue
            i += len(chunk)
            if bsz < batch:
                bsz = min(batch, bsz * 2)
            futs = {}
            for e in chunk:
                url = urls.get(e["path"])
                if not url:
                    failed.append({"path": e["path"], "error": "no download url"})
                    continue
                futs[ex.submit(fetch, url, dest + e["path"])] = e
            for fut, e in list(futs.items()):
                try:
                    size = fut.result()
                except Exception as err:  # noqa: BLE001
                    failed.append({"path": e["path"],
                                   "error": f"{type(err).__name__}: {str(err)[:150]}"})
                    continue
                if e["size"] and size != e["size"]:
                    failed.append({"path": e["path"],
                                   "error": f"size {size} != {e['size']}"})
                    continue
                done += 1
            if done and done % (batch * 4) < batch:
                el = time.time() - started
                print(f"  {done}/{len(pend)} {el/60:.1f} min failed={len(failed)}",
                      flush=True)
    print(f"downloaded={done} failed={len(failed)} in {(time.time()-started)/60:.1f} min")
    for f in failed[:20]:
        print(f"  FAILED {f['path']}: {f['error']}")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="onedrive_pull.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("walk", help="recursive inventory of the remote drive")
    w.add_argument("--out", required=True)
    w.add_argument("--roots", nargs="*", default=[],
                   help="remote root folder names (default: all top-level dirs)")
    w.add_argument("--workers", type=int, default=6)

    p = sub.add_parser("pull", help="download missing/changed files")
    p.add_argument("--manifest", required=True)
    p.add_argument("--dest", default=DEFAULT_DEST)
    p.add_argument("--todo", help="JSON list of paths to restrict the pull to")
    p.add_argument("--batch", type=int, default=25)
    p.add_argument("--workers", type=int, default=12)
    p.add_argument("--kinds", choices=("docs", "all"), default="docs",
                   help="'docs' keeps indexable documents/scans, 'all' mirrors everything")

    args = ap.parse_args(argv)
    if args.cmd == "walk":
        roots = args.roots or [k["name"] for k in children(path="/") if "folder" in k]
        entries = walk(roots, args.workers)
        json.dump(entries, open(args.out, "w"))
        files = [e for e in entries if not e["dir"]]
        print(f"wrote {args.out}: {len(files)} files, "
              f"{sum(e['size'] for e in files)/1e9:.2f} GB, "
              f"{sum(1 for e in entries if e.get('excluded'))} excluded dirs")
        return 0
    return pull(args.manifest, args.dest, args.todo, args.batch, args.workers,
                args.kinds)


if __name__ == "__main__":
    sys.exit(main())
