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
    mkEnableOption
    mkIf
    mkMerge
    mkOption
    mapAttrsToList
    types
    ;
  inherit (import ../lib { inherit lib; }) remapStatePath;

  cfg = config.services.hermesPnP;
  agent = config.services.hermes-agent;
  catalog = import ../skills/catalog.nix;
  allSkills = catalog // cfg.skills.extraSkills;
  skillsDir = "${agent.stateDir}/skills";
  skillsExternalDir =
    if agent.container.enable then
      remapStatePath {
        inherit (agent) stateDir;
        path = skillsDir;
      }
    else
      skillsDir;
in
{
  imports = [ ./enable.nix ];

  options.services.hermesPnP.skills = {
    enable = mkEnableOption "first-party hermes-pnp skills (browser, retrieval-reflex, gbrain-http-auth)";
    extraSkills = mkOption {
      type = types.attrsOf types.path;
      default = { };
      description = "Name → skill dir beside the catalog (consumer skills).";
    };
  };

  config = mkIf cfg.enable (mkMerge [
    {
      services.hermesPnP.skills.enable = mkDefault true;
    }
    (mkIf cfg.skills.enable {
    # Official skills.external_dirs. One path: host stateDir, or /data/skills
    # when the official jail remaps stateDir. Do not list both.
    # settings is deepConfigType — do not wrap leaves in mkIf/mkDefault.
    services.hermes-agent.settings.skills.external_dirs = [ skillsExternalDir ];

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
    })
  ]);
}
