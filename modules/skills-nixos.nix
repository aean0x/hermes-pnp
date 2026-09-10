# NixOS skill materialize. Not imported by Home Manager.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib) mkIf mapAttrsToList;
  cfg = config.services.hermesPnP;
  agent = config.services.hermes-agent;
  catalog = import ../skills/catalog.nix;
  allSkills = catalog // cfg.skills.extraSkills;
  skillsDir = "${agent.stateDir}/skills";
in
{
  config = mkIf (cfg.enable && cfg.skills.enable) {
    systemd.services.hermes-agent-skills = {
      description = "Materialize hermes-pnp first-party skills";
      wantedBy = [ "multi-user.target" ];
      before = [ "hermes-agent.service" ];

      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        User = agent.user;
        Group = agent.group;
      };

      path = [
        pkgs.coreutils
        pkgs.rsync
      ];

      script = ''
        set -euo pipefail
        install -d -m 2770 "${skillsDir}"
        ${lib.concatStringsSep "\n" (
          mapAttrsToList (name: src: ''
            install -d -m 2770 "${skillsDir}/${name}"
            rsync -a --delete "${src}/" "${skillsDir}/${name}/"
          '') allSkills
        )}
      '';
    };
  };
}
