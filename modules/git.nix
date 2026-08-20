# github.com HTTPS credential helper for Hermes git (git-hook, ad-hoc).
# Nix cannot see sops GITHUB_PAT at eval, so the helper is installed
# when the composer is on and no-ops at runtime if no token exists.
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
    mkOption
    types
    ;

  pnp = config.services.hermesPnP;
  cfg = pnp.git.credentialHelper;

  helper = pkgs.writeShellApplication {
    name = "git-credential-github-env";
    runtimeInputs = [
      pkgs.gnugrep
      pkgs.coreutils
    ];
    checkPhase = "";
    text = lib.removePrefix "#!/usr/bin/env bash\n" (
      builtins.readFile ../scripts/git-credential-github-env
    );
  };
in
{
  options.services.hermesPnP.git.credentialHelper.enable = mkOption {
    type = types.bool;
    default = false;
    description = ''
      Install git-credential-github-env on programs.git. Feeds
      GITHUB_PAT / GH_TOKEN / GITHUB_TOKEN for github.com HTTPS.
      No token in env or the usual Hermes env files → helper exits 0.
      Composer enable mkDefaults this on. Site user.name / user.email
      stay in the consumer.
    '';
  };

  config = lib.mkMerge [
    (mkIf pnp.enable {
      services.hermesPnP.git.credentialHelper.enable = mkDefault true;
    })

    (mkIf cfg.enable {
      programs.git.enable = mkDefault true;
      programs.git.config = {
        credential = {
          helper = "${helper}/bin/git-credential-github-env";
          useHttpPath = true;
        };
      };
    })

    (mkIf (cfg.enable && config.services.hermes-agent.container.enable) {
      services.hermes-agent.container.extraVolumes = [
        "/etc/gitconfig:/etc/gitconfig:ro"
      ];
    })
  ];
}
