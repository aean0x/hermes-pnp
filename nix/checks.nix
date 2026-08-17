# Eval-only composer checks. Dummy packages — do not realize official
# hermes-agent / hermes-webui builds.
{ self
, nixpkgs
, system
, pkgs
,
}:

let
  inherit (nixpkgs) lib;

  dummyAgent = pkgs.runCommand "dummy-hermes-agent"
    {
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
      services.hermesPnP.enable = true;
    }
  ];

  dropInConfig = eval [
    {
      services.hermes-agent.enable = true;
      services.hermes-agent.settings.model.default = "xai/grok-4";
      services.hermesPnP.enable = false;
      services.hermesPnP.plugins = [ "model-router" ];
    }
  ];

  gbrainConfig = eval [
    {
      services.hermes-agent.enable = true;
      services.hermesPnP.enable = true;
      services.hermesPnP.gbrain.enable = true;
    }
  ];

  containerConfig = eval [
    {
      services.hermes-agent.enable = true;
      services.hermesPnP.enable = true;
      services.hermesPnP.container.enable = true;
    }
  ];

  optionsEval = evalSystem [ ];

  dropAux = (dropInConfig.services.hermes-agent.settings.auxiliary or { }).triage_specifier or { };

  # plugins is listOf, not a submodule — these children must not exist.
  pluginsOpt = optionsEval.options.services.hermesPnP.plugins;
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
    test "${toString (gbrainConfig.systemd.services ? gbrain-mcp-http)}" = "1"
    test "${gbrainConfig.systemd.services.gbrain-mcp-http.serviceConfig.User}" = "${gbrainConfig.services.hermes-agent.user}"
    test "${toString (modulesConfig.services.hermes-webui.extraEnvironment ? HERMES_WEBUI_TRUST_FORWARDED_PROTO)}" = "1"
    test "${toString (modulesConfig.services.hermes-webui.extraEnvironment ? HERMES_WEBUI_EXTENSION_DIR)}" = "1"
    test "${toString (builtins.elem "model-router" modulesConfig.services.hermesPnP.plugins)}" = "1"
    test "${toString (builtins.elem "model-router" gbrainConfig.services.hermesPnP.plugins)}" = "1"
    test "${toString (builtins.elem "gbrain-retrieval-reflex" gbrainConfig.services.hermes-agent.settings.plugins.enabled)}" = "1"
    test "${toString (builtins.elem "gbrain-memory-flush" gbrainConfig.services.hermes-agent.settings.plugins.enabled)}" = "1"
    test "${toString (builtins.elem "model-router" gbrainConfig.services.hermes-agent.settings.plugins.enabled)}" = "1"
    test "${modulesConfig.services.hermes-agent.settings.model.default}" = "${modulesConfig.services.hermesPnP.models.high.model}"
    test "${modulesConfig.services.hermes-agent.settings.model.provider}" = "${modulesConfig.services.hermesPnP.models.high.provider}"
    test "${modulesConfig.services.hermes-agent.settings.auxiliary.triage_specifier.model}" = "${modulesConfig.services.hermesPnP.models.low.model}"
    test "${modulesConfig.services.hermes-agent.settings.auxiliary.background_review.model}" = "${modulesConfig.services.hermesPnP.models.medium.model}"
    test "${modulesConfig.services.hermes-agent.settings.auxiliary.curator.model}" = "${modulesConfig.services.hermesPnP.models.medium.model}"
    test "${modulesConfig.services.hermes-agent.settings.delegation.model}" = "${modulesConfig.services.hermesPnP.models.medium.model}"
    test "${modulesConfig.services.hermes-agent.settings.cron.model}" = "${modulesConfig.services.hermesPnP.models.low.model}"
    test "${modulesConfig.services.hermes-agent.settings.browser.cdp_url}" = "http://127.0.0.1:9222"
    test "${toString (modulesConfig.systemd.services ? hermes-browser)}" = "1"
    test "${toString (modulesConfig.services.hermesPnP.toolbox.hostPath != "")}" = "1"
    test "${toString (builtins.match ".*toolbox/bin.*" modulesConfig.services.hermesPnP.toolbox.hostPath != null)}" = "1"
    test "${toString modulesConfig.services.hermes-agent.container.enable}" = ""
    test "${toString containerConfig.services.hermes-agent.container.enable}" = "1"
    test "${containerConfig.services.hermes-agent.container.image}" = "ubuntu:24.04"
    touch "$out"
  '';

  drop-in = pkgs.runCommand "hermes-pnp-drop-in-eval" { } ''
    test "${toString dropInConfig.services.hermesPnP.enable}" = ""
    test "${toString dropInConfig.services.hermes-webui.enable}" = ""
    test "${toString (builtins.elem "model-router" dropInConfig.services.hermes-agent.settings.plugins.enabled)}" = "1"
    test "${dropInConfig.services.hermes-agent.settings.model.default}" = "xai/grok-4"
    test "${dropAux.model or ""}" = ""
    touch "$out"
  '';

  options = pkgs.runCommand "hermes-pnp-options-assert" { } ''
    test "${pluginsOpt.type.name}" = "listOf"
    test "${toString (pluginsOpt ? enable)}" = ""
    test "${toString (pluginsOpt ? modelRouter)}" = ""
    test "${toString (optionsEval.options.services.hermesPnP ? models)}" = "1"
    test "${optionsEval.config.services.hermesPnP.models.low.model}" = "deepseek-v4-flash"
    test "${optionsEval.config.services.hermesPnP.models.medium.model}" = "deepseek-v4-pro"
    test "${optionsEval.config.services.hermesPnP.models.high.model}" = "grok-4.6"
    test "${toString (optionsEval.options.services.hermesPnP ? extraPlugins)}" = "1"
    test "${toString (optionsEval.options.services.hermesPnP ? pluginInstall)}" = "1"
    test "${toString (optionsEval.options.services.hermesPnP.webui.enable.isDefined or true)}" = "1"
    test "${toString (optionsEval.options.services.hermesPnP.toolbox.enable.isDefined or true)}" = "1"
    test "${toString (optionsEval.options.services.hermesPnP.browser.enable.default or true)}" = "1"
    test "${toString (optionsEval.options.services.hermesPnP.browser.cdpPort.default or 0)}" = "9222"
    test "${toString (optionsEval.options.services.hermesPnP.browser.noVNC.enable.default or false)}" = "1"
    test "${toString (optionsEval.options.services.hermesPnP ? runtime)}" = ""
    test "${toString optionsEval.options.services.hermesPnP.gbrain.enable.default}" = ""
    test "${toString (optionsEval.options.services.hermesPnP ? mcpProxy)}" = "1"
    test "${toString optionsEval.options.services.hermesPnP.mcpProxy.enable.default}" = ""
    test "${toString (optionsEval.options.services.hermesPnP.mcpProxy ? backends)}" = "1"
    test "${toString (optionsEval.options.services ? mcpProxy)}" = "1"
    test "${toString optionsEval.options.services.hermesPnP.packageFixes.silenceMarkers.default}" = "1"
    test "${toString (optionsEval.options.services.hermesPnP ? hmc)}" = "1"
    test "${toString optionsEval.options.services.hermesPnP.hmc.enable.default}" = ""
    test "${toString (optionsEval.options.services.hermesPnP ? container)}" = "1"
    test "${toString optionsEval.options.services.hermesPnP.container.enable.default}" = ""
    test "${optionsEval.config.services.hermesPnP.container.image}" = "ubuntu:24.04"
    touch "$out"
  '';
}
