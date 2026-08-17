# Hermes PnP composer. Official agent/webui modules are imported from
# flake.nix (nixosModules.default) so this file stays a plain module.
{ config
, lib
, ...
}:
{
  imports = [
    ./options.nix
    ./package.nix
    ./agent.nix
    ./webui.nix
    ./models.nix
    ./plugins.nix
    ./skills.nix
    ./toolbox.nix
    ./browser.nix
    ./gbrain.nix
    ../../services/mcp-proxy/nix/module.nix
  ];

  config = lib.mkIf config.services.hermesPnP.mcpProxy.enable {
    services.mcpProxy.enable = true;
  };
}
