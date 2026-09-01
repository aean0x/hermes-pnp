# Optional hermes-context-manager. Upstream pin + generated config.yaml.
# Hermes compact stays on; HMC does per-tool work only.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib)
    mkEnableOption
    mkIf
    mkOption
    types
    ;

  pnp = config.services.hermesPnP;
  cfg = pnp.hmc;
  agent = config.services.hermes-agent;
  src = cfg.src;

  hmcSrc = pkgs.fetchFromGitHub {
    inherit (src)
      owner
      repo
      rev
      hash
      ;
  };

  percent = toString cfg.compressPercent;

  hmcConfig = pkgs.writeText "hermes-context-manager-config.yaml" ''
    # Managed by hermes-pnp (modules/hmc.nix). Pin ${src.rev}.
    enabled: true
    debug: false

    manual_mode:
      enabled: false
      automatic_strategies: true

    compress:
      max_context_percent: ${percent}
      min_context_percent: ${percent}
      protected_tools:
        - write_file
        - patch

    # Dedup + error purge rewrite OLD tool messages in place every turn,
    # busting the prompt-cache prefix. On DeepSeek (cache-hit input ~30x
    # cheaper than miss) each rewrite re-bills the whole suffix at miss
    # rate, costing more than the removed bytes save. Keep both off;
    # native Hermes compaction covers the over-budget case and
    # short_circuits/truncation/code_filter still trim new tool output.
    # See tools/cache-bust/ for the prefix-stability harness.
    strategies:
      deduplication:
        enabled: false
        protected_tools: []
      purge_errors:
        enabled: false
        turns: 4
        protected_tools: []

    short_circuits:
      enabled: true

    truncation:
      enabled: true
      max_lines: 30
      head_lines: 10
      tail_lines: 6
      min_content_length: 500

    # Hermes compact stays on.
    background_compression:
      enabled: false
      protect_recent_turns: 3

    analytics:
      enabled: true
      retention_days: 90
      db_path: ""

    code_filter:
      enabled: true
      languages:
        - python
        - javascript
        - typescript
        - rust
        - go
      min_lines: 30
      preserve_docstrings: true
  '';

  hmcPluginSrc = pkgs.runCommand "hermes-context-manager" { } ''
    mkdir -p "$out"
    cp -a ${hmcSrc}/. "$out/"
    chmod -R u+w "$out"
    rm -rf "$out/.github" "$out/tests" "$out/.gitignore"
    cp ${hmcConfig} "$out/config.yaml"
  '';
in
{
  options.services.hermesPnP.hmc = {
    enable = mkEnableOption ''
      Pin hermes-context-manager as extraPluginDirs.hermes-context-manager
      and create $stateDir/.hermes/hmc_state. Native compact stays on;
      HMC does cheap per-tool work only.
    '';

    compressPercent = mkOption {
      type = types.float;
      default = 0.30;
      description = ''
        HMC compress.max/min_context_percent of the probed window.
        Unused while background_compression is off.
      '';
    };

    src = {
      owner = mkOption {
        type = types.str;
        default = "entrepeneur4lyf";
      };
      repo = mkOption {
        type = types.str;
        default = "hermes-context-manager";
      };
      rev = mkOption {
        type = types.str;
        default = "3f775efd48e878679e8fd4290b96968880fed6f7";
      };
      hash = mkOption {
        type = types.str;
        default = "sha256-aQMKhWN9KVfpgbIbcvlGgTZHZ4xC/ATgJkz8btofM7Y=";
      };
    };
  };

  config = mkIf cfg.enable {
    services.hermesPnP.extraPluginDirs.hermes-context-manager = hmcPluginSrc;

    system.activationScripts.hermes-hmc-state =
      lib.stringAfter
        [
          "users"
          "groups"
          "hermes-agent-setup"
        ]
        ''
          install -d -m 2770 -o ${agent.user} -g ${agent.group} ${agent.stateDir}/.hermes/hmc_state
        '';
  };
}
