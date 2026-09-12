"""Nix-managed Hermes runtime hooks.

Loaded via a site .pth on the pnp PYTHONPATH overlay:

- Vertex OpenAPI wants ``<publisher>/<model>`` (``google/gemini-3.8-flash``).
  Hermes passes bare Gemini ids through for provider ``vertex``.
- ``hermes doctor`` treats the uv2nix sealed venv as a pip tree and tells
  you to ``pip install -e`` under site-packages. Skip that on Nix.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable
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


def _wrap_environments_local(mod: Any) -> None:
    if getattr(mod, "_pnp_env_local_patched", False):
        return
    current = getattr(mod, "_SANE_PATH", None)
    if isinstance(current, str):
        nix_paths = "/run/wrappers/bin:/run/current-system/sw/bin"
        if nix_paths not in current:
            mod._SANE_PATH = f"{current}:{nix_paths}"
    mod._pnp_env_local_patched = True


def _wrap_process_registry(mod: Any) -> None:
    if getattr(mod, "_pnp_process_registry_patched", False):
        return
    orig_which = shutil.which

    def _fallback_which(cmd: str, *args: Any, **kwargs: Any) -> str | None:
        found = orig_which(cmd, *args, **kwargs)
        if found:
            return found
        if cmd in ("systemd-run", "systemctl"):
            host_path = f"/run/current-system/sw/bin/{cmd}"
            if os.path.isfile(host_path) and os.access(host_path, os.X_OK):
                return host_path
        return None

    shutil.which = _fallback_which
    mod._pnp_process_registry_patched = True


def _ensure_nixos_path() -> None:
    """Ensure standard NixOS system binary directories are on PATH.

    Under systemd user services or minimal environments, PATH may contain
    only nix store paths, omitting systemd-run, systemctl, and host
    utilities in /run/current-system/sw/bin.
    """
    current = os.environ.get("PATH", "")
    parts = [p for p in current.split(":") if p]
    candidates = [
        "/run/wrappers/bin",
        "/run/current-system/sw/bin",
    ]
    user = os.environ.get("USER") or os.environ.get("LOGNAME")
    if user:
        candidates.append(f"/etc/profiles/per-user/{user}/bin")
    home = os.environ.get("HOME")
    if home:
        candidates.append(f"{home}/.nix-profile/bin")

    added = False
    for c in candidates:
        if os.path.isdir(c) and c not in parts:
            parts.append(c)
            added = True
    if added:
        os.environ["PATH"] = ":".join(parts)


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
    elif name == "hermes_cli.doctor":
        plat = sys.modules.get("hermes_cli.doctor_platform")
        if plat is not None:
            _wrap_doctor(plat)
        cfg = sys.modules.get("hermes_cli.doctor_config")
        if cfg is not None:
            _wrap_doctor_config(cfg)
    elif name == "tools.environments.local":
        mod = sys.modules.get(name)
        if mod is not None:
            _wrap_environments_local(mod)
    elif name == "tools.process_registry":
        mod = sys.modules.get(name)
        if mod is not None:
            _wrap_process_registry(mod)


def install() -> None:
    _ensure_nixos_path()

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
        if name == "hermes_cli" and fromlist:
            for leaf in fromlist:
                _maybe_patch(f"hermes_cli.{leaf}")
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
        "tools.environments.local",
        "tools.process_registry",
    ):
        if loaded in sys.modules:
            _maybe_patch(loaded)


def builtins_import() -> Callable[..., Any]:
    import builtins

    return builtins.__import__
