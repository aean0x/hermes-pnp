"""End-of-turn auto-commit for a git working tree.

Default tree is $HERMES_HOME/projects (override PROJECTS_ROOT). Fires after
each completed agent turn (post_llm_call once the tool loop ends, plus
on_session_end as a belt-and-suspenders path). Spawns the commit script
out-of-process so a slow push cannot block the turn finalizer for long — we
wait a short budget then detach if needed.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

log = logging.getLogger("plugins.projects_auto_commit")

_HOME = Path(os.environ.get("HERMES_HOME") or os.environ.get("HOME") or "/data/.hermes")
_PLUGIN_DIR = Path(__file__).resolve().parent


def _script_path() -> Path:
    explicit = os.environ.get("PROJECTS_AUTO_COMMIT_SCRIPT", "").strip()
    if explicit:
        return Path(explicit)
    bundled = _PLUGIN_DIR / "scripts" / "projects_auto_commit.py"
    if bundled.is_file():
        return bundled
    return _HOME / "scripts" / "projects_auto_commit.py"


_SCRIPT = _script_path()
# Soft wait so clean trees return instantly; dirty+push may finish in-band.
_WAIT_S = float(os.environ.get("PROJECTS_AUTO_COMMIT_WAIT_S", "25"))


def _disabled() -> bool:
    raw = os.environ.get("PROJECTS_AUTO_COMMIT", "1").strip().lower()
    return raw in {"0", "false", "no", "off", ""}


def _python_bin() -> str:
    """Gateway process PATH often lacks `python3`; prefer the interpreter running Hermes."""
    for candidate in (
        sys.executable,
        os.environ.get("HERMES_PYTHON"),
        os.environ.get("HERMES_PY"),
        "/data/toolbox/bin/python3",
        "/var/lib/hermes/toolbox/bin/python3",
    ):
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    found = shutil.which("python3")
    return found or "python3"


def _run_auto_commit(source: str) -> None:
    if _disabled():
        return
    if not _SCRIPT.is_file():
        log.warning("projects-auto-commit: missing script %s", _SCRIPT)
        return

    env = os.environ.copy()
    env.setdefault("HERMES_HOME", str(_HOME))
    # Tag message origin lightly without forcing a fixed subject
    if not env.get("PROJECTS_AUTO_COMMIT_MSG"):
        env["PROJECTS_AUTO_COMMIT_SOURCE"] = source
    # Ensure child can resolve git/python even if gateway PATH is minimal.
    extra = os.environ.get("PROJECTS_AUTO_COMMIT_PATH", "").strip()
    path_bits = [p for p in extra.split(":") if p]
    path_bits += [
        str(_HOME.parent / "toolbox" / "bin"),
        "/data/toolbox/bin",
        "/run/current-system/sw/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
    ]
    cur = env.get("PATH", "")
    env["PATH"] = ":".join(path_bits + ([cur] if cur else []))

    py = _python_bin()
    try:
        proc = subprocess.Popen(
            [py, str(_SCRIPT)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            start_new_session=True,
        )
    except Exception:
        log.exception("projects-auto-commit: spawn failed (%s) py=%s", source, py)
        return

    def _finish() -> None:
        try:
            out, err = proc.communicate(timeout=_WAIT_S)
            if proc.returncode == 0:
                msg = (out or "").strip() or "ok"
                if msg not in {"clean", "skip: PROJECTS_AUTO_COMMIT disabled"}:
                    log.info("projects-auto-commit [%s]: %s", source, msg)
                else:
                    log.debug("projects-auto-commit [%s]: %s", source, msg)
            else:
                log.warning(
                    "projects-auto-commit [%s] rc=%s out=%s err=%s",
                    source,
                    proc.returncode,
                    (out or "").strip()[:400],
                    (err or "").strip()[:400],
                )
        except subprocess.TimeoutExpired:
            # Detach: push can finish in background; flock prevents overlap
            log.info(
                "projects-auto-commit [%s]: still running after %.0fs; detached",
                source,
                _WAIT_S,
            )
        except Exception:
            log.exception("projects-auto-commit: waiter failed (%s)", source)

    # Run waiter off the hook thread so we never stall turn finalization hard.
    threading.Thread(target=_finish, name="projects-auto-commit", daemon=True).start()


def _hook_post_llm_call(**kwargs) -> None:
    # Fired once per turn after the tool-calling loop completes (hermes hooks.py).
    if kwargs.get("error"):
        return
    _run_auto_commit("post_llm_call")


def _hook_on_session_end(**kwargs) -> None:
    reason = str(kwargs.get("turn_exit_reason") or "")
    # Still commit on most exits — dirty tree from a cancelled turn is real work.
    if reason in {"error"} and kwargs.get("error"):
        # Prefer not to commit half-broken tool storms; post_llm may have skipped too.
        return
    _run_auto_commit(f"on_session_end:{reason or 'unknown'}")


def register(ctx) -> None:
    ctx.register_hook("post_llm_call", _hook_post_llm_call)
    ctx.register_hook("on_session_end", _hook_on_session_end)
    log.info(
        "projects-auto-commit: registered (script=%s wait=%.0fs)",
        _SCRIPT,
        _WAIT_S,
    )
