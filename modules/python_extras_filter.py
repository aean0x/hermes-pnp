#!/usr/bin/env python3
"""Copy extra Python site-packages, skipping dists already in a sealed venv.

Hermes' uv2nix venv aborts the build when extraPythonPackages overlap a
core dist (google-api-core, protobuf, requests, …). This filter walks
each extra derivation, drops any whose METADATA name is already in the
venv, and merges the rest into one overlay directory. Namespace packages
(google.*, grpc.*) keep working because colliding dists stay in the venv.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

_CANON_RE = re.compile(r"[-_.]+")


def canonical(name: str) -> str:
    return _CANON_RE.sub("-", name).lower()


def dist_names(site: Path) -> set[str]:
    names: set[str] = set()
    for dist_info in site.glob("*.dist-info"):
        meta = dist_info / "METADATA"
        if not meta.is_file():
            continue
        for line in meta.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("Name:"):
                names.add(canonical(line.split(":", 1)[1].strip()))
                break
    return names


def site_packages_of(root: Path) -> Path | None:
    matches = sorted(p for p in root.glob("lib/python*/site-packages") if p.is_dir())
    return matches[0] if matches else None


def merge_extra(src_site: Path, dest: Path, core: set[str]) -> str:
    names = dist_names(src_site)
    if names & core:
        return "skip"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src_site, dest, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    return "copy"


def run(venv_site: Path, dest: Path, extras: list[Path]) -> int:
    if not venv_site.is_dir():
        print(f"python extras: venv site-packages missing: {venv_site}", file=sys.stderr)
        return 1
    core = dist_names(venv_site)
    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    for extra in extras:
        src_site = site_packages_of(extra)
        if src_site is None:
            print(f"python extras: no site-packages in {extra}", file=sys.stderr)
            return 1
        action = merge_extra(src_site, dest, core)
        print(f"python extras: {action} {extra.name} ({', '.join(sorted(dist_names(src_site))) or 'no-dist'})")
        if action == "copy":
            copied += 1
    print(f"python extras: copied {copied}/{len(extras)} extras; core dists {len(core)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venv-site", type=Path, required=True)
    parser.add_argument("--dest", type=Path, required=True)
    parser.add_argument("extras", nargs="*", type=Path)
    args = parser.parse_args(argv)
    return run(args.venv_site, args.dest, args.extras)


if __name__ == "__main__":
    raise SystemExit(main())
