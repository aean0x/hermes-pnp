# Home Manager skill materialize under $hermesHome/pnp-skills.
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
  catalog = import ../../skills/catalog.nix;
  allSkills = catalog // cfg.skills.extraSkills;
  skillsDir = "${agent.hermesHome}/pnp-skills";
in
{
  config = mkIf (cfg.enable && cfg.skills.enable) {
    home.activation.hermesPnPSkills = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
      set -euo pipefail
      $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -d -m 0750 ${lib.escapeShellArg skillsDir}
      ${lib.concatStringsSep "\n" (
        mapAttrsToList (name: src: ''
          $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -d -m 0750 ${lib.escapeShellArg "${skillsDir}/${name}"}
          $DRY_RUN_CMD ${pkgs.rsync}/bin/rsync -a --delete ${src}/ ${lib.escapeShellArg "${skillsDir}/${name}"}/
        '') allSkills
      )}
    '';
  };
}
