"""browser-lease — one session drives the shared browser at a time.

Two halves in one importable module (no relative imports, so the file runs
standalone under stock CPython as well as inside the plugin package):

1. :class:`BrowserLease` — an advisory, cross-process, auto-expiring lock.
2. The Hermes hook surface that takes that lock around browser tool calls.

Why a file lock and not a proxy or a setting: every Hermes surface (WebUI
threads, gateway/cron agents, kanban workers) reaches the browser as a plain CDP
client on the same endpoint, and they already share one filesystem (the Hermes
home). An advisory ``flock`` in that tree is the one primitive all of them can
honour without a broker process, and the kernel releases it if the holder dies.

The lock file MUST live on storage shared by every jail (the Hermes home), never
``/tmp``: containers with private ``/tmp`` would take two different locks and
serialise nothing.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

log = logging.getLogger("hermes.plugins.browser_lease")

DEFAULT_WAIT_S = 45.0
DEFAULT_STICKY_S = 120.0
_POLL_S = 0.2


class BrowserLease:
    """Advisory, auto-expiring, cross-process lock over the shared browser.

    Hold semantics: taken on the first browser tool call of a turn, renewed while
    calls keep coming, released at turn end, and released by a watchdog after
    ``sticky_s`` without a call so an abandoned turn cannot wedge the engine.
    """

    def __init__(self, owner: str, *, path: Path, wait_s: float = DEFAULT_WAIT_S,
                 sticky_s: float = DEFAULT_STICKY_S,
                 on_wait: Optional[Callable[[float], None]] = None) -> None:
        self.owner = owner
        self.path = Path(path)
        self.wait_s = wait_s
        self.sticky_s = sticky_s
        self.on_wait = on_wait
        self._fd: Optional[int] = None
        self._last_touch = 0.0
        self._lock = threading.RLock()
        self._watchdog: Optional[threading.Thread] = None

    # ---------------------------------------------------------------- internals

    def _write_owner(self) -> None:
        """Record the holder so a waiting session can name it in the tool result."""
        assert self._fd is not None
        os.ftruncate(self._fd, 0)
        os.lseek(self._fd, 0, os.SEEK_SET)
        os.write(self._fd, json.dumps({
            "owner": self.owner, "pid": os.getpid(), "since": time.time(),
        }).encode())

    def _peer_owner(self) -> str:
        """Human-readable description of whoever holds the lease."""
        try:
            with open(self.path, "rb") as fh:
                data = json.loads(fh.read().decode() or "{}")
            who, pid = data.get("owner") or "unknown", data.get("pid")
            age = int(max(0.0, time.time() - float(data.get("since") or 0)))
            return f"{who} (pid {pid}, held {age}s)"
        except Exception:
            return "another session"

    # -------------------------------------------------------------------- public

    def held_by_me(self) -> bool:
        with self._lock:
            return self._fd is not None

    def touch(self) -> None:
        with self._lock:
            self._last_touch = time.time()

    def acquire(self) -> tuple[bool, str]:
        """Take the lease, waiting up to ``wait_s``.

        Returns ``(True, "")`` on success and ``(False, holder)`` when another
        session holds it past the deadline, so the caller can return a retryable
        blocked tool result instead of silently racing the other session.
        """
        with self._lock:
            if self._fd is not None:
                self.touch()
                return True, ""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.time() + self.wait_s
        waited = False
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.time() >= deadline:
                        return False, self._peer_owner()
                    if not waited and self.on_wait:
                        self.on_wait(self.wait_s)
                    waited = True
                    time.sleep(_POLL_S)
            with self._lock:
                self._fd = fd
                self._write_owner()
                self.touch()
                self._start_watchdog()
            return True, ""
        except BaseException:
            os.close(fd)
            raise

    def release(self) -> None:
        with self._lock:
            if self._fd is None:
                return
            fd, self._fd = self._fd, None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    # ----------------------------------------------------------------- watchdog

    def _start_watchdog(self) -> None:
        if self._watchdog and self._watchdog.is_alive():
            return
        self._watchdog = threading.Thread(
            target=self._watch_loop, name="browser-lease-watchdog", daemon=True)
        self._watchdog.start()

    def _watch_loop(self) -> None:
        """Release once the holder has been idle for ``sticky_s``.

        This bounds an abandoned turn: whatever happens to the session that took
        the lease, the engine returns to everyone else.
        """
        while True:
            time.sleep(_POLL_S)
            with self._lock:
                if self._fd is None:
                    return
                idle = time.time() - self._last_touch
            if idle >= self.sticky_s:
                self.release()
                return


# --------------------------------------------------------------------- config

_DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "wait_s": DEFAULT_WAIT_S,
    "sticky_s": DEFAULT_STICKY_S,
    "lock_path": "",
}


def _is_browser_tool(name: str) -> bool:
    """True for the tools that drive the shared engine."""
    return name == "browser" or name.startswith("browser_")


def _default_home() -> Path:
    """Hermes home, preferring the state dir both jails mount."""
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env)
    shared = Path("/data/.hermes")
    return shared if shared.is_dir() else Path.home() / ".hermes"


def _lock_path(cfg: Dict[str, Any]) -> Path:
    override = cfg.get("lock_path")
    return Path(override) if override else _default_home() / "cache" / "browser-lease.lock"


def _cfg() -> Dict[str, Any]:
    """``browser.lease`` from config; defaults on any failure (never break a turn)."""
    values = dict(_DEFAULTS)
    try:
        from hermes_cli.config import load_config_readonly
        section = (load_config_readonly() or {}).get("browser") or {}
        user = section.get("lease") if isinstance(section, dict) else None
        if isinstance(user, dict):
            for key in values:
                if user.get(key) is not None:
                    values[key] = user[key]
    except Exception:
        pass
    return values


class _State:
    """One lease per session id in this process."""

    def __init__(self) -> None:
        self.leases: Dict[str, BrowserLease] = {}

    def for_session(self, session_id: str) -> Optional[BrowserLease]:
        cfg = _cfg()
        if not cfg["enabled"]:
            return None
        lease = self.leases.get(session_id)
        if lease is None:
            lease = BrowserLease(
                owner=session_id or f"pid:{os.getpid()}",
                path=_lock_path(cfg),
                wait_s=float(cfg["wait_s"]),
                sticky_s=float(cfg["sticky_s"]),
                on_wait=lambda wait: log.info(
                    "browser busy; waiting up to %.0fs for the lease", wait),
            )
            self.leases[session_id] = lease
        return lease


STATE = _State()


# ---------------------------------------------------------------------- hooks

def pre_tool_call(tool_name: str = "", session_id: str = "", **_: Any):
    """Take (or renew) the lease before a browser call; block if another session holds it."""
    if not _is_browser_tool(tool_name):
        return None
    lease = STATE.for_session(session_id)
    if lease is None:
        return None
    ok, holder = lease.acquire()
    if ok:
        return None
    return {
        "action": "block",
        "message": (
            f"browser-lease: the browser is checked out by {holder}. "
            "Retry in a few seconds instead of opening a second tab set on the same engine."
        ),
    }


def post_tool_call(tool_name: str = "", session_id: str = "", **_: Any):
    """Renew after a browser call so a multi-call turn keeps the lease."""
    if _is_browser_tool(tool_name):
        lease = STATE.leases.get(session_id)
        if lease is not None:
            lease.touch()
    return None


def post_llm_call(session_id: str = "", **_: Any):
    """Turn boundary: hand the engine back now rather than waiting out the watchdog."""
    lease = STATE.leases.get(session_id)
    if lease is not None:
        lease.release()
    return None


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", pre_tool_call)
    ctx.register_hook("post_tool_call", post_tool_call)
    ctx.register_hook("post_llm_call", post_llm_call)
