# First-party skills shipped by hermes-pnp. Materialized into the
# agent skills dir ($stateDir/skills/<name>) — same destination Hermes
# already reads. No symlink layer.
#
# On when the composer is on (`mkDefault cfg.enable`). Extra consumer
# skills merge via skills.extraSkills.

{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib)
    mkDefault
    mkIf
    mapAttrsToList
    ;
  cfg = config.services.hermesPnP;
  agent = config.services.hermes-agent;
  catalog = import ../../skills/catalog.nix;
  allSkills = catalog // cfg.skills.extraSkills;
  skillsDir = "${agent.stateDir}/skills";
in
{
  config = mkIf cfg.enable {
    services.hermesPnP.skills.enable = mkDefault true;

    systemd.services.hermes-agent-skills = mkIf cfg.skills.enable {
      description = "Materialize hermes-pnp first-party skills";
      wantedBy = [ "multi-user.target" ];
      before = [ "hermes-agent.service" ];
      after = [ "hermes-agent-setup.service" ];

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
        install -d -m 0755 "${skillsDir}"
        ${lib.concatStringsSep "\n" (
          mapAttrsToList (name: src: ''
            install -d -m 0755 "${skillsDir}/${name}"
            rsync -a --delete "${src}/" "${skillsDir}/${name}/"
          '') allSkills
        )}
      '';
    };
  };
}
