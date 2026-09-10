# First-party skills. Materialize lives in skills-nixos.nix (NixOS) and
# hm/skills.nix (Home Manager). This file is shared: options + the
# skills.external_dirs pointer.
{
  config,
  lib,
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
    types
    ;
  inherit (import ../lib { inherit lib; }) remapStatePath;

  cfg = config.services.hermesPnP;
  agent = config.services.hermes-agent;
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
  ]);
}
