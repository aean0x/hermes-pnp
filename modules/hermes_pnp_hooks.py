"""Nix-managed Hermes runtime hooks.

Loaded via a site .pth on the pnp PYTHONPATH overlay:

- Vertex OpenAPI wants ``<publisher>/<model>`` (``google/gemini-3.8-flash``).
  Hermes passes bare Gemini ids through for provider ``vertex``.
- ``hermes doctor`` treats the uv2nix sealed venv as a pip tree and tells
  you to ``pip install -e`` under site-packages. Skip that on Nix.
- ``cron.scheduler_prompt`` injects the newest archived output of a
  ``context_from`` job. Label each injected archive with its own run date and
  bound failure documents by age; without that a run that failed once is
  quoted as "the most recent output" for as long as every later run is silent.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable
from datetime import datetime
from typing import Any

_VERTEX_ALIASES = frozenset(
    {
        "vertex",
        "google-vertex",
        "vertex-ai",
        "gcp-vertex",
        "vertexai",
    }
)
_GOOGLE_PUBLISHER_HEADS = frozenset({"gemini", "gemma"})


def vertex_model_id(model_input: str, target_provider: str) -> str:
    """Prefix ``google/`` onto bare Gemini/Gemma ids for Vertex OpenAPI."""
    name = (model_input or "").strip()
    provider = (target_provider or "").strip().lower().replace("_", "-")
    if provider not in _VERTEX_ALIASES or not name or "/" in name:
        return name
    head = name.split("-", 1)[0].lower()
    if head in _GOOGLE_PUBLISHER_HEADS:
        return f"google/{name}"
    return name


def nix_hermes_bin() -> str | None:
    """Store path or managed-install marker when Hermes is Nix-wrapped."""
    managed = (os.environ.get("HERMES_MANAGED") or "").strip()
    raw = [
        os.environ.get("HERMES_BIN"),
        shutil.which("hermes"),
        sys.argv[0] if sys.argv else None,
    ]
    for cand in raw:
        if not cand:
            continue
        paths = [cand]
        which = shutil.which(cand)
        if which:
            paths.append(which)
        for path in paths:
            if path and "/nix/store/" in path:
                return path
            try:
                resolved = os.path.realpath(path)
            except OSError:
                continue
            if "/nix/store/" in resolved:
                return resolved
    if managed:
        return os.environ.get("HERMES_BIN") or shutil.which("hermes") or "nix-managed"
    return None


def _wrap_normalize(mod: Any) -> None:
    if getattr(mod, "_pnp_vertex_patched", False):
        return
    real = getattr(mod, "normalize_model_for_provider", None)
    if not callable(real):
        return

    def wrapped(model_input: str, target_provider: str) -> str:
        return vertex_model_id(real(model_input, target_provider), target_provider)

    mod.normalize_model_for_provider = wrapped
    mod._pnp_vertex_patched = True


def _wrap_doctor(mod: Any) -> None:
    if getattr(mod, "_pnp_doctor_patched", False):
        return
    orig = getattr(mod, "_check_command_installation", None)
    if not callable(orig):
        return

    def patched(should_fix: bool = False, *args: Any, **kwargs: Any) -> Any:
        hermes_bin = nix_hermes_bin()
        if hermes_bin is None:
            return orig(should_fix, *args, **kwargs)
        section = getattr(mod, "_section", None)
        check_ok = getattr(mod, "check_ok", None)
        if callable(section):
            section("Command Installation")
        if callable(check_ok):
            check_ok(f"Nix-wrapped hermes ({hermes_bin})")
        from hermes_cli.doctor_report import Finding

        return Finding()

    mod._check_command_installation = patched
    mod._pnp_doctor_patched = True
    doc = sys.modules.get("hermes_cli.doctor")
    if doc is not None and hasattr(doc, "DOCTOR_CHECKS"):
        checks = getattr(doc, "DOCTOR_CHECKS")
        doc.DOCTOR_CHECKS = tuple(patched if item is orig else item for item in checks)


def _wrap_doctor_config(mod: Any) -> None:
    """Vertex OpenAPI wants google/<model>; doctor treats slashes as aggregator-only."""
    current = getattr(mod, "_VENDOR_SLUG_PROVIDERS", None)
    if current is None:
        return
    extra = frozenset({"vertex", "google-vertex", "vertex-ai", "gcp-vertex", "vertexai"})
    if extra <= frozenset(current):
        return
    mod._VENDOR_SLUG_PROVIDERS = frozenset(current) | extra


# --- cron context_from: date every injected archive, age-bound failures -----------------
#
# ``_inject_context_from`` walks a source job's archives newest-first and takes the first
# one ``_archive_answer`` accepts. A FAILED run document carries no ``## Response``
# heading, so the extractor returns the WHOLE document - the failed run's assembled prompt
# and its traceback - and the walk lands on it weeks later, as soon as the newer runs are
# all silent. That is ~9 KB of dead prompt per fire plus a false failure signal.
# Kept to two bounded changes, so upstream's walk still chooses the archive:
#   * a failure document is usable only while fresh (default 24 h); older ones fall through.
#   * every injected archive carries its own run date in the block's first line.
# Retune the bound without a rebuild via ``HERMES_PNP_CONTEXT_FROM_FAILURE_MAX_AGE_SECONDS``
# (0 disables failure injection). Drop this hook once the pinned hermes-agent dates and
# bounds these archives itself.

_CONTEXT_FROM_FAILURE_MAX_AGE_ENV = "HERMES_PNP_CONTEXT_FROM_FAILURE_MAX_AGE_SECONDS"
_CONTEXT_FROM_FAILURE_MAX_AGE_DEFAULT_S = 24 * 60 * 60
_ARCHIVE_RUN_TIME_MARK = "**Run Time:**"
_ARCHIVE_SILENT_STATUS = ("silent", "no_change")
_ARCHIVE_FAILED_TITLE = " (FAILED)"

# Replaces upstream's bare "the most recent output": the block states when that output ran.
_SELF_CONTEXT_INTRO = (
    "The following is this job's most recent usable output from a previous run; the block's "
    "first line carries that run's date. Use it for continuity: avoid repeating what was "
    "already reported, and continue where the last run left off."
)
_UPSTREAM_CONTEXT_INTRO = (
    "The following is the most recent usable output from a preceding cron job; the block's "
    "first line carries that run's date. Use it as context for your analysis."
)


def _hermes_now() -> datetime:
    """The configured-timezone clock, or the host clock when hermes_time is unimportable."""
    try:
        from hermes_time import now as _now

        return _now()
    except Exception:
        return datetime.now().astimezone()


def _archive_header(archive: str) -> str:
    """The run header of a stored archive - the same slice the injector reads."""
    return archive.split("\n---\n", 1)[0].split("\n## Prompt", 1)[0]


def _archive_failure(archive: str) -> bool:
    """True for a run document that records a failure rather than a report."""
    first_line = archive.lstrip().split("\n", 1)[0].strip()
    if first_line.startswith("# Cron Job:") and first_line.endswith(_ARCHIVE_FAILED_TITLE):
        return True
    for line in _archive_header(archive).splitlines():
        if line.startswith("**Status:**"):
            status = line[len("**Status:**") :].strip().lower()
            if status and not status.startswith(_ARCHIVE_SILENT_STATUS):
                return True
    return False


def _archive_run_time(archive: str) -> datetime | None:
    """The run's local timestamp from the archive header, or None when absent/unparsable."""
    for line in _archive_header(archive).splitlines():
        if not line.startswith(_ARCHIVE_RUN_TIME_MARK):
            continue
        raw = line[len(_ARCHIVE_RUN_TIME_MARK) :].strip()
        for length, fmt in ((19, "%Y-%m-%d %H:%M:%S"), (16, "%Y-%m-%d %H:%M")):
            try:
                return datetime.strptime(raw[:length], fmt)
            except ValueError:
                continue
        return None
    return None


def _archive_age_seconds(run_at: datetime) -> float:
    """Age of an archive on the header's own (naive, configured-tz) clock."""
    now = _hermes_now()
    if run_at.tzinfo is None and now.tzinfo is not None:
        run_at = run_at.replace(tzinfo=now.tzinfo)
    return (now - run_at).total_seconds()


def _failure_max_age_seconds() -> float:
    """Operator bound for failure documents; anything unparsable falls back to the default."""
    raw = (os.environ.get(_CONTEXT_FROM_FAILURE_MAX_AGE_ENV) or "").strip()
    try:
        value = float(raw)
    except ValueError:
        return float(_CONTEXT_FROM_FAILURE_MAX_AGE_DEFAULT_S)
    return value if value >= 0 else float(_CONTEXT_FROM_FAILURE_MAX_AGE_DEFAULT_S)


def _format_age(seconds: float) -> str:
    if seconds < 5400:
        return f"{max(0, int(seconds // 60))}m"
    if seconds < 172800:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def _archive_label(run_at: datetime, age_seconds: float, failure: bool) -> str:
    stamp = run_at.strftime("%Y-%m-%d %H:%M")
    if failure:
        return (
            f"[cron context_from: FAILED run of {stamp} ({_format_age(age_seconds)} ago); "
            "the text below is that run's own prompt and error text, not a report]"
        )
    return f"[cron context_from: run of {stamp} ({_format_age(age_seconds)} ago)]"


def _wrap_context_from(mod: Any) -> None:
    """Wrap ``_archive_answer`` so the injector dates its archives and skips stale failures."""
    if getattr(mod, "_pnp_context_from_patched", False):
        return
    orig = getattr(mod, "_archive_answer", None)
    if not callable(orig):
        return

    def archive_answer(archive: str) -> Any:
        answer = orig(archive)
        if answer is None:
            return None
        failure = _archive_failure(archive)
        run_at = _archive_run_time(archive)
        if run_at is None:
            # An error document with no run date cannot be dated or bounded: never an answer.
            return None if failure else answer
        age = _archive_age_seconds(run_at)
        if failure and age > _failure_max_age_seconds():
            return None  # stale failure - the walk continues to an older usable archive
        return f"{_archive_label(run_at, age, failure)}\n{answer}"

    mod._archive_answer = archive_answer
    mod._UPSTREAM_CONTEXT_INTRO = _UPSTREAM_CONTEXT_INTRO
    mod._SELF_CONTEXT_INTRO = _SELF_CONTEXT_INTRO
    mod._pnp_context_from_patched = True


def _maybe_patch(name: str) -> None:
    if name == "hermes_cli.model_normalize":
        mod = sys.modules.get(name)
        if mod is not None:
            _wrap_normalize(mod)
    elif name == "hermes_cli.doctor_platform":
        mod = sys.modules.get(name)
        if mod is not None:
            _wrap_doctor(mod)
    elif name == "hermes_cli.doctor_config":
        mod = sys.modules.get(name)
        if mod is not None:
            _wrap_doctor_config(mod)
    elif name == "cron.scheduler_prompt":
        mod = sys.modules.get(name)
        if mod is not None:
            _wrap_context_from(mod)
    elif name == "hermes_cli.doctor":
        plat = sys.modules.get("hermes_cli.doctor_platform")
        if plat is not None:
            _wrap_doctor(plat)
        cfg = sys.modules.get("hermes_cli.doctor_config")
        if cfg is not None:
            _wrap_doctor_config(cfg)


def install() -> None:
    orig_import: Callable[..., Any] = builtins_import()

    def _import(
        name: str,
        globals: Any = None,
        locals: Any = None,
        fromlist: Any = (),
        level: int = 0,
    ) -> Any:
        mod = orig_import(name, globals, locals, fromlist, level)
        _maybe_patch(name)
        for package in ("hermes_cli", "cron"):
            if name == package and fromlist:
                for leaf in fromlist:
                    _maybe_patch(f"{package}.{leaf}")
        return mod

    import builtins

    if getattr(builtins.__import__, "_pnp_hooks", False):
        return
    _import._pnp_hooks = True  # type: ignore[attr-defined]
    builtins.__import__ = _import
    for loaded in (
        "hermes_cli.model_normalize",
        "hermes_cli.doctor_platform",
        "hermes_cli.doctor_config",
        "hermes_cli.doctor",
        "cron.scheduler_prompt",
    ):
        if loaded in sys.modules:
            _maybe_patch(loaded)


def builtins_import() -> Callable[..., Any]:
    import builtins

    return builtins.__import__
