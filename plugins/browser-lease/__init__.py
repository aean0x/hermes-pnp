"""browser-lease — one session drives the shared browser at a time."""

from __future__ import annotations

from .lease import register

__all__ = ["register"]
