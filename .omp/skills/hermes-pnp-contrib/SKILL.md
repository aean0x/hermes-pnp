---
name: hermes-pnp-contrib
description: Commit, check, and lint the hermes-pnp library flake. Use before a commit, a history rewrite, nix flake check, or the PR lint gate.
---

# hermes-pnp contrib

Procedures for changing this library flake. Architecture and code rules stay in `AGENTS.md` and `docs/DESIGN.md`.

## Git identity

Commit only as GitHub user `aean0x`.

- `user.name` is `aean0x`.
- `user.email` is `3682177+aean0x@users.noreply.github.com`.
- Set both global and repo-local.
- Check `git config user.name` and `git config user.email` before the first commit of a session.
- Do not override `GIT_AUTHOR_*` or `GIT_COMMITTER_*`.
- Do not add an agent or bot `Co-authored-by` trailer.

After a history rewrite, re-pin every downstream `flake.lock` that follows this repo.

## Checks

`nix flake check` is the local suite.

- Composer eval uses dummy packages (`checks/eval.nix`, `checks/home-manager.nix`). Do not realize official Hermes builds inside checks.
- The same `nix flake check` runs plugin `unittest` (`checks/plugins.nix`) and mcp-proxy `unittest` (`checks/mcp-proxy.nix`).
- Catalog plugins that install at runtime (`git-hook`, `secret-handoff`, `gbrain-retrieval-reflex`) are tested in their own repos, not here.

## Lint gate

PR merge requires `.github/workflows/lint.yml` job `lint`. Local equivalents:

```bash
ruff check
npx --yes oxlint@1.65.0
nix run nixpkgs#statix -- check .
```

- Python: ruff C901 (`ruff.toml`, max-complexity 20).
- JS: oxlint `complexity` (`.oxlintrc.json`, max 20).
- Nix: statix (`statix.toml`). `inherit` and repeated-key lints are disabled. They are NixOS noise, not complexity.
- statix is not cyclomatic complexity. There is no McCabe gate for Nix. statix is the Nix antipattern gate.

## Add a plugin

Drop `plugins/<name>/` and add one line to `plugins/catalog.nix`. Do not install first-party trees through official `extraPlugins`. Upstream catalog plugins go on `catalogInstall` and are not materialized. Consumer trees go on `extraPluginDirs`.
