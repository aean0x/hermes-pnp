# Eval-only composer checks. Dummy packages — do not realize official
# hermes-agent / hermes-webui builds.
{
  self,
  nixpkgs,
  system,
  pkgs,
}:

let
  inherit (nixpkgs) lib;

  dummyAgent = pkgs.runCommand "dummy-hermes-agent" {
    passthru.hermesVenv = pkgs.runCommand "dummy-hermes-venv" { } ''
      mkdir -p "$out/bin"
      touch "$out/bin/python3"
      chmod +x "$out/bin/python3"
    '';
  } ''
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

  dummyWebui = pkgs.runCommand "dummy-hermes-webui" { } ''
    mkdir -p "$out/bin"
    cat > "$out/bin/hermes-webui" <<'EOF'
    #!/bin/sh
    exit 0
    EOF
    chmod +x "$out/bin/hermes-webui"
  '';

  baseModules = [
    self.nixosModules.default
    {
      nixpkgs.hostPlatform = system;
      system.stateVersion = "25.11";
      documentation.enable = false;
      services.hermes-agent.package = dummyAgent;
      services.hermes-webui.package = dummyWebui;
      # Keep official package wrap unevaluated (see package.nix).
      services.hermesPnP.packageFixes.silenceMarkers = false;
    }
  ];

  evalSystem =
    extraModules:
    lib.nixosSystem {
      inherit system;
      modules = baseModules ++ extraModules;
    };

  eval = extraModules: (evalSystem extraModules).config;

  modulesConfig = eval [
    {
      services.hermes-agent.enable = true;
      services.hermes-agent.settings.model.default = "xai/grok-4";
      services.hermesPnP.enable = true;
    }
  ];

  dropInConfig = eval [
    {
      services.hermes-agent.enable = true;
      services.hermes-agent.settings.model.default = "xai/grok-4";
      services.hermesPnP.enable = false;
      services.hermesPnP.plugins.enable = [ "model-router" ];
    }
  ];

  gbrainConfig = eval [
    {
      services.hermes-agent.enable = true;
      services.hermesPnP.enable = true;
      services.hermesPnP.gbrain.enable = true;
    }
  ];

  optionsEval = evalSystem [ ];
in
{
  modules = pkgs.runCommand "hermes-pnp-modules-eval" { } ''
    test "${toString modulesConfig.services.hermesPnP.enable}" = "1"
    test "${toString modulesConfig.services.hermes-webui.enable}" = "1"
    test "${modulesConfig.services.hermes-webui.host}" = "127.0.0.1"
    test "${toString modulesConfig.services.hermes-webui.port}" = "8787"
    test "${modulesConfig.services.hermes-webui.user}" = "${modulesConfig.services.hermes-agent.user}"
    test "${modulesConfig.services.hermes-webui.group}" = "${modulesConfig.services.hermes-agent.group}"
    test "${modulesConfig.services.hermes-webui.hermesHome}" = "${modulesConfig.services.hermes-agent.stateDir}/.hermes"
    test "${toString (modulesConfig.services.hermes-webui.agent.package == modulesConfig.services.hermes-agent.package)}" = "1"
    test "${toString (modulesConfig.services.hermes-agent.extraDependencyGroups == [ ])}" = "1"
    test "${toString (modulesConfig.systemd.services ? gbrain-mcp-http)}" = ""
    test "${gbrainConfig.services.hermes-agent.mcpServers.gbrain.url}" = "http://127.0.0.1:3131/mcp"
    test "${toString (gbrainConfig.systemd.services ? gbrain-mcp-http)}" = ""
    touch "$out"
  '';

  drop-in = pkgs.runCommand "hermes-pnp-drop-in-eval" { } ''
    test "${toString dropInConfig.services.hermesPnP.enable}" = ""
    test "${toString dropInConfig.services.hermes-webui.enable}" = ""
    test "${toString (builtins.elem "model-router" dropInConfig.services.hermes-agent.settings.plugins.enabled)}" = "1"
    touch "$out"
  '';

  options = pkgs.runCommand "hermes-pnp-options-assert" { } ''
    test "${toString (optionsEval.options.services.hermesPnP.plugins.enable.isDefined or true)}" = "1"
    test "${toString (optionsEval.options.services.hermesPnP.webui.enable.isDefined or true)}" = "1"
    test "${toString (optionsEval.options.services.hermesPnP.toolbox.enable.isDefined or true)}" = "1"
    test "${optionsEval.options.services.hermesPnP.runtime.mode.default}" = "upstream"
    test "${toString optionsEval.options.services.hermesPnP.gbrain.enable.default}" = ""
    test "${toString optionsEval.options.services.hermesPnP.packageFixes.silenceMarkers.default}" = "1"
    touch "$out"
  '';
}
