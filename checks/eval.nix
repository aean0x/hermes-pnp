# Eval-only composer checks. Dummy packages; do not realize official builds.
{
  self,
  nixpkgs,
  system,
  pkgs,
}:

let
  inherit (nixpkgs) lib;

  dummyAgent =
    pkgs.runCommand "dummy-hermes-agent"
      {
        passthru.hermesVenv = pkgs.runCommand "dummy-hermes-venv" { } ''
          mkdir -p "$out/bin"
          touch "$out/bin/python3"
          chmod +x "$out/bin/python3"
        '';
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
      # Skip the official package wrap.
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

  modulesConfig = eval [ ../examples/composer.nix ];

  dropInConfig = eval [ ../examples/library-plugins.nix ];

  gbrainConfig = eval [ ../examples/gbrain.nix ];

  containerConfig = eval [ ../examples/container.nix ];
  containerResourcesConfig = eval [
    ../examples/container.nix
    {
      services.hermesPnP.webui.container.memory = "2g";
      services.hermesPnP.webui.container.cpus = 2;
      services.hermesPnP.browser.container.memory = "1g";
      services.hermesPnP.browser.container.shmSize = "256m";
    }
  ];
  adminConfig = eval [
    ../examples/container.nix
    { services.hermesPnP.admin.enable = true; }
  ];

  containerGbrainConfig = eval [
    ../examples/container.nix
    ../examples/gbrain.nix
  ];

  containerMcpConfig = eval [
    ../examples/container.nix
    ../examples/mcp-proxy.nix
  ];

  mcpProxyConfig = eval [ ../examples/mcp-proxy.nix ];

  browserConfig = eval [ ../examples/browser.nix ];

  toolboxConfig = eval [ ../examples/toolbox.nix ];

  foldedPackagesConfig = eval [
    ../examples/composer.nix
    { services.hermes-agent.extraPackages = [ pkgs.sops ]; }
  ];

  officialContainerOnly = eval [
    ../examples/composer.nix
    { services.hermes-agent.container.enable = true; }
  ];

  extraPluginUnion = eval [
    ../examples/library-plugins.nix
    { services.hermes-agent.extraPlugins = [ pkgs.hello ]; }
  ];

  skillsConfig = eval [ ../examples/skills.nix ];

  # Skip the GitHub fetch; keep the pin options.
  hmcConfig = eval [
    ../examples/hmc.nix
    { services.hermesPnP.hmc.enable = lib.mkForce false; }
  ];

  ladderConfig = eval [
    ../examples/composer.nix
    {
      services.hermesPnP.models.low.ladder = [
        {
          provider = "openrouter";
          model = "google/gemini-2.5-flash";
        }
      ];
      services.hermesPnP.models.high.ladder = [
        {
          provider = "anthropic";
          model = "claude-opus-4-6";
        }
      ];
    }
  ];

  optionsEval = evalSystem [ ];

  dropAux = (dropInConfig.services.hermes-agent.settings.auxiliary or { }).triage_specifier or { };

  # plugins is listOf, not a submodule.
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
    test "${
      toString (
        modulesConfig.services.hermes-webui.agent.package == modulesConfig.services.hermes-agent.package
      )
    }" = "1"
    test "${toString (modulesConfig.services.hermes-agent.extraDependencyGroups == [ ])}" = "1"
    test "${toString (modulesConfig.systemd.services ? gbrain-mcp-http)}" = ""
    test "${gbrainConfig.services.hermes-agent.mcpServers.gbrain.url}" = "http://127.0.0.1:3131/mcp"
    test "${toString (gbrainConfig.systemd.services ? gbrain-mcp-http)}" = "1"
    test "${toString (lib.hasInfix "gbrain-wire-config.py" gbrainConfig.system.activationScripts.hermes-gbrain.text)}" = "1"
    test "${toString (lib.elem "hermes-agent-setup" gbrainConfig.system.activationScripts.hermes-gbrain.deps)}" = "1"
    test "${gbrainConfig.systemd.services.gbrain-mcp-http.serviceConfig.User}" = "${gbrainConfig.services.hermes-agent.user}"
    test "${toString gbrainConfig.systemd.services.gbrain-mcp-http.unitConfig.StartLimitIntervalSec}" = "120"
    test "${toString gbrainConfig.systemd.services.gbrain-mcp-http.unitConfig.StartLimitBurst}" = "5"
    test "${
      toString (gbrainConfig.systemd.services.gbrain-mcp-http.serviceConfig ? StartLimitIntervalSec)
    }" = ""
    test "${toString (lib.elem "hermes-agent-setup.service" gbrainConfig.systemd.services.gbrain-mcp-http.after)}" = ""
    test "${
      toString (modulesConfig.services.hermes-webui.extraEnvironment ? HERMES_WEBUI_TRUST_FORWARDED_PROTO)
    }" = "1"
    test "${modulesConfig.services.hermes-webui.extraEnvironment.HERMES_WEBUI_TRUSTED_PROXY_CIDRS}" = "127.0.0.1/32,::1/128"
    test "${modulesConfig.systemd.services.hermes-webui.serviceConfig.UMask}" = "0077"
    test "${
      toString (modulesConfig.services.hermes-webui.extraEnvironment ? HERMES_WEBUI_EXTENSION_DIR)
    }" = "1"
    test "${toString (builtins.elem "model-router" modulesConfig.services.hermesPnP.plugins)}" = "1"
    test "${toString (builtins.elem "model-router" gbrainConfig.services.hermesPnP.plugins)}" = "1"
    test "${toString (builtins.elem "gbrain-retrieval-reflex" gbrainConfig.services.hermes-agent.settings.plugins.enabled)}" = "1"
    test "${toString (builtins.elem "gbrain-memory-flush" gbrainConfig.services.hermes-agent.settings.plugins.enabled)}" = "1"
    test "${toString (builtins.elem "model-router" gbrainConfig.services.hermes-agent.settings.plugins.enabled)}" = "1"
    test "${modulesConfig.services.hermes-agent.settings.model.default}" = "${modulesConfig.services.hermesPnP.models.high.model}"
    test "${modulesConfig.services.hermes-agent.settings.model.provider}" = "${modulesConfig.services.hermesPnP.models.high.provider}"
    test "${modulesConfig.services.hermes-agent.settings.auxiliary.triage_specifier.model}" = "${modulesConfig.services.hermesPnP.models.auxiliary.model}"
    test "${modulesConfig.services.hermes-agent.settings.auxiliary.background_review.model}" = "${modulesConfig.services.hermesPnP.models.auxiliary.model}"
    test "${modulesConfig.services.hermes-agent.settings.auxiliary.curator.model}" = "${modulesConfig.services.hermesPnP.models.auxiliary.model}"
    test "${modulesConfig.services.hermes-agent.settings.auxiliary.triage_specifier.reasoning_effort}" = "none"
    test "${toString ((modulesConfig.services.hermes-agent.settings.agent or {}).reasoning_effort or "")}" = ""
    test "${modulesConfig.services.hermes-agent.settings.delegation.model}" = "${modulesConfig.services.hermesPnP.models.medium.model}"
    test "${modulesConfig.services.hermes-agent.settings.cron.model}" = "${modulesConfig.services.hermesPnP.models.low.model}"
    test "${modulesConfig.services.hermes-agent.settings.browser.cdp_url}" = "http://127.0.0.1:9222"
    test "${modulesConfig.services.hermes-agent.settings.browser.engine}" = "${modulesConfig.services.hermesPnP.browser.engine}"
    test "${toString (modulesConfig.systemd.services ? hermes-browser)}" = "1"
    test "${toString (modulesConfig.systemd.services ? hermes-browser-gate)}" = "1"
    test "${toString (modulesConfig.systemd.services ? hermes-browser-vnc)}" = ""
    test "${toString (modulesConfig.systemd.services ? hermes-browser-novnc)}" = ""
    test "${toString (modulesConfig.systemd.services ? hermes-browser-env)}" = ""
    test "${modulesConfig.services.hermes-agent.environment.HERMES_BROWSER_GATE_URL}" = "http://127.0.0.1:4848"
    test "${modulesConfig.services.hermes-agent.environment.HERMES_BROWSER_GATE_PORT}" = "4848"
    test "${modulesConfig.services.hermes-agent.environment.AGENT_BROWSER_ENGINE}" = "${modulesConfig.services.hermesPnP.browser.engine}"
    test "${toString (modulesConfig.services.hermes-agent.extraPackages == [ ])}" = "1"
    test "${
      toString (
        lib.any (p: lib.hasPrefix "hermes-toolbox" (p.name or "")) modulesConfig.users.users.hermes.packages
      )
    }" = "1"
    test "${toString modulesConfig.services.hermes-agent.addToSystemPackages}" = "1"
    test "${modulesConfig.services.hermes-agent.environment.HERMES_BROWSER_PROFILE}" = "${modulesConfig.services.hermes-agent.stateDir}/browser-profile"
    test "${gbrainConfig.services.hermes-agent.environment.GBRAIN_TOKEN_FILE}" = "${gbrainConfig.services.hermes-agent.stateDir}/home/.gbrain/hermes-mcp.token"
    test "${toString (builtins.elem 6080 modulesConfig.networking.firewall.allowedTCPPorts)}" = ""
    test "${toString (builtins.elem 4848 modulesConfig.networking.firewall.allowedTCPPorts)}" = ""
    test "${toString (modulesConfig.services.hermesPnP.toolbox.hostPath != "")}" = "1"
    test "${
      toString (
        builtins.match ".*toolbox/bin.*" modulesConfig.services.hermesPnP.toolbox.hostPath != null
      )
    }" = "1"
    test "${toString modulesConfig.services.hermes-agent.container.enable}" = ""
    test "${lib.concatStringsSep "," modulesConfig.services.hermes-agent.settings.skills.external_dirs}" = "/var/lib/hermes/skills"
    test "${toString modulesConfig.programs.git.enable}" = "1"
    test "${
      toString (
        lib.any (
          x: lib.hasInfix "git-credential-github-env" (x.credential.helper or "")
        ) modulesConfig.programs.git.config
      )
    }" = "1"
    test "${toString (lib.any (x: x ? user) modulesConfig.programs.git.config)}" = ""
    test "${toString containerConfig.services.hermes-agent.container.enable}" = "1"
    test "${
      toString (
        lib.any (
          v: lib.hasInfix "/etc/gitconfig" v
        ) containerConfig.services.hermes-agent.container.extraVolumes
      )
    }" = "1"
    test "${lib.concatStringsSep "," containerConfig.services.hermes-agent.settings.skills.external_dirs}" = "/data/skills"
    test "${containerConfig.services.hermes-agent.container.image}" = "ubuntu:24.04"
    test "${toString containerConfig.services.hermesPnP.webui.container.enable}" = "1"
    test "${toString (lib.hasInfix "ca-bundle.crt:/etc/ssl/certs/ca-certificates.crt:ro" containerConfig.systemd.services.hermes-webui.preStart)}" = "1"
    test "${toString (lib.hasInfix "SSL_CERT_FILE=" containerConfig.systemd.services.hermes-webui.preStart)}" = "1"
    test "${toString (lib.hasInfix "/etc/ssl:/etc/ssl" containerConfig.systemd.services.hermes-webui.preStart)}" = ""
    test "${toString (lib.hasInfix "/etc/static" containerConfig.systemd.services.hermes-webui.preStart)}" = ""
    test "${toString (lib.hasInfix "/etc/gitconfig:/etc/gitconfig:ro" containerConfig.systemd.services.hermes-webui.preStart)}" = "1"
    test "${toString (lib.hasInfix "/var/lib/hermes-oci/hermes-webui" containerConfig.systemd.services.hermes-webui.preStart)}" = "1"
    test "${toString (lib.hasInfix "/var/lib/hermes-oci/hermes-browser" containerConfig.systemd.services.hermes-browser.preStart)}" = "1"
    test "${toString (lib.hasInfix ".oci-identity" containerConfig.systemd.services.hermes-webui.preStart)}" = ""
    test "${toString (lib.hasInfix ".oci-identity" containerConfig.systemd.services.hermes-browser.preStart)}" = ""
    test "${toString (lib.hasInfix "no-new-privileges" containerConfig.systemd.services.hermes-webui.preStart)}" = "1"
    test "${containerConfig.services.hermes-webui.extraEnvironment.HERMES_PYTHON}" = "/home/hermes/.venv/bin/python3"
    test "${toString (containerConfig.services.hermes-webui.extraEnvironment ? PIP_USER)}" = ""
    test "${toString (lib.hasInfix "/home/hermes/.venv/bin" containerConfig.services.hermesPnP.toolbox.containerPath)}" = "1"
    test "${toString (lib.hasInfix "HERMES_PYTHON=/home/hermes/.venv/bin/python3" containerConfig.systemd.services.hermes-webui.preStart)}" = "1"
    test "${toString (lib.hasInfix "HERMES_PYTHON=/home/hermes/.venv/bin/python3" (lib.concatStringsSep " " containerConfig.services.hermes-agent.container.extraOptions))}" = "1"
    test "${toString (lib.hasInfix "no-new-privileges" containerConfig.systemd.services.hermes-browser.preStart)}" = "1"
    test "${toString (lib.hasInfix "--init" containerConfig.systemd.services.hermes-webui.preStart)}" = "1"
    test "${toString (lib.hasInfix "--init" containerConfig.systemd.services.hermes-browser.preStart)}" = "1"
    test "${toString (lib.hasInfix "hermes-browser-supervisor" containerConfig.systemd.services.hermes-browser.preStart)}" = "1"
    test "${toString (lib.hasInfix "--shm-size=2g" containerConfig.systemd.services.hermes-browser.preStart)}" = ""
    test "${toString (lib.hasInfix "--memory=1g" containerResourcesConfig.systemd.services.hermes-browser.preStart)}" = "1"
    test "${toString (lib.hasInfix "--shm-size=256m" containerResourcesConfig.systemd.services.hermes-browser.preStart)}" = "1"
    test "${toString (lib.hasInfix "--memory=2g" containerResourcesConfig.systemd.services.hermes-webui.preStart)}" = "1"
    test "${toString (lib.hasInfix "--cpus=2" containerResourcesConfig.systemd.services.hermes-webui.preStart)}" = "1"
    test "${toString (lib.hasInfix "--init" containerResourcesConfig.systemd.services.hermes-browser.preStart)}" = "1"
    test "${toString (lib.elem "--renderer-process-limit=5" containerConfig.services.hermesPnP.browser.extraArgs)}" = "1"
    test "${toString (containerConfig.systemd.sockets ? hermes-admin)}" = ""
    test "${toString (lib.hasInfix "/run/hermes-admin" containerConfig.systemd.services.hermes-webui.preStart)}" = ""
    test "${toString (adminConfig.systemd.sockets ? hermes-admin)}" = "1"
    test "${toString (adminConfig.systemd.services ? "hermes-admin@")}" = "1"
    test "${toString (lib.hasInfix "/run/hermes-admin:/run/hermes-admin" adminConfig.systemd.services.hermes-webui.preStart)}" = "1"
    test "${toString (lib.elem "/run/hermes-admin:/run/hermes-admin" adminConfig.services.hermes-agent.container.extraVolumes)}" = "1"
    test "${toString (lib.hasInfix "/run/docker.sock" adminConfig.systemd.services.hermes-webui.preStart)}" = ""
    test "${toString (lib.hasInfix "--read-only" containerConfig.systemd.services.hermes-webui.preStart)}" = "1"
    test "${toString (lib.hasInfix "--cap-drop=ALL" containerConfig.systemd.services.hermes-webui.preStart)}" = "1"
    test "${toString (lib.hasInfix "--user" containerConfig.systemd.services.hermes-webui.preStart)}" = "1"
    test "${toString (lib.hasInfix "/etc/ssl:/etc/ssl" containerConfig.systemd.services.hermes-browser.preStart)}" = ""
    test "${toString (lib.hasInfix "/etc/static" containerConfig.systemd.services.hermes-browser.preStart)}" = ""
    test "${toString (lib.hasInfix ":/etc/ssl/certs/ca-certificates.crt:ro" containerConfig.systemd.services.hermes-browser.preStart)}" = "1"
    test "${toString (lib.hasInfix "SSL_CERT_FILE=" containerConfig.systemd.services.hermes-browser.preStart)}" = "1"
    test "${toString (containerConfig.services.hermes-agent.environment ? HERMES_BROWSER_PROFILE)}" = ""
    test "${toString (lib.hasInfix "HERMES_BROWSER_PROFILE=/data/browser-profile" (lib.concatStringsSep " " containerConfig.services.hermes-agent.container.extraOptions))}" = "1"
    test "${toString (lib.hasInfix "HERMES_BUNDLED_PLUGINS=" (lib.concatStringsSep " " containerConfig.services.hermes-agent.container.extraOptions))}" = ""
    test "${toString containerConfig.services.hermesPnP.browser.container.enable}" = "1"
    test "${toString (lib.elem "docker.service" containerConfig.systemd.services.hermes-webui.requires)}" = "1"
    test "${toString (lib.elem "docker.service" containerConfig.systemd.services.hermes-browser.requires)}" = "1"
    test "${toString (containerConfig.systemd.services ? hermes-browser)}" = "1"
    test "${toString (containerConfig.systemd.services.hermes-browser.script != "")}" = "1"
    test "${toString (containerConfig.systemd.services ? hermes-browser-vnc)}" = ""
    test "${toString (containerConfig.systemd.services ? hermes-browser-novnc)}" = ""
    test "${toString (containerConfig.systemd.services ? hermes-browser-env)}" = ""
    test "${toString (containerConfig.systemd.services ? hermes-browser-gate)}" = ""
    test "${toString (lib.hasInfix "session info --json" (builtins.readFile ../modules/browser/shared.nix))}" = ""
    test "${toString (lib.hasInfix "connect \${cdpUrl}" (builtins.readFile ../modules/browser/shared.nix))}" = "1"
    test "${toString (lib.hasInfix "connect \${toString cdpPort}" (builtins.readFile ../modules/browser/shared.nix))}" = ""
    test "${toString (lib.hasInfix "default.pid" (builtins.readFile ../modules/browser/shared.nix))}" = "1"
    test "${toString (lib.hasInfix "FONTCONFIG_FILE" (builtins.readFile ../modules/browser/shared.nix))}" = "1"
    test "${toString (lib.hasInfix "about:blank" (builtins.readFile ../modules/browser/shared.nix))}" = ""
    test "${toString (lib.hasInfix "dashboard.pid" (builtins.readFile ../modules/browser/shared.nix))}" = "1"
    test "${toString (lib.hasInfix ''"$dash/"'' (builtins.readFile ../modules/browser/shared.nix))}" = ""
    test "${toString (lib.hasInfix "Current Session" (builtins.readFile ../modules/browser/container.nix))}" = ""
    test "${toString (lib.hasInfix "json/close" (builtins.readFile ../modules/browser/container.nix))}" = ""
    test "${toString (lib.hasInfix "setpriv" (builtins.readFile ../lib/oci-container.nix))}" = ""
    test "${toString (lib.hasInfix "hide-crash-restore-bubble" (builtins.readFile ../modules/browser/default.nix))}" = ""
    test "${toString (lib.hasInfix "dashboard start --help" (builtins.readFile ../modules/browser/shared.nix))}" = ""
    test "${toString (lib.hasInfix "disable-dev-shm-usage" (builtins.readFile ../modules/browser/shared.nix))}" = "1"
    test "${toString (lib.hasInfix "pkgs.agent-browser or" (builtins.readFile ../modules/browser/shared.nix))}" = ""
    test "${
      toString (
        lib.hasInfix "agent-browser-0.34" (
          lib.concatMapStringsSep " " toString modulesConfig.environment.systemPackages
        )
      )
    }" = "1"
    test "${
      toString (
        lib.hasInfix "agent-browser-0.27" (
          lib.concatMapStringsSep " " toString modulesConfig.environment.systemPackages
        )
      )
    }" = ""
    test "${
      toString (
        lib.hasInfix "agent-browser-0.34" (
          lib.concatMapStringsSep " " toString containerConfig.environment.systemPackages
        )
      )
    }" = "1"
    test "${containerConfig.services.hermesPnP.browser.gate.listenAddress}" = "127.0.0.1"
    test "${toString containerConfig.services.hermesPnP.browser.gate.port}" = "4848"
    test "${toString (builtins.elem 6080 containerConfig.networking.firewall.allowedTCPPorts)}" = ""
    test "${toString (builtins.elem 4848 containerConfig.networking.firewall.allowedTCPPorts)}" = ""
    test "${containerConfig.services.hermes-agent.environment.HERMES_BROWSER_GATE_URL}" = "http://127.0.0.1:4848"
    test "${
      toString (containerGbrainConfig.services.hermes-agent.environment ? GBRAIN_TOKEN_FILE)
    }" = ""
    test "${toString (lib.hasInfix "GBRAIN_TOKEN_FILE=/home/hermes/.gbrain/hermes-mcp.token" (lib.concatStringsSep " " containerGbrainConfig.services.hermes-agent.container.extraOptions))}" = "1"
    test "${toString (lib.elem "mcp-proxy.service" containerMcpConfig.systemd.services.hermes-webui.after)}" = "1"
    test "${toString (lib.elem "mcp-proxy.service" containerMcpConfig.systemd.services.hermes-webui.wants)}" = "1"
    test "${containerMcpConfig.services.hermesPnP.mcpProxy.clientAuth}" = "token"
    test "${
      toString (
        containerMcpConfig.services.hermes-agent.mcpServers.github.headers."X-MCP-Proxy-Token"
        == "\${MCP_PROXY_TOKEN}"
      )
    }" = "1"
    test "${
      toString (
        lib.any (
          p: toString p == "/run/mcp-proxy/client.env"
        ) containerMcpConfig.services.hermes-agent.environmentFiles
      )
    }" = "1"
    touch "$out"
  '';

  drop-in = pkgs.runCommand "hermes-pnp-drop-in-eval" { } ''
    test "${toString dropInConfig.services.hermes-agent.addToSystemPackages}" = ""
    test "${toString dropInConfig.services.hermesPnP.enable}" = ""
    test "${toString dropInConfig.services.hermesPnP.git.credentialHelper.enable}" = ""
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
    test "${optionsEval.config.services.hermesPnP.models.auxiliary.model}" = "deepseek-v4-flash"
    test "${optionsEval.config.services.hermesPnP.models.auxiliary.reasoning_effort}" = "none"
    test "${toString (optionsEval.config.services.hermesPnP.models.low.reasoning_effort == null)}" = "1"
    test "${toString (optionsEval.config.services.hermesPnP.models.high.reasoning_effort == null)}" = "1"
    test "${toString (optionsEval.config.services.hermesPnP.models.low.ladder == [ ])}" = "1"
    test "${toString (optionsEval.options.services.hermesPnP.models.low ? ladder)}" = "1"
    test "${toString (optionsEval.options.services.hermesPnP.models.auxiliary ? ladder)}" = ""
    test "${toString (optionsEval.options.services.hermesPnP ? extraPlugins)}" = "1"
    test "${toString (optionsEval.options.services.hermesPnP ? extraPluginDirs)}" = "1"
    test "${toString (optionsEval.options.services.hermesPnP ? pluginInstall)}" = "1"
    test "${toString (optionsEval.options.services.hermesPnP.webui.enable.isDefined or true)}" = "1"
    test "${toString (optionsEval.options.services.hermesPnP.webui ? container)}" = "1"
    test "${toString (optionsEval.options.services.hermesPnP.browser ? container)}" = "1"
    test "${toString (optionsEval.options.services.hermesPnP.webui.container ? memory)}" = "1"
    test "${toString (optionsEval.options.services.hermesPnP.browser.container ? shmSize)}" = "1"
    test "${toString (optionsEval.options.services.hermesPnP.browser ? gate)}" = "1"
    test "${toString (optionsEval.options.services.hermesPnP.browser ? cdpAllowOrigins)}" = "1"
    test "${toString (optionsEval.options.services.hermesPnP.browser ? noVNC)}" = ""
    test "${toString (optionsEval.options.services.hermesPnP.toolbox.enable.isDefined or true)}" = "1"
    test "${toString (optionsEval.options.services.hermesPnP.browser.enable.default or true)}" = "1"
    test "${toString (optionsEval.options.services.hermesPnP.browser.cdpPort.default or 0)}" = "9222"
    test "${
      toString (optionsEval.options.services.hermesPnP.browser.gate.enable.default or false)
    }" = "1"
    test "${toString (optionsEval.options.services.hermesPnP.browser.gate.port.default or 0)}" = "4848"
    test "${toString (optionsEval.options.services.hermesPnP ? runtime)}" = ""
    test "${toString optionsEval.options.services.hermesPnP.gbrain.enable.default}" = ""
    test "${toString (optionsEval.options.services.hermesPnP ? mcpProxy)}" = "1"
    test "${toString optionsEval.options.services.hermesPnP.mcpProxy.enable.default}" = ""
    test "${toString (optionsEval.options.services.hermesPnP.mcpProxy ? backends)}" = "1"
    test "${optionsEval.config.services.hermesPnP.mcpProxy.clientAuth}" = "none"
    test "${toString (optionsEval.options.services ? mcpProxy)}" = "1"
    test "${toString optionsEval.options.services.hermesPnP.packageFixes.silenceMarkers.default}" = "1"
    test "${toString (optionsEval.options.services.hermesPnP ? hmc)}" = "1"
    test "${toString optionsEval.options.services.hermesPnP.hmc.enable.default}" = ""
    test "${toString (optionsEval.options.services.hermesPnP ? container)}" = "1"
    test "${toString optionsEval.options.services.hermesPnP.container.enable.default}" = ""
    test "${toString (optionsEval.options.services.hermesPnP ? admin)}" = "1"
    test "${toString optionsEval.options.services.hermesPnP.admin.enable.default}" = ""
    test "${toString (optionsEval.options.services.hermesPnP.browser ? maxTabs)}" = "1"
    test "${optionsEval.config.services.hermesPnP.container.image}" = "ubuntu:24.04"
    touch "$out"
  '';

  examples = pkgs.runCommand "hermes-pnp-examples-eval" { } ''
    test "${toString mcpProxyConfig.services.hermesPnP.mcpProxy.enable}" = "1"
    test "${mcpProxyConfig.services.hermesPnP.mcpProxy.clientAuth}" = "none"
    test "${
      toString (mcpProxyConfig.services.hermes-agent.mcpServers.github.headers."X-MCP-Proxy-Token" or "")
    }" = ""
    test "${mcpProxyConfig.services.hermesPnP.mcpProxy.backends.github.auth.mode}" = "passthrough"
    test "${mcpProxyConfig.services.hermesPnP.mcpProxy.backends.docs.upstream}" = "https://example.invalid/mcp"
    test "${mcpProxyConfig.services.hermes-agent.mcpServers.github.url}" = "http://127.0.0.1:3140/github"
    test "${toString (mcpProxyConfig.systemd.services ? mcp-proxy)}" = "1"
    test "${toString (lib.hasInfix "--config" (toString mcpProxyConfig.systemd.services.mcp-proxy.serviceConfig.ExecStart))}" = "1"
    test "${toString (lib.hasInfix "mcp-proxy-0." (toString mcpProxyConfig.systemd.services.mcp-proxy.serviceConfig.ExecStart))}" = ""
    test "${browserConfig.services.hermesPnP.browser.gate.publicUrl}" = "https://browser.example.com/"
    test "${toString (browserConfig.services.hermesPnP.browser.package == pkgs.brave)}" = "1"
    test "${toString (builtins.elem pkgs.sops toolboxConfig.services.hermesPnP.toolbox.extraPackages)}" = "1"
    test "${toString (builtins.elem pkgs.sops toolboxConfig.services.hermesPnP.toolbox.paths)}" = "1"
    test "${toString (builtins.elem pkgs.sops foldedPackagesConfig.services.hermes-agent.extraPackages)}" = "1"
    test "${toString (builtins.elem pkgs.sops foldedPackagesConfig.services.hermesPnP.toolbox.paths)}" = "1"
    test "${
      toString (
        lib.any (
          p: lib.hasPrefix "hermes-toolbox" (p.name or "")
        ) foldedPackagesConfig.services.hermes-agent.extraPackages
      )
    }" = ""
    test "${toString officialContainerOnly.services.hermesPnP.container.enable}" = ""
    test "${toString officialContainerOnly.services.hermes-agent.container.enable}" = "1"
    test "${toString officialContainerOnly.services.hermesPnP.webui.container.enable}" = "1"
    test "${toString officialContainerOnly.services.hermesPnP.browser.container.enable}" = "1"
    test "${toString (lib.hasInfix "--network host" officialContainerOnly.systemd.services.hermes-webui.preStart)}" = "1"
    test "${toString (builtins.elem "hello" extraPluginUnion.services.hermes-agent.settings.plugins.enabled)}" = "1"
    test "${toString (builtins.elem "nix-managed-hello" extraPluginUnion.services.hermes-agent.settings.plugins.enabled)}" = "1"
    test "${toString (builtins.elem "model-router" extraPluginUnion.services.hermes-agent.settings.plugins.enabled)}" = "1"
    test "${toString (skillsConfig.services.hermesPnP.skills.extraSkills ? site-runbook)}" = "1"
    test "${toString (hmcConfig.services.hermesPnP.hmc.compressPercent == 0.30)}" = "1"
    test "${toString hmcConfig.services.hermesPnP.hmc.enable}" = ""
    test "${(builtins.head ladderConfig.services.hermesPnP.models.low.ladder).model}" = "google/gemini-2.5-flash"
    test "${(builtins.head ladderConfig.services.hermes-agent.settings.fallback_providers).provider}" = "anthropic"
    test "${(builtins.head ladderConfig.services.hermes-agent.settings.fallback_providers).model}" = "claude-opus-4-6"
    test "${toString (modulesConfig.services.hermes-agent.settings ? fallback_providers)}" = ""
    touch "$out"
  '';
}
