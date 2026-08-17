# Small extraPackages set. Opinion: the agent can do basic unix work.
# Official analog of the design's container.extraPackages is
# services.hermes-agent.extraPackages (no container.extraPackages option).
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib) mkIf;

  pnp = config.services.hermesPnP;
  agent = config.services.hermes-agent;

  defaultPackages = [
    pkgs.git
    pkgs.curl
    pkgs.jq
    pkgs.ripgrep
    pkgs.file
    pkgs.unzip
    pkgs.gnused
    pkgs.coreutils
    pkgs.findutils
  ];

  toolboxPackages = defaultPackages ++ pnp.toolbox.extraPackages;
in
{
  config = mkIf (pnp.enable && pnp.toolbox.enable) {
    services.hermes-agent.extraPackages = toolboxPackages;

    environment.systemPackages = mkIf (!agent.container.enable) toolboxPackages;
  };
}
