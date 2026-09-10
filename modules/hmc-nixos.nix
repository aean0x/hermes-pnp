# NixOS HMC state dir. Not imported by Home Manager.
{
  config,
  lib,
  ...
}:

let
  cfg = config.services.hermesPnP.hmc;
  agent = config.services.hermes-agent;
in
{
  config = lib.mkIf cfg.enable {
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
