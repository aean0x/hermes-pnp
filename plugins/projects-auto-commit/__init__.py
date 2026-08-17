"""Auto git sync for worktrees Hermes touches. Hooks only — no tools."""

from .sync import register

__all__ = ["register"]
