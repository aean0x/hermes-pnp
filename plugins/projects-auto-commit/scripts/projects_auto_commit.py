#!/usr/bin/env python3
"""Programmatic monorepo commit (+ optional push) for /data/.hermes/projects.

Intended for end-of-turn hooks so agents/crons do not spend tokens on git.
Idempotent, flock-serialized, never force-pushes, respects .gitignore.

Env:
  HERMES_HOME              default /data/.hermes
  PROJECTS_ROOT            default $HERMES_HOME/projects
  PROJECTS_AUTO_COMMIT     0/false/off → no-op
  PROJECTS_AUTO_PUSH       0/false/off → commit only (default: push)
  PROJECTS_AUTO_COMMIT_MSG optional override message
"""
from __future__ import annotations

import fcntl
import os
import re
import subprocess
import sys
import time
from pathlib import Path


def _truthy(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _run(
    argv: list[str],
    *,
    cwd: Path,
    timeout: float = 60,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        timeout=timeout,
        check=check,
    )


def _short_paths(paths: list[str], limit: int = 6) -> str:
    if not paths:
        return "empty"
    shown = paths[:limit]
    body = ", ".join(shown)
    if len(paths) > limit:
        body += f" (+{len(paths) - limit})"
    # keep commit subject reasonable
    body = re.sub(r"\s+", " ", body).strip()
    if len(body) > 120:
        body = body[:117] + "..."
    return body


def main() -> int:
    if not _truthy("PROJECTS_AUTO_COMMIT", True):
        print("skip: PROJECTS_AUTO_COMMIT disabled")
        return 0

    home = Path(os.environ.get("HERMES_HOME", "/data/.hermes"))
    root = Path(os.environ.get("PROJECTS_ROOT", str(home / "projects"))).resolve()
    git_dir = root / ".git"
    if not git_dir.is_dir():
        print(f"skip: not a git repo: {root}", file=sys.stderr)
        return 0

    lock_path = git_dir / "projects-auto-commit.lock"
    lock_fh = open(lock_path, "a+", encoding="utf-8")
    try:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("skip: another auto-commit holds the lock")
        return 0

    try:
        # Refuse mid-rebase / merge messes
        for marker in ("rebase-merge", "rebase-apply", "MERGE_HEAD", "CHERRY_PICK_HEAD"):
            if (git_dir / marker).exists():
                print(f"skip: in-progress git state ({marker})", file=sys.stderr)
                return 0

        st = _run(["git", "status", "--porcelain"], cwd=root, timeout=30)
        if st.returncode != 0:
            print(st.stderr or st.stdout or "git status failed", file=sys.stderr)
            return 1
        porcelain = st.stdout.strip()
        if not porcelain:
            print("clean")
            return 0

        paths: list[str] = []
        for line in porcelain.splitlines():
            # XY PATH or XY ORIG -> PATH
            entry = line[3:] if len(line) >= 4 else line
            if " -> " in entry:
                entry = entry.split(" -> ", 1)[1]
            entry = entry.strip().strip('"')
            if entry:
                paths.append(entry)

        # Stage everything tracked+untracked allowed by .gitignore
        add = _run(["git", "add", "-A"], cwd=root, timeout=60)
        if add.returncode != 0:
            print(add.stderr or add.stdout or "git add failed", file=sys.stderr)
            return 1

        # Re-check after add (nothing staged → exit)
        staged = _run(["git", "diff", "--cached", "--name-only"], cwd=root, timeout=30)
        names = [n for n in (staged.stdout or "").splitlines() if n.strip()]
        if not names:
            print("clean after add")
            return 0

        msg = os.environ.get("PROJECTS_AUTO_COMMIT_MSG", "").strip()
        if not msg:
            ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            msg = f"auto: {_short_paths(names)} [{ts}]"

        commit = _run(
            [
                "git",
                "-c",
                "user.useConfigOnly=true",
                "commit",
                "-m",
                msg,
            ],
            cwd=root,
            timeout=60,
        )
        if commit.returncode != 0:
            out = (commit.stdout or "") + (commit.stderr or "")
            if "nothing to commit" in out.lower():
                print("clean")
                return 0
            print(out or "git commit failed", file=sys.stderr)
            return 1

        head = _run(["git", "rev-parse", "--short", "HEAD"], cwd=root, timeout=10)
        sha = (head.stdout or "").strip() or "?"
        print(f"committed {sha}: {msg}")

        if not _truthy("PROJECTS_AUTO_PUSH", True):
            print("push skipped (PROJECTS_AUTO_PUSH disabled)")
            return 0

        # Non-force push only
        push = _run(["git", "push", "origin", "HEAD"], cwd=root, timeout=120)
        if push.returncode != 0:
            # Commit succeeded; push failure is soft so turns still finish
            print(push.stderr or push.stdout or "git push failed", file=sys.stderr)
            print(f"committed_local_only {sha}")
            return 0

        print(f"pushed {sha}")
        return 0
    finally:
        try:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        lock_fh.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.TimeoutExpired as e:
        print(f"timeout: {e}", file=sys.stderr)
        raise SystemExit(1)
