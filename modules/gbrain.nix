# Optional loopback `gbrain serve`. Off by default.
# Plugins work without this hook and no-op if GBRAIN_MCP_URL is unset.
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
    mkOption
    optionalAttrs
    types
    ;

  inherit (import ../lib { inherit pkgs lib; }) mkDockerEnv remapStatePath;

  pnp = config.services.hermesPnP;
  cfg = pnp.gbrain;
  agent = config.services.hermes-agent;

  containerTokenFile = remapStatePath {
    inherit (agent) stateDir;
    path = cfg.tokenFile;
  };
  home = "${agent.stateDir}/home";
  hostPath =
    if pnp.enable && pnp.toolbox.enable then
      pnp.toolbox.hostPath
    else
      # ${pkgs.bun}/bin is a reproducible interpreter fallback; the ~/.bun/bin
      # global shim (installed by the bootstrap one-shot) wins when present.
      "${home}/.bun/bin:${home}/.local/bin:${pkgs.bun}/bin:/run/current-system/sw/bin:/usr/bin:/bin";
  gbrainBin = "${home}/.bun/bin/gbrain";

  gbrainHttpScript = pkgs.writeShellScript "gbrain-mcp-http" ''
    set -euo pipefail
    export HOME="${home}"
    export PATH="${hostPath}"
    if [ ! -x "${gbrainBin}" ] && ! command -v gbrain >/dev/null 2>&1; then
      echo "gbrain-mcp-http: gbrain not installed under ${home}/.bun/bin (bootstrap first)" >&2
      exit 1
    fi
    cd "$HOME"
    exec gbrain serve --http --port ${toString cfg.port} --bind ${cfg.bind}
  '';

  hermesHome = "${agent.stateDir}/.hermes";
  wirePython = pkgs.python3.withPackages (ps: [ ps.pyyaml ]);
  wireScript = pkgs.writeText "gbrain-wire-config.py" (
    builtins.readFile ../scripts/gbrain-wire-config.py
  );
  # Store copy of the operator script so the one-shot can call it without
  # needing the repo on the device. Invoked via `bash ${setupScript}`.
  setupScript = builtins.toFile "gbrain-setup.sh" (
    builtins.readFile ../scripts/gbrain-setup.sh
  );
  tokenFile = if cfg.tokenFile != null then cfg.tokenFile else "";
in
{
  options.services.hermesPnP.gbrain = {
    enable = mkEnableOption ''
      GBrain HTTP MCP: start gbrain-mcp-http (loopback serve), mkDefault
      mcpServers.gbrain.url, export GBRAIN_MCP_URL / GBRAIN_TOKEN_FILE,
      and re-apply a literal Bearer on config.yaml after official merge.
      Also installs the two gbrain plugins even if they are not listed.
      Off by default. Does not ship PGLite, sources, or a memory registry.
      State bootstrap (CLI + PGLite + bearer) is the gbrain-bootstrap one-shot
      (runs scripts/gbrain-setup.sh, pinned to `ref`, stamped on success).
    '';

    url = mkOption {
      type = types.str;
      default = "http://127.0.0.1:3131/mcp";
      description = "GBrain HTTP MCP URL advertised to the agent.";
    };

    bind = mkOption {
      type = types.str;
      default = "127.0.0.1";
      description = "Address gbrain serve binds. Keep loopback unless you have a reason.";
    };

    port = mkOption {
      type = types.port;
      default = 3131;
      description = "Port gbrain serve listens on.";
    };

    tokenFile = mkOption {
      type = types.nullOr types.str;
      default = "${agent.stateDir}/.gbrain/hermes-mcp.token";
      defaultText = lib.literalExpression ''"''${config.services.hermes-agent.stateDir}/.gbrain/hermes-mcp.token"'';
      description = "Path to a token file. Injected as GBRAIN_TOKEN_FILE; never read into Nix.";
    };

    ref = mkOption {
      type = types.str;
      default = "v0.46.30.0";
      defaultText = lib.literalExpression ''"v0.46.30.0"'';
      description = "gbrain git ref the bootstrap one-shot installs (github:garrytan/gbrain#<ref>). Pin to a tag; bump on release.";
    };
  };

  config = mkIf cfg.enable {
    # Typed mcpServers option; official merges it into settings.mcp_servers.
    # mkDefault inside settings is stored as a literal.
    services.hermes-agent.mcpServers.gbrain = {
      url = mkDefault cfg.url;
      connect_timeout = mkDefault 120;
      timeout = mkDefault 120;
    };

    services.hermes-agent.environment = {
      GBRAIN_MCP_URL = mkDefault cfg.url;
    }
    // optionalAttrs (cfg.tokenFile != null && !agent.container.enable) {
      GBRAIN_TOKEN_FILE = mkDefault cfg.tokenFile;
    };

    services.hermes-agent.container.extraOptions = mkIf (
      agent.container.enable && cfg.tokenFile != null
    ) (mkDockerEnv { GBRAIN_TOKEN_FILE = containerTokenFile; });

    systemd.services.gbrain-mcp-http = {
      description = "GBrain MCP HTTP (loopback; sole PGLite writer)";
      after = [
        "network-online.target"
      ];
      wants = [ "network-online.target" ];
      wantedBy = [ "multi-user.target" ];
      # systemd only honours StartLimit* under [Unit], not [Service]
      unitConfig = {
        StartLimitIntervalSec = 120;
        StartLimitBurst = 5;
      };
      serviceConfig = {
        Type = "simple";
        User = agent.user;
        Group = agent.group;
        EnvironmentFile = map (path: "-${toString path}") agent.environmentFiles;
        Environment = [
          "HOME=${home}"
          "PATH=${hostPath}"
        ];
        WorkingDirectory = home;
        ExecStart = "${gbrainHttpScript}";
        Restart = "on-failure";
        RestartSec = 10;
        TimeoutStartSec = "120";
        # Loopback server hardening (mirrors upstream minions worker flags,
        # minus anything bun's JIT / PGLite IPC would trip on).
        NoNewPrivileges = true;
        PrivateTmp = true;
        ProtectSystem = "full";
        ProtectControlGroups = true;
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectKernelLogs = true;
        RestrictSUIDSGID = true;
        LockPersonality = true;
      };
    };

    # One-shot state bootstrap: installs the pinned CLI + PGLite + bearer on a
    # fresh host, no-ops once stamped. Runs the same script the operator would
    # (scripts/gbrain-setup.sh) so there is one source of truth. Deliberately
    # NOT ordered against gbrain-mcp-http: the script manages serve itself
    # (mint needs serve up, import/embed needs it down); the serve unit just
    # restarts until the CLI exists.
    systemd.services.gbrain-bootstrap = {
      description = "GBrain one-shot bootstrap (CLI + PGLite + token)";
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      wantedBy = [ "multi-user.target" ];
      serviceConfig = {
        Type = "oneshot";
      };
      script = ''
        set -euo pipefail
        export PATH="/run/current-system/sw/bin:${home}/.bun/bin:/run/wrappers/bin"
        # Stamp records the pinned ref. Re-run when the CLI is missing OR the
        # stamp no longer matches `ref` (fresh host and ref-bump both re-run).
        stamp="${agent.stateDir}/.gbrain/.bootstrap-ok"
        if [ -x "${gbrainBin}" ] && [ "$(cat "$stamp" 2>/dev/null)" = "${cfg.ref}" ]; then
          echo "gbrain-bootstrap: CLI present at pinned ref ${cfg.ref} — nothing to do"
          exit 0
        fi
        export HERMES_HOME_DIR="${home}"
        export HERMES_STATE="${hermesHome}"
        export GBRAIN_REF="${cfg.ref}"
        export GBRAIN_WIRE_PY="${wireScript}"
        bash ${setupScript}
        printf '%s' "${cfg.ref}" > "${agent.stateDir}/.gbrain/.bootstrap-ok"
        chmod 0644 "${agent.stateDir}/.gbrain/.bootstrap-ok"
        chown ${agent.user}:${agent.group} "${agent.stateDir}/.gbrain/.bootstrap-ok"
      '';
    };

    systemd.services.hermes-agent = {
      after = [ "gbrain-mcp-http.service" ];
      wants = [ "gbrain-mcp-http.service" ];
    };
    systemd.services.hermes-webui = mkIf pnp.webui.enable {
      after = [ "gbrain-mcp-http.service" ];
      wants = [ "gbrain-mcp-http.service" ];
    };

    # Directories gbrain serve expects. Official hermes-agent-setup merges
    # Nix mcpServers into config.yaml and Nix keys win (headers dropped),
    # so re-apply a literal Bearer after that merge.
    system.activationScripts.hermes-gbrain = lib.stringAfter [ "hermes-agent-setup" ] ''
      install -d -m 0750 -o ${agent.user} -g ${agent.group} ${home}
      install -d -m 0750 -o ${agent.user} -g ${agent.group} ${agent.stateDir}/.gbrain
      install -d -m 0750 -o ${agent.user} -g ${agent.group} ${home}/brain
      # .gbrain is canonical under stateDir now (sibling of .hermes). Migrate
      # a pre-relocation brain once, then link it in from $HOME so gbrain's
      # default ~/.gbrain keeps resolving. The relative target resolves in both
      # the host namespace (gbrain-mcp-http) and the OCI jail; home and stateDir
      # share a filesystem, so mv is an atomic rename.
      if [ -d ${home}/.gbrain ] && [ ! -L ${home}/.gbrain ]; then
        shopt -s dotglob nullglob 2>/dev/null || true
        mv -f ${home}/.gbrain/* ${agent.stateDir}/.gbrain/ 2>/dev/null || true
        rm -rf -- ${home}/.gbrain
      fi
      ln -sfn ../.gbrain ${home}/.gbrain
      if [ ! -e /home/hermes ]; then
        ln -sfn ${home} /home/hermes
      elif [ ! -L /home/hermes ] && [ ! -d /home/hermes ]; then
        ln -sfn ${home} /home/hermes
      fi

      token=""
      for tp in \
        ${lib.optionalString (tokenFile != "") tokenFile} \
        ${home}/.gbrain/hermes-mcp.token \
        /home/hermes/.gbrain/hermes-mcp.token
      do
        [ -n "$tp" ] || continue
        if [ -s "$tp" ]; then
          token=$(tr -d '\r\n' <"$tp")
          [ -n "$token" ] && break
        fi
      done
      if [ -z "$token" ]; then
        for ep in ${hermesHome}/.env /home/hermes/.hermes/.env; do
          [ -f "$ep" ] || continue
          line=$(grep -E '^GBRAIN_REMOTE_TOKEN=' "$ep" | tail -n1 || true)
          token=''${line#GBRAIN_REMOTE_TOKEN=}
          token=''${token#\"}
          token=''${token%\"}
          token=''${token#\'}
          token=''${token%\'}
          [ -n "$token" ] && break
        done
      fi
      ${wirePython}/bin/python3 ${wireScript} ${hermesHome}/config.yaml ${lib.escapeShellArg cfg.url} "$token" || true
      if [ -f ${hermesHome}/config.yaml ]; then
        chown ${agent.user}:${agent.group} ${hermesHome}/config.yaml 2>/dev/null || true
      fi
    '';
  };
}
