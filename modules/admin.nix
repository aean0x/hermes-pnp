# Allowlisted host helper. Jails cannot sudo (no-new-privileges)
# and must not get the docker socket. A unix socket owned by the
# hermes group is the only extra privilege: status / restart /
# reset-failed of named units. Nothing else.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib)
    mkEnableOption
    mkIf
    mkOption
    types
    ;

  pnp = config.services.hermesPnP;
  agent = config.services.hermes-agent;
  cfg = pnp.admin;

  socketDir = "/run/hermes-admin";
  socketPath = "${socketDir}/admin.sock";
  bind = "${socketDir}:${socketDir}";

  unitPattern = lib.concatStringsSep "|" cfg.units;

  server = pkgs.writeShellApplication {
    name = "hermes-admin-server";
    runtimeInputs = [
      pkgs.coreutils
      pkgs.systemd
    ];
    text = ''
      set -euo pipefail
      cooldown=${toString cfg.restartCooldownSec}
      stampdir=${socketDir}

      IFS= read -r line || exit 0
      # One line, two tokens, allowlist only. Never eval.
      if [[ ! "$line" =~ ^(status|restart|reset-failed)[[:space:]]+(${unitPattern})(\.service)?$ ]]; then
        echo "denied" >&2
        exit 1
      fi
      verb="''${BASH_REMATCH[1]}"
      unit="''${BASH_REMATCH[2]}"

      if [[ "$verb" == "restart" ]]; then
        now=$(date +%s)
        stamp="$stampdir/last-$unit"
        if [[ -f "$stamp" ]]; then
          last=$(cat "$stamp")
          if (( now - last < cooldown )); then
            echo "cooldown ''${cooldown}s" >&2
            exit 3
          fi
        fi
        echo "$now" > "$stamp"
        systemctl reset-failed "$unit.service" || true
        systemctl --no-pager --full restart "$unit.service"
      else
        systemctl --no-pager --full "$verb" "$unit.service"
      fi
    '';
  };

  client = pkgs.writeShellApplication {
    name = "hermes-admin";
    runtimeInputs = [ pkgs.socat ];
    text = ''
      set -euo pipefail
      sock="''${HERMES_ADMIN_SOCKET:-${socketPath}}"
      usage() {
        echo "usage: hermes-admin status|restart|reset-failed UNIT" >&2
        echo "units: ${lib.concatStringsSep " " cfg.units}" >&2
        exit 2
      }
      [[ $# -eq 2 ]] || usage
      if [[ ! -S "$sock" ]]; then
        echo "hermes-admin: no socket at $sock" >&2
        exit 1
      fi
      printf '%s %s\n' "$1" "$2" | socat -t 90 - UNIX-CONNECT:"$sock"
    '';
  };
in
{
  options.services.hermesPnP.admin = {
    enable = mkEnableOption ''
      Host unix-socket helper so the hermes user (host or jail) can
      status/restart/reset-failed a fixed unit list. Not sudo. Not
      the docker socket. Off by default.
    '';

    units = mkOption {
      type = types.listOf types.str;
      default = [
        "hermes-agent"
        "hermes-webui"
        "hermes-browser"
      ];
      description = ''
        Unit names without .service. Wider lists increase blast
        radius. Do not add docker, gbrain, or host-unrelated units.
      '';
    };

    restartCooldownSec = mkOption {
      type = types.ints.unsigned;
      default = 15;
      description = "Minimum seconds between restart of the same unit.";
    };
  };

  config = mkIf (pnp.enable && cfg.enable) {
    assertions = [
      {
        assertion = cfg.units != [ ];
        message = "services.hermesPnP.admin.units must not be empty.";
      }
      {
        assertion = lib.all (u: builtins.match "[A-Za-z0-9@_-]+" u != null) cfg.units;
        message = "services.hermesPnP.admin.units: names must match [A-Za-z0-9@_-]+.";
      }
    ];

    environment.systemPackages = [ client ];

    services.hermesPnP.toolbox.extraPackages = [ client ];

    systemd.tmpfiles.rules = [
      "d ${socketDir} 0750 root ${agent.group} -"
    ];

    systemd.sockets.hermes-admin = {
      description = "Hermes admin allowlist socket";
      wantedBy = [ "sockets.target" ];
      socketConfig = {
        ListenStream = socketPath;
        SocketMode = "0660";
        SocketUser = "root";
        SocketGroup = agent.group;
        DirectoryMode = "0750";
        Accept = true;
      };
    };

    systemd.services."hermes-admin@" = {
      description = "Hermes admin allowlist connection";
      serviceConfig = {
        Type = "oneshot";
        StandardInput = "socket";
        StandardOutput = "socket";
        StandardError = "journal";
        ExecStart = "${server}/bin/hermes-admin-server";
        User = "root";
        ProtectHome = true;
        PrivateTmp = true;
        NoNewPrivileges = true;
        RestrictAddressFamilies = [ "AF_UNIX" ];
        RestrictSUIDSGID = true;
        LockPersonality = true;
        MemoryDenyWriteExecute = true;
      };
    };

    # Socket dir only. Never /run (that would leak docker.sock).
    services.hermesPnP.webui.container.extraVolumes = mkIf pnp.webui.container.enable [ bind ];

    services.hermes-agent.container.extraVolumes = mkIf (agent.container.enable or false) [ bind ];
  };
}
