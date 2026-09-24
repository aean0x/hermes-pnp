"""Vertex publisher prefix, Nix doctor skip, and cron context_from archive hygiene."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

from hermes_pnp_hooks import (
    _archive_failure,
    _archive_run_time,
    _maybe_patch,
    _wrap_context_from,
    _wrap_doctor_config,
    nix_hermes_bin,
    vertex_model_id,
)

_FAILURE_MAX_AGE_ENV = "HERMES_PNP_CONTEXT_FROM_FAILURE_MAX_AGE_SECONDS"


def _run_doc(title: str, run_time: str, body: str = "## Prompt\n\nassembled prompt\n") -> str:
    """A stored cron run document, in the shape the injector reads."""
    return (
        f"# Cron Job: {title}\n\n"
        "**Job ID:** b240ff09bd0b\n"
        f"**Run Time:** {run_time}\n"
        "**Schedule:** 0 6,19 * * *\n\n"
        f"{body}"
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


class ContextFromArchiveTests(unittest.TestCase):
    """The injector's archive walk: failure documents bounded, archives dated."""

    NOW = datetime(2026, 9, 24, 20, 52, tzinfo=timezone.utc)

    def setUp(self) -> None:
        patcher = mock.patch("hermes_pnp_hooks._hermes_now", return_value=self.NOW)
        patcher.start()
        self.addCleanup(patcher.stop)
        previous = os.environ.pop(_FAILURE_MAX_AGE_ENV, None)
        if previous is not None:
            self.addCleanup(os.environ.__setitem__, _FAILURE_MAX_AGE_ENV, previous)

    def _wrapped(self, answer):
        mod = SimpleNamespace(_archive_answer=lambda archive: answer)
        _wrap_context_from(mod)
        return mod

    def test_failed_title_is_a_failure(self) -> None:
        self.assertTrue(_archive_failure(_run_doc("inbox-triage (FAILED)", "2026-09-12 19:01:10")))

    def test_error_status_is_a_failure(self) -> None:
        doc = _run_doc("inbox-triage", "2026-09-12 19:01:10", "**Status:** script failed\n")
        self.assertTrue(_archive_failure(doc))

    def test_silent_status_is_not_a_failure(self) -> None:
        doc = _run_doc("inbox-triage", "2026-09-12 19:01:10", "**Status:** silent (empty output)\n")
        self.assertFalse(_archive_failure(doc))

    def test_plain_run_is_not_a_failure(self) -> None:
        doc = _run_doc("inbox-triage", "2026-09-12 19:01:10", "## Response\n\nreal answer\n")
        self.assertFalse(_archive_failure(doc))

    def test_run_time_read_from_header(self) -> None:
        doc = _run_doc("inbox-triage", "2026-09-12 19:01:10")
        self.assertEqual(_archive_run_time(doc), datetime(2026, 9, 12, 19, 1, 10))
        self.assertIsNone(_archive_run_time(_run_doc("inbox-triage", "unknown")))

    def test_stale_failure_is_dropped(self) -> None:
        doc = _run_doc("inbox-triage (FAILED)", "2026-09-12 19:01:10", "## Error\n\nboom\n")
        self.assertIsNone(self._wrapped(doc)._archive_answer(doc))

    def test_fresh_failure_is_injected_with_a_failure_label(self) -> None:
        doc = _run_doc("inbox-triage (FAILED)", "2026-09-24 18:40:10", "## Error\n\nboom\n")
        injected = self._wrapped(doc)._archive_answer(doc)
        self.assertTrue(injected.startswith("[cron context_from: FAILED run of 2026-09-24 18:40"))
        self.assertIn("2h ago", injected)
        self.assertIn("not a report", injected)
        self.assertIn("## Error\n\nboom", injected)

    def test_old_success_is_injected_with_its_run_date(self) -> None:
        doc = _run_doc("inbox-triage", "2026-09-05 06:03:51", "## Response\n\nreal answer\n")
        injected = self._wrapped("real answer")._archive_answer(doc)
        self.assertEqual(
            injected,
            "[cron context_from: run of 2026-09-05 06:03 (19d ago)]\nreal answer",
        )

    def test_silent_answer_still_falls_through(self) -> None:
        doc = _run_doc("inbox-triage", "2026-09-24 19:01:53", "## Response\n\n[SILENT]\n")
        self.assertIsNone(self._wrapped(None)._archive_answer(doc))

    def test_double_wrap_labels_once(self) -> None:
        doc = _run_doc("inbox-triage", "2026-09-24 19:01:53", "## Response\n\nreal answer\n")
        mod = self._wrapped("real answer")
        _wrap_context_from(mod)
        injected = mod._archive_answer(doc)
        self.assertEqual(injected.count("[cron context_from:"), 1)

    def test_env_bound_disables_failure_injection(self) -> None:
        doc = _run_doc("inbox-triage (FAILED)", "2026-09-24 20:51:53", "## Error\n\nboom\n")
        with mock.patch.dict(os.environ, {_FAILURE_MAX_AGE_ENV: "0"}):
            self.assertIsNone(self._wrapped(doc)._archive_answer(doc))

    def test_intros_point_at_the_date_label(self) -> None:
        mod = self._wrapped("real answer")
        self.assertIn("run's date", mod._UPSTREAM_CONTEXT_INTRO)
        self.assertIn("run's date", mod._SELF_CONTEXT_INTRO)

    def test_maybe_patch_rewrites_a_loaded_module(self) -> None:
        doc = _run_doc("inbox-triage (FAILED)", "2026-09-12 19:01:10", "## Error\n\nboom\n")
        mod = SimpleNamespace(_archive_answer=lambda archive: "kept")
        with mock.patch.dict(sys.modules, {"cron.scheduler_prompt": mod}):
            _maybe_patch("cron.scheduler_prompt")
        self.assertTrue(getattr(mod, "_pnp_context_from_patched", False))
        self.assertIsNone(mod._archive_answer(doc))

