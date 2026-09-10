# Home Manager eval. Dummy packages; do not realize official builds.
{
  self,
  nixpkgs,
  system,
  pkgs,
  hermes-agent,
}:

let
  inherit (nixpkgs) lib;

  home-manager = hermes-agent.inputs.home-manager;

  dummyDesktop = lib.makeOverridable (
    {
      extraEnv ? { },
      extraRun ? [ ],
    }:
    pkgs.runCommand "dummy-hermes-desktop" { pname = "hermes-desktop"; } ''
      mkdir -p "$out/bin"
      printf '%s\n' ${lib.escapeShellArg (builtins.toJSON extraEnv)} > "$out/extraEnv"
      printf '%s\n' ${lib.escapeShellArg (lib.concatStrings extraRun)} > "$out/extraRun"
      touch "$out/bin/hermes-desktop"
      chmod +x "$out/bin/hermes-desktop"
    ''
  ) { };

  dummyAgent =
    pkgs.runCommand "dummy-hermes-agent"
      {
        passthru = {
          hermesVenv = pkgs.runCommand "dummy-hermes-venv" { } ''
            mkdir -p "$out/bin"
            touch "$out/bin/python3"
            chmod +x "$out/bin/python3"
          '';
          hermesDesktop = dummyDesktop;
        };
      }
      ''
        mkdir -p "$out/bin" \
          "$out/share/hermes-agent/plugins" \
          "$out/share/hermes-agent/skills" \
          "$out/share/hermes-agent/optional-skills" \
          "$out/share/hermes-agent/locales" \
          "$out/share/hermes-agent/optional-mcps" \
          "$out/share/hermes-agent/web_dist" \
          "$out/ui-tui"
        cat > "$out/bin/hermes" <<'EOF'
        #!/bin/sh
        exit 0
        EOF
        chmod +x "$out/bin/hermes"
      '';

  evalHome =
    extraModules:
    home-manager.lib.homeManagerConfiguration {
      inherit pkgs;
      modules = [
        self.homeManagerModules.default
        {
          home = {
            username = "aean";
            homeDirectory = "/home/aean";
            stateVersion = "25.11";
          };
          services.hermes-agent.package = dummyAgent;
          programs.hermes-agent.package = dummyAgent;
          programs.hermes-agent.desktop.package = dummyDesktop;
          services.hermesPnP.packageFixes.silenceMarkers = false;
          services.hermesPnP.packageFixes.missingPyModules = false;
        }
      ]
      ++ extraModules;
    };

  hmEval = evalHome [ ../examples/home-manager.nix ];
  desktopCfg = hmEval.config;
in
{
  home-manager = pkgs.runCommand "hermes-pnp-home-manager-eval" { } ''
    test "${desktopCfg.home.username}" = "aean"
    test "${desktopCfg.services.hermes-agent.hermesHome}" = "/home/aean/.hermes"
    test "${desktopCfg.services.hermes-agent.workingDirectory}" = "/home/aean"
    test "${toString desktopCfg.programs.hermes-agent.enable}" = "1"
    test "${toString desktopCfg.programs.hermes-agent.desktop.enable}" = "1"
    test "${toString desktopCfg.services.hermes-agent.enable}" = "1"
    test "${desktopCfg.services.hermes-agent.backend.mode}" = "serve"
    test "${desktopCfg.services.hermes-agent.backend.host}" = "127.0.0.1"
    test "${toString desktopCfg.services.hermes-agent.backend.port}" = "9119"
    test "${toString (desktopCfg.systemd.user.services ? hermes-backend)}" = "1"
    test "${toString (desktopCfg.systemd.user.services ? hermes-agent)}" = ""
    test "${toString (builtins.elem "model-router" desktopCfg.services.hermes-agent.settings.plugins.enabled)}" = "1"
    test "${toString (builtins.elem "tool-call-coherency" desktopCfg.services.hermes-agent.settings.plugins.enabled)}" = "1"
    test "${lib.concatStringsSep "," desktopCfg.services.hermes-agent.settings.skills.external_dirs}" = "/home/aean/.hermes/pnp-skills"
    test "${toString (desktopCfg.home.activation ? hermesPnPPlugins)}" = "1"
    test "${toString (desktopCfg.home.activation ? hermesPnPSkills)}" = "1"
    test "${toString (hmEval.options.services.hermes-agent ? user)}" = ""
    touch "$out"
  '';
}
