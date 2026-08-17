"""Hook-driven git sync for any worktree Hermes touches.

pre_tool_call  — ff-only pull on first read of a clean repo (before the
                 tool result reaches the model).
post_tool_call — record porcelain delta (only files this turn changed).
post_llm_call / on_session_end — commit those paths and push.

Fail-open everywhere. Never registers a tool. Never shells a sidecar.
"""
from __future__ import annotations

import fcntl
import logging
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Set

log = logging.getLogger("plugins.projects_auto_commit")

_WRITE_TOOLS = frozenset({"write_file", "patch", "skill_manage"})
_PATH_KEYS = ("path", "file_path", "workdir")
_SKIP_PREFIXES = (
    "/nix/",
    "/proc/",
    "/sys/",
    "/dev/",
    "/run/",
    "/tmp/",
    "/var/tmp/",
)
_PATCH_FILE_RE = re.compile(
    r"^\*\*\* (?:(?:Update|Add|Delete) File|Rename File(?: From)?): (.+)$"
)
_FALSE = frozenset({"0", "false", "no", "off", ""})

_lock = threading.Lock()
_pulled: Set[str] = set()
_before: Dict[str, frozenset[str]] = {}
_dirty: Dict[str, Set[str]] = {}
_root_cache: Dict[str, Optional[str]] = {}


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in _FALSE


def disabled() -> bool:
    return not _truthy("PROJECTS_AUTO_COMMIT", True)


def _home() -> Path:
    return Path(
        os.environ.get("HERMES_HOME")
        or os.environ.get("HOME")
        or "/data/.hermes"
    ).expanduser().resolve()


def _projects_root() -> Path:
    raw = os.environ.get("PROJECTS_ROOT", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (_home() / "projects").resolve()


def _timeout(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _git_bin() -> str:
    found = shutil.which("git")
    if found:
        return found
    for candidate in (
        "/run/current-system/sw/bin/git",
        "/usr/bin/git",
        "/opt/homebrew/bin/git",
    ):
        if os.access(candidate, os.X_OK):
            return candidate
    return "git"


def _git(
    args: list[str],
    cwd: str,
    timeout: float,
    extra_env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    extra = os.environ.get("PROJECTS_AUTO_COMMIT_PATH", "").strip()
    bits = [p for p in extra.split(":") if p]
    bits += [
        str(_home().parent / "toolbox" / "bin"),
        "/data/toolbox/bin",
        "/run/current-system/sw/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
    ]
    cur = env.get("PATH", "")
    env["PATH"] = ":".join(bits + ([cur] if cur else []))
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [_git_bin(), *args],
        cwd=cwd,
        timeout=timeout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        check=False,
    )


def _existing_path(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text.startswith(("http://", "https://", "data:")):
        return None
    try:
        path = Path(text).expanduser()
        if path.exists():
            return str(path.resolve())
    except OSError:
        return None
    # Parent may exist even if the file is about to be created.
    try:
        parent = Path(text).expanduser().parent
        if parent.exists():
            return str(parent.resolve())
    except OSError:
        return None
    return None


def extract_paths(tool_name: str, args: Optional[Dict[str, Any]]) -> list[str]:
    """Filesystem paths a tool is about to touch. Conservative."""
    args = args if isinstance(args, dict) else {}
    found: list[str] = []
    seen: Set[str] = set()

    def _add(raw: Any) -> None:
        path = _existing_path(raw)
        if path and path not in seen:
            seen.add(path)
            found.append(path)

    for key in _PATH_KEYS:
        _add(args.get(key))
    _add(args.get("image_url"))

    patch_text = args.get("patch")
    if isinstance(patch_text, str):
        for line in patch_text.splitlines():
            match = _PATCH_FILE_RE.match(line)
            if match:
                _add(match.group(1).strip())

    if tool_name in {"terminal", "execute_code"} and not found:
        _add(os.getcwd())
    return found


def _skipped(path: str) -> bool:
    try:
        p = Path(path).resolve()
        projects = _projects_root()
        if p == projects or projects in p.parents:
            return False
        home = _home()
        if p == home or home in p.parents:
            return True
    except OSError:
        return True
    resolved = str(p).rstrip("/") + "/"
    if any(resolved.startswith(pref) or str(p) == pref.rstrip("/") for pref in _SKIP_PREFIXES):
        return True
    return False


def git_root(path: str) -> Optional[str]:
    """Innermost worktree containing path, or None if skipped / not git."""
    try:
        start = Path(path)
        start = start if start.is_dir() else start.parent
        start = start.resolve()
    except OSError:
        return None
    key = str(start)
    if key in _root_cache:
        return _root_cache[key]
    if _skipped(key):
        _root_cache[key] = None
        return None
    try:
        proc = _git(
            ["rev-parse", "--show-toplevel"],
            cwd=key,
            timeout=3,
        )
    except (subprocess.TimeoutExpired, OSError):
        _root_cache[key] = None
        return None
    if proc.returncode != 0:
        _root_cache[key] = None
        return None
    root = (proc.stdout or "").strip()
    if not root:
        _root_cache[key] = None
        return None
    if _skipped(root):
        _root_cache[key] = None
        return None
    _root_cache[key] = root
    return root


def _porcelain_paths(root: str) -> frozenset[str]:
    try:
        proc = _git(["status", "--porcelain", "-z"], cwd=root, timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        return frozenset()
    if proc.returncode != 0 or not proc.stdout:
        return frozenset()
    parts = proc.stdout.split("\0")
    paths: Set[str] = set()
    i = 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if not entry:
            continue
        # "XY PATH" — status is 2 chars, then space, then path.
        rel = entry[3:] if len(entry) > 3 else ""
        if not rel:
            continue
        paths.add(rel)
        # rename/copy: next -z field is the original path
        if entry[0] in {"R", "C"} or (len(entry) > 1 and entry[1] in {"R", "C"}):
            if i < len(parts) and parts[i]:
                paths.add(parts[i])
                i += 1
    return frozenset(paths)


def _busy(root: str) -> bool:
    git_dir = Path(root) / ".git"
    if git_dir.is_file():
        return False
    for name in (
        "MERGE_HEAD",
        "REBASE_HEAD",
        "rebase-merge",
        "rebase-apply",
        "CHERRY_PICK_HEAD",
        "REVERT_HEAD",
    ):
        if (git_dir / name).exists():
            return True
    return False


def _has_upstream(root: str) -> bool:
    try:
        proc = _git(
            ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
            cwd=root,
            timeout=3,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0 and bool((proc.stdout or "").strip())


def _with_repo_lock(root: str):
    git_dir = Path(root) / ".git"
    if git_dir.is_file():
        # linked worktree: lock next to the worktree, not the shared git dir
        lock_path = Path(root) / ".git-auto-sync.lock"
    else:
        lock_path = git_dir / "projects-auto-commit.lock"

    class _Guard:
        def __enter__(self):
            self.fh = open(lock_path, "a+")
            try:
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                self.fh.close()
                raise
            return self

        def __exit__(self, *exc):
            try:
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
            finally:
                self.fh.close()

    return _Guard()


def pull_if_clean(root: str) -> str:
    """ff-only pull. Returns a short status token. Never raises."""
    if root in _pulled:
        return "already"
    if _busy(root):
        return "busy"
    if _porcelain_paths(root):
        return "dirty"
    if not _has_upstream(root):
        _pulled.add(root)
        return "no-upstream"
    timeout = _timeout("PROJECTS_AUTO_PULL_TIMEOUT_S", 12)
    try:
        with _with_repo_lock(root):
            if _porcelain_paths(root):
                return "dirty"
            proc = _git(["pull", "--ff-only", "--quiet"], cwd=root, timeout=timeout)
    except OSError:
        return "locked"
    except subprocess.TimeoutExpired:
        log.warning("projects-auto-commit: pull timeout %s", root)
        return "timeout"
    _pulled.add(root)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[:300]
        log.info("projects-auto-commit: pull skipped %s (%s)", root, err)
        return "failed"
    return "pulled"


def _commit_message(paths: Iterable[str]) -> str:
    override = os.environ.get("PROJECTS_AUTO_COMMIT_MSG", "").strip()
    if override:
        return override
    names = [Path(p).name for p in paths]
    if len(names) == 1:
        return f"update {names[0]}"
    return f"update {len(names)} files"


def commit_and_push(root: str, paths: Set[str], source: str) -> str:
    """Stage only `paths`, commit, optionally push. Fail-open."""
    if not paths:
        return "clean"
    if _busy(root):
        return "busy"
    rels = sorted({p for p in paths if p and not p.startswith("/")})
    if not rels:
        return "clean"
    try:
        with _with_repo_lock(root):
            add = _git(["add", "--", *rels], cwd=root, timeout=10)
            if add.returncode != 0:
                log.warning(
                    "projects-auto-commit: add failed %s %s",
                    root,
                    (add.stderr or "").strip()[:200],
                )
                return "add-failed"
            if not _porcelain_paths(root):
                return "clean"
            msg = _commit_message(rels)
            # useConfigOnly: never invent hermes@local / agent@hostname
            commit = _git(
                [
                    "-c",
                    "user.useConfigOnly=true",
                    "commit",
                    "-m",
                    msg,
                    "--",
                    *rels,
                ],
                cwd=root,
                timeout=15,
                extra_env={"GIT_TERMINAL_PROMPT": "0"},
            )
            if commit.returncode != 0:
                err = (commit.stderr or commit.stdout or "").strip()[:300]
                log.info("projects-auto-commit: commit skipped %s (%s)", root, err)
                return "commit-skipped"
            sha = (_git(["rev-parse", "--short", "HEAD"], cwd=root, timeout=3).stdout or "").strip()
            if not _truthy("PROJECTS_AUTO_PUSH", True):
                log.info("projects-auto-commit [%s]: committed %s (push off)", source, sha)
                return f"committed {sha}"
            if not _has_upstream(root):
                push = _git(["push", "origin", "HEAD"], cwd=root, timeout=_timeout("PROJECTS_AUTO_PUSH_TIMEOUT_S", 20))
            else:
                push = _git(["push"], cwd=root, timeout=_timeout("PROJECTS_AUTO_PUSH_TIMEOUT_S", 20))
            if push.returncode != 0:
                log.warning(
                    "projects-auto-commit [%s]: committed %s, push failed %s",
                    source,
                    sha,
                    (push.stderr or "").strip()[:200],
                )
                return f"committed_local_only {sha}"
            log.info("projects-auto-commit [%s]: pushed %s", source, sha)
            return f"pushed {sha}"
    except OSError:
        return "locked"
    except subprocess.TimeoutExpired:
        log.warning("projects-auto-commit: git timeout %s", root)
        return "timeout"


def _roots_for(tool_name: str, args: Optional[Dict[str, Any]]) -> list[str]:
    roots: list[str] = []
    seen: Set[str] = set()
    for path in extract_paths(tool_name, args):
        root = git_root(path)
        if root and root not in seen:
            seen.add(root)
            roots.append(root)
    return roots


def on_pre_tool_call(
    tool_name: str = "",
    args: Optional[Dict[str, Any]] = None,
    **_kwargs: Any,
) -> None:
    if disabled():
        return
    # Writes still record a before-snapshot; pull only on non-write.
    do_pull = tool_name not in _WRITE_TOOLS
    with _lock:
        for root in _roots_for(tool_name, args):
            if root not in _before:
                _before[root] = _porcelain_paths(root)
            if do_pull:
                status = pull_if_clean(root)
                if status == "pulled":
                    _before[root] = _porcelain_paths(root)


def on_post_tool_call(
    tool_name: str = "",
    args: Optional[Dict[str, Any]] = None,
    status: str = "",
    **_kwargs: Any,
) -> None:
    if disabled():
        return
    if status in {"blocked"}:
        return
    with _lock:
        for root in _roots_for(tool_name, args):
            before = _before.get(root, frozenset())
            after = _porcelain_paths(root)
            delta = set(after - before)
            if delta:
                _dirty.setdefault(root, set()).update(delta)
            _before[root] = after


def _flush(source: str) -> None:
    if disabled():
        return
    with _lock:
        pending = {root: set(paths) for root, paths in _dirty.items() if paths}
        _dirty.clear()
    for root, paths in pending.items():
        try:
            commit_and_push(root, paths, source)
        except Exception:
            log.exception("projects-auto-commit: flush failed %s", root)


def on_post_llm_call(**kwargs: Any) -> None:
    if kwargs.get("error"):
        return
    _flush("post_llm_call")


def on_session_end(**kwargs: Any) -> None:
    reason = str(kwargs.get("turn_exit_reason") or "")
    if reason == "error" and kwargs.get("error"):
        return
    _flush(f"on_session_end:{reason or 'unknown'}")


def reset_state() -> None:
    """Tests only."""
    with _lock:
        _pulled.clear()
        _before.clear()
        _dirty.clear()
        _root_cache.clear()


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    ctx.register_hook("post_tool_call", on_post_tool_call)
    ctx.register_hook("post_llm_call", on_post_llm_call)
    ctx.register_hook("on_session_end", on_session_end)
    log.info("projects-auto-commit: registered (in-process, any worktree)")
