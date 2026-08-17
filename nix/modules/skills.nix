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
    mkMerge
    mapAttrsToList
    ;
  cfg = config.services.hermesPnP;
  agent = config.services.hermes-agent;
  catalog = import ../../skills/catalog.nix;
  allSkills = catalog // cfg.skills.extraSkills;
  skillsDir = "${agent.stateDir}/skills";
in
{
  config = mkIf cfg.enable (mkMerge [
    {
      services.hermesPnP.skills.enable = mkDefault true;
    }
    (mkIf cfg.skills.enable {
    # Host CLI sees $stateDir/skills; the container bind is $stateDir → /data.
    # settings is deepConfigType — do not wrap leaves in mkIf/mkDefault.
    services.hermes-agent.settings.skills.external_dirs =
      [ skillsDir ] ++ lib.optional agent.container.enable "/data/skills";

    systemd.services.hermes-agent-skills = {
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
    })
  ]);
}
