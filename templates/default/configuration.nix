# Site identity stays here. PnP only pairs the agent.
{ config, pkgs, ... }:

{
  services.hermes-agent.enable = true;

  services.hermesPnP = {
    enable = true;
    # environmentFiles = [ config.sops.templates.hermesEnv.path ];
    # browser.package = pkgs.brave;
  };
}
