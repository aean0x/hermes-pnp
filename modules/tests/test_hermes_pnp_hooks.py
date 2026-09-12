"""Vertex publisher prefix + Nix doctor skip."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from hermes_pnp_hooks import (
    _ensure_nixos_path,
    _wrap_doctor_config,
    _wrap_environments_local,
    _wrap_process_registry,
    nix_hermes_bin,
    vertex_model_id,
)


class VertexModelIdTests(unittest.TestCase):
    def test_prefixes_bare_gemini(self) -> None:
        self.assertEqual(
            vertex_model_id("gemini-3.8-flash", "vertex"),
            "google/gemini-3.8-flash",
        )

    def test_prefixes_flash_lite(self) -> None:
        self.assertEqual(
            vertex_model_id("gemini-3.5-flash-lite", "vertex"),
            "google/gemini-3.5-flash-lite",
        )

    def test_keeps_already_prefixed(self) -> None:
        self.assertEqual(
            vertex_model_id("google/gemini-3.8-flash", "vertex"),
            "google/gemini-3.8-flash",
        )

    def test_ignores_gemini_ai_studio(self) -> None:
        self.assertEqual(vertex_model_id("gemini-3.8-flash", "gemini"), "gemini-3.8-flash")

    def test_leaves_non_google_bare(self) -> None:
        self.assertEqual(vertex_model_id("claude-sonnet-4", "vertex"), "claude-sonnet-4")

    def test_alias_google_vertex(self) -> None:
        self.assertEqual(
            vertex_model_id("gemini-3.8-flash", "google-vertex"),
            "google/gemini-3.8-flash",
        )


class NixHermesBinTests(unittest.TestCase):
    def test_store_path_from_which(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch(
                "hermes_pnp_hooks.shutil.which",
                return_value="/nix/store/abc-hermes-agent/bin/hermes",
            ):
                self.assertEqual(
                    nix_hermes_bin(),
                    "/nix/store/abc-hermes-agent/bin/hermes",
                )

    def test_resolves_profile_symlink(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch(
                "hermes_pnp_hooks.shutil.which",
                return_value="/etc/profiles/per-user/aean/bin/hermes",
            ):
                with mock.patch(
                    "hermes_pnp_hooks.os.path.realpath",
                    return_value="/nix/store/abc-hermes-agent/bin/hermes",
                ):
                    self.assertEqual(
                        nix_hermes_bin(),
                        "/nix/store/abc-hermes-agent/bin/hermes",
                    )

    def test_managed_env(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"HERMES_MANAGED": "home-manager", "HERMES_BIN": ""},
            clear=False,
        ):
            with mock.patch("hermes_pnp_hooks.shutil.which", return_value=None):
                self.assertEqual(nix_hermes_bin(), "nix-managed")

    def test_unmanaged_none(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("hermes_pnp_hooks.shutil.which", return_value="/usr/bin/hermes"):
                with mock.patch("hermes_pnp_hooks.sys.argv", ["hermes"]):
                    self.assertIsNone(nix_hermes_bin())


class DoctorConfigTests(unittest.TestCase):
    def test_adds_vertex_to_vendor_slug_providers(self) -> None:
        class Mod:
            _VENDOR_SLUG_PROVIDERS = frozenset({"openrouter", "nous"})

        _wrap_doctor_config(Mod)
        self.assertIn("vertex", Mod._VENDOR_SLUG_PROVIDERS)
        self.assertIn("openrouter", Mod._VENDOR_SLUG_PROVIDERS)


class EnsureNixosPathTests(unittest.TestCase):
    def test_appends_existing_candidates(self) -> None:
        def fake_isdir(path: str) -> bool:
            return path in ("/run/current-system/sw/bin", "/etc/profiles/per-user/alice/bin")

        with mock.patch.dict(os.environ, {"PATH": "/nix/store/a-pkg/bin", "USER": "alice", "HOME": "/home/alice"}):
            with mock.patch("os.path.isdir", side_effect=fake_isdir):
                _ensure_nixos_path()
                path = os.environ["PATH"]
                self.assertIn("/run/current-system/sw/bin", path)
                self.assertIn("/etc/profiles/per-user/alice/bin", path)
                self.assertTrue(path.startswith("/nix/store/a-pkg/bin:"))

    def test_does_not_duplicate(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"PATH": "/run/current-system/sw/bin:/nix/store/a/bin", "USER": "bob", "HOME": "/home/bob"},
        ):
            with mock.patch("os.path.isdir", return_value=True):
                _ensure_nixos_path()
                parts = os.environ["PATH"].split(":")
                self.assertEqual(parts.count("/run/current-system/sw/bin"), 1)


class EnvironmentsLocalTests(unittest.TestCase):
    def test_appends_nix_paths_to_sane_path(self) -> None:
        class Mod:
            _SANE_PATH = "/usr/bin:/bin"

        _wrap_environments_local(Mod)
        self.assertIn("/run/current-system/sw/bin", Mod._SANE_PATH)
        self.assertIn("/usr/bin:/bin", Mod._SANE_PATH)


class ProcessRegistryTests(unittest.TestCase):
    def test_fallback_which_finds_systemd_run(self) -> None:
        class Mod:
            class shutil:
                @staticmethod
                def which(cmd: str, *args, **kwargs):
                    return None

        with mock.patch("os.path.isfile", return_value=True), mock.patch("os.access", return_value=True):
            _wrap_process_registry(Mod)
            self.assertEqual(
                Mod.shutil.which("systemd-run"),
                "/run/current-system/sw/bin/systemd-run",
            )
            self.assertEqual(
                Mod.shutil.which("systemctl"),
                "/run/current-system/sw/bin/systemctl",
            )
            self.assertIsNone(Mod.shutil.which("other-cmd"))
