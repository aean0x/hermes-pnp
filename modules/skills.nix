# First-party skills at $stateDir/skills/<name> (NixOS) or
# $hermesHome/pnp-skills/<name> (Home Manager).
# On with the composer (mkDefault). extraSkills merge beside the catalog.

{
  config,
  lib,
  pkgs,
  options,
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
  isHomeManager = options ? home && options.home ? homeDirectory;
  hasStateDir = options.services.hermes-agent ? stateDir;
  hasContainer = options.services.hermes-agent ? container;

  skillsDir =
    if hasStateDir then
      "${agent.stateDir}/skills"
    else
      "${agent.hermesHome}/pnp-skills";

  skillsExternalDir =
    if hasContainer && agent.container.enable then
      remapStatePath {
        inherit (agent) stateDir;
        path = skillsDir;
      }
    else
      skillsDir;

  installScript =
    let
      mode = if isHomeManager then "0750" else "2770";
    in
    ''
      set -euo pipefail
      install -d -m ${mode} "${skillsDir}"
      ${lib.concatStringsSep "\n" (
        mapAttrsToList (name: src: ''
          install -d -m ${mode} "${skillsDir}/${name}"
          ${pkgs.rsync}/bin/rsync -a --delete "${src}/" "${skillsDir}/${name}/"
        '') allSkills
      )}
    '';
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
      # One skills.external_dirs entry. deepConfigType: no mkDefault on leaves.
      services.hermes-agent.settings.skills.external_dirs = [ skillsExternalDir ];
    })
    (mkIf (cfg.skills.enable && !isHomeManager) {
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

        script = installScript;
      };
    })
    (mkIf (cfg.skills.enable && isHomeManager) {
      home.activation.hermesPnPSkills = config.lib.hm.dag.entryAfter [ "writeBoundary" ] ''
        $DRY_RUN_CMD bash -c ${lib.escapeShellArg installScript}
      '';
    })
  ]);
}
