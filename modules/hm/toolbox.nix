# Home Manager toolbox wiring.
{
  config,
  lib,
  ...
}:

let
  inherit (lib) mkIf;
  cfg = config.services.hermesPnP;
in
{
  config = mkIf (cfg.enable && cfg.toolbox.enable) {
    home.packages = [ cfg.toolbox.hermesToolbox ];
  };
}
