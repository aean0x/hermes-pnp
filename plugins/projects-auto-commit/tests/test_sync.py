"""In-process git-sync: pull-before-read, commit only this turn's delta."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import sync


def _git(args, cwd, check=True):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _init_repo(path: Path, *, name="dev", email="dev@example.com") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-b", "main"], cwd=path)
    _git(["config", "user.name", name], cwd=path)
    _git(["config", "user.email", email], cwd=path)
    (path / "README").write_text("hello\n")
    _git(["add", "README"], cwd=path)
    _git(["commit", "-m", "init"], cwd=path)
    return path


class ExtractPaths(unittest.TestCase):
    def test_read_file_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "a.txt"
            target.write_text("x")
            paths = sync.extract_paths("read_file", {"path": str(target)})
            self.assertEqual(paths, [str(target.resolve())])

    def test_patch_header_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "mod.py"
            target.write_text("x")
            patch = f"*** Update File: {target}\n@@\n-x\n+y\n"
            paths = sync.extract_paths("patch", {"patch": patch})
            self.assertEqual(paths, [str(target.resolve())])

    def test_ignores_urls(self):
        paths = sync.extract_paths(
            "vision_analyze", {"image_url": "https://example.com/x.png"}
        )
        self.assertEqual(paths, [])


class SkipRules(unittest.TestCase):
    def test_nix_and_tmp_skipped(self):
        self.assertTrue(sync._skipped("/nix/store/abc"))
        self.assertTrue(sync._skipped("/tmp/foo"))

    def test_hermes_home_skipped_except_projects(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "hermes"
            projects = home / "projects"
            projects.mkdir(parents=True)
            os.environ["HERMES_HOME"] = str(home)
            os.environ["PROJECTS_ROOT"] = str(projects)
            try:
                self.assertTrue(sync._skipped(str(home / "memories" / "MEMORY.md")))
                self.assertFalse(sync._skipped(str(projects / "foo.md")))
            finally:
                os.environ.pop("HERMES_HOME", None)
                os.environ.pop("PROJECTS_ROOT", None)


class GitSync(unittest.TestCase):
    def setUp(self):
        sync.reset_state()
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        os.environ["PROJECTS_ROOT"] = str(self.root)
        os.environ.pop("PROJECTS_AUTO_COMMIT", None)
        os.environ.pop("PROJECTS_AUTO_PUSH", None)
        os.environ.pop("PROJECTS_AUTO_COMMIT_MSG", None)

    def tearDown(self):
        sync.reset_state()
        os.environ.pop("PROJECTS_ROOT", None)
        os.environ.pop("HERMES_HOME", None)
        self.td.cleanup()

    def _clone_pair(self):
        origin = _init_repo(self.root / "origin")
        # make origin a bare remote by cloning
        bare = self.root / "origin.git"
        _git(["clone", "--bare", str(origin), str(bare)], cwd=self.root)
        work = self.root / "work"
        _git(["clone", str(bare), str(work)], cwd=self.root)
        _git(["config", "user.name", "dev"], cwd=work)
        _git(["config", "user.email", "dev@example.com"], cwd=work)
        return bare, work

    def test_pull_ff_only_when_clean(self):
        bare, work = self._clone_pair()
        other = self.root / "other"
        _git(["clone", str(bare), str(other)], cwd=self.root)
        _git(["config", "user.name", "dev"], cwd=other)
        _git(["config", "user.email", "dev@example.com"], cwd=other)
        (other / "new.txt").write_text("from other\n")
        _git(["add", "new.txt"], cwd=other)
        _git(["commit", "-m", "add new"], cwd=other)
        _git(["push", "origin", "HEAD"], cwd=other)

        status = sync.pull_if_clean(str(work))
        self.assertEqual(status, "pulled")
        self.assertTrue((work / "new.txt").exists())

    def test_skip_pull_when_dirty(self):
        _, work = self._clone_pair()
        (work / "README").write_text("dirty\n")
        status = sync.pull_if_clean(str(work))
        self.assertEqual(status, "dirty")

    def test_commit_only_this_turns_delta(self):
        _, work = self._clone_pair()
        os.environ["PROJECTS_AUTO_PUSH"] = "0"
        (work / "wip.txt").write_text("unrelated wip\n")  # pre-existing dirty
        target = work / "touched.txt"

        sync.on_pre_tool_call("write_file", {"path": str(work / "README")})
        target.write_text("ours\n")
        sync.on_post_tool_call("write_file", {"path": str(target)}, status="ok")

        result = sync.commit_and_push(
            str(work), set(sync._dirty.get(str(work), set())), "test"
        )
        self.assertIn("committed", result)
        log = _git(["log", "-1", "--name-only", "--pretty=format:"], cwd=work).stdout
        self.assertIn("touched.txt", log)
        self.assertNotIn("wip.txt", log)
        status = _git(["status", "--porcelain"], cwd=work).stdout
        self.assertIn("wip.txt", status)

    def test_read_then_flush_does_not_commit_unrelated(self):
        _, work = self._clone_pair()
        os.environ["PROJECTS_AUTO_PUSH"] = "0"
        (work / "wip.txt").write_text("wip\n")
        readme = work / "README"
        sync.on_pre_tool_call("read_file", {"path": str(readme)})
        sync.on_post_tool_call("read_file", {"path": str(readme)}, status="ok")
        sync._flush("test")
        log = _git(["log", "-1", "--pretty=%s"], cwd=work).stdout.strip()
        self.assertEqual(log, "init")

    def test_disabled(self):
        os.environ["PROJECTS_AUTO_COMMIT"] = "0"
        self.assertTrue(sync.disabled())
        sync.on_pre_tool_call("read_file", {"path": "/tmp"})
        self.assertEqual(sync._pulled, set())

    def test_register_hooks(self):
        ctx = MagicMock()
        sync.register(ctx)
        names = [c.args[0] for c in ctx.register_hook.call_args_list]
        self.assertEqual(
            names,
            ["pre_tool_call", "post_tool_call", "post_llm_call", "on_session_end"],
        )


if __name__ == "__main__":
    if not shutil.which("git"):
        raise SystemExit("git required")
    unittest.main()
