"""Collision-aware extra site-packages merge."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from python_extras_filter import dist_names, merge_extra, run


def _write_dist(site: Path, name: str, *rel_files: str) -> None:
    site.mkdir(parents=True, exist_ok=True)
    dist = site / f"{name.replace('-', '_')}-1.0.dist-info"
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "METADATA").write_text(f"Name: {name}\nVersion: 1.0\n", encoding="utf-8")
    for rel in rel_files:
        path = site / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {name}\n", encoding="utf-8")


def _pkg_root(tmp: Path, leaf: str, dist_name: str, *rel_files: str) -> Path:
    root = tmp / leaf
    site = root / "lib" / "python3.12" / "site-packages"
    _write_dist(site, dist_name, *rel_files)
    return root


class DistNamesTests(unittest.TestCase):
    def test_empty_dir(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        site = tmp / "site"
        site.mkdir()
        self.assertEqual(dist_names(site), set())

    def test_canonicalizes_underscores(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        site = tmp / "site"
        _write_dist(site, "Google_API.core")
        self.assertEqual(dist_names(site), {"google-api-core"})


class MergeTests(unittest.TestCase):
    def test_skips_core_dist_keeps_new_namespace(self) -> None:
        base = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        venv_site = base / "venv" / "lib" / "python3.12" / "site-packages"
        _write_dist(venv_site, "google-api-core", "google/api_core/__init__.py")
        core = _pkg_root(base, "google-api-core", "google-api-core", "google/api_core/retry.py")
        leaf = _pkg_root(
            base,
            "google-cloud-pubsub",
            "google-cloud-pubsub",
            "google/cloud/pubsub_v1/__init__.py",
        )
        dest = base / "overlay"
        self.assertEqual(run(venv_site, dest, [core, leaf]), 0)
        self.assertFalse((dest / "google" / "api_core" / "retry.py").exists())
        self.assertTrue((dest / "google" / "cloud" / "pubsub_v1" / "__init__.py").is_file())

    def test_skips_when_venv_already_has_leaf(self) -> None:
        base = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        venv_site = base / "venv" / "lib" / "python3.12" / "site-packages"
        _write_dist(venv_site, "google-cloud-pubsub", "google/cloud/pubsub_v1/__init__.py")
        leaf = _pkg_root(
            base,
            "google-cloud-pubsub",
            "google-cloud-pubsub",
            "google/cloud/pubsub_v1/subscriber.py",
        )
        dest = base / "overlay"
        self.assertEqual(run(venv_site, dest, [leaf]), 0)
        self.assertFalse((dest / "google" / "cloud" / "pubsub_v1" / "subscriber.py").exists())

    def test_missing_venv_fails(self) -> None:
        base = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        self.assertEqual(run(base / "missing", base / "dest", []), 1)

    def test_copy_when_no_overlap(self) -> None:
        base = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        src = base / "src"
        _write_dist(src, "grpcio", "grpc/__init__.py")
        dest = base / "dest"
        self.assertEqual(merge_extra(src, dest, {"protobuf"}), "copy")
        self.assertTrue((dest / "grpc" / "__init__.py").is_file())
