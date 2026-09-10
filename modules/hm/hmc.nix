# Home Manager HMC state dir under $hermesHome.
{
  config,
  lib,
  ...
}:

let
  cfg = config.services.hermesPnP.hmc;
  hermesHome = config.services.hermes-agent.hermesHome;
in
{
  config = lib.mkIf cfg.enable {
    home.activation.hermesHmcState = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
      $DRY_RUN_CMD install -d -m 0700 ${lib.escapeShellArg "${hermesHome}/hmc_state"}
    '';
  };
}
