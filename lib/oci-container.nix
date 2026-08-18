# Slim official-Hermes-shaped OCI helper.
#
# Matches services.hermes-agent.container:
#   docker create --network=host
#   /nix/store bind + identity hash + start -a
#
# Slim entrypoint: provision the host hermes UID/GID, drop privs with
# setpriv. No sudo, no apt — WebUI/browser do not need a writable
# Ubuntu userland. The official agent entrypoint stays fat on purpose.
#
# Upstream-shaped: this file is the piece we can lift into
# nesquena/hermes-webui nix/nixosModules.nix as container.enable.
#
# Do not point cfg.image at ghcr.io/nesquena/hermes-webui or
# nousresearch/hermes-agent. Do not treat agent-browser docker/ as a
# runtime. The jail is ubuntu + /nix/store:ro + this entrypoint.
{ pkgs, lib }:

let
  inherit (lib)
    concatMapStringsSep
    concatStringsSep
    escapeShellArg
    mapAttrsToList
    mkDefault
    mkOption
    optional
    optionals
    types
    ;
in
rec {
  nixStoreBind = "/nix/store:/nix/store:ro";

  # NixOS /etc/ssl/certs is a symlink farm into /etc/static. Binding
  # only /etc/ssl leaves ca-certificates.crt dangling in Ubuntu.
  nixosCaBinds = [
    "/etc/ssl:/etc/ssl:ro"
    "/etc/static:/etc/static:ro"
  ];

  gitconfigBind = "/etc/gitconfig:/etc/gitconfig:ro";

  # Shared host-native hardening. No PrivateTmp / ProtectSystem /
  # ProtectHome — official webui already has a home; browser adds those
  # on its own units.
  hardenHost = {
    NoNewPrivileges = true;
    CapabilityBoundingSet = [ ];
    ProtectKernelTunables = true;
    ProtectKernelModules = true;
    ProtectKernelLogs = true;
    ProtectControlGroups = true;
    RestrictSUIDSGID = true;
  };

  mkOciServiceOptions =
    {
      extraOptions ? [ "--security-opt=no-new-privileges" ],
      description ? ''
        Run inside an OCI container (same docker create --network=host
        + /nix/store pattern as services.hermes-agent.container).
        Defaults on when hermesPnP.container.enable is set.
      '',
      extraOptionsDescription ? "Extra docker create args. Default refuses privilege regain.",
    }:
    {
      enable = mkOption {
        type = types.bool;
        default = false;
        inherit description;
      };
      backend = mkOption {
        type = types.str;
        default = "docker";
        description = "docker or podman. Follows hermesPnP.container.backend.";
      };
      image = mkOption {
        type = types.str;
        default = "ubuntu:24.04";
        description = "Base image (pulled at runtime). Same default as official agent.";
      };
      extraVolumes = mkOption {
        type = types.listOf types.str;
        default = [ ];
        description = ''
          Extra host:container[:mode] binds. Independent of
          services.hermes-agent.container.extraVolumes.
        '';
      };
      extraOptions = mkOption {
        type = types.listOf types.str;
        default = extraOptions;
        description = extraOptionsDescription;
      };
    };

  followComposerContainer = pnp: {
    enable = mkDefault pnp.container.enable;
    backend = mkDefault pnp.container.backend;
    image = mkDefault pnp.container.image;
  };

  mkSlimEntrypoint = name:
    pkgs.writeShellScript "${name}-oci-entrypoint" ''
      set -euo pipefail
      # Ubuntu 24.04 image. Keep this short — host isolation is the point.
      if [ -n "''${HERMES_UID:-}" ] && [ -n "''${HERMES_GID:-}" ]; then
        if ! getent group "$HERMES_GID" >/dev/null 2>&1; then
          groupadd -g "$HERMES_GID" hermes 2>/dev/null || true
        fi
        if ! getent passwd "$HERMES_UID" >/dev/null 2>&1; then
          useradd -u "$HERMES_UID" -g "$HERMES_GID" -d /home/hermes -M -s /bin/bash hermes 2>/dev/null || true
        fi
        mkdir -p /home/hermes /tmp
        chown "$HERMES_UID:$HERMES_GID" /home/hermes 2>/dev/null || true
        export HOME=/home/hermes
        export USER=hermes
        export LOGNAME=hermes
        # Drop every remaining capability once we are hermes. The container
      # starts with default docker caps so useradd/setpriv can run.
      exec setpriv --reuid="$HERMES_UID" --regid="$HERMES_GID" --init-groups \
        --bounding-set=-all --inh-caps=-all --ambient-caps=-all -- "$@"
      fi
      exec "$@"
    '';

  # Returns { preStart, script, preStop, identityHash }.
  mkUnitScripts = {
    backend,
    containerName,
    image,
    user,
    volumes,
    extraOptions ? [ ],
    extraEnv ? { },
    envFiles ? [ ],
    entrypoint,
    command,
    identity,
    identityFile,
  }:
    let
      containerBin =
        if backend == "docker"
        then "${pkgs.docker}/bin/docker"
        else "${pkgs.podman}/bin/podman";

      identityHash = builtins.hashString "sha256" (builtins.toJSON identity);

      volumeArgs = concatMapStringsSep " " (v: "--volume ${escapeShellArg v}") volumes;
      envArgs = concatStringsSep " " (mapAttrsToList (n: v: "--env ${n}=${escapeShellArg v}") extraEnv);
      envFileArgs = concatMapStringsSep " " (f: "--env-file ${escapeShellArg f}") envFiles;
      extraArgs = concatStringsSep " " extraOptions;
      cmdArgs = concatMapStringsSep " " escapeShellArg command;
    in
    {
      inherit identityHash identityFile containerBin;

      preStart = ''
        set -euo pipefail
        NEED_CREATE=false
        if ! ${containerBin} inspect ${containerName} >/dev/null 2>&1; then
          NEED_CREATE=true
        elif [ ! -f ${identityFile} ] || [ "$(cat ${identityFile})" != "${identityHash}" ]; then
          echo "Container config changed, recreating ${containerName}..."
          ${containerBin} rm -f ${containerName} || true
          NEED_CREATE=true
        fi

        if [ "$NEED_CREATE" = "true" ]; then
          HERMES_UID=$(${pkgs.coreutils}/bin/id -u ${user})
          HERMES_GID=$(${pkgs.coreutils}/bin/id -g ${user})
          echo "Creating ${containerName} (uid=$HERMES_UID gid=$HERMES_GID)..."
          ${containerBin} create \
            --name ${containerName} \
            --network=host \
            --entrypoint ${entrypoint} \
            ${volumeArgs} \
            --env HERMES_UID="$HERMES_UID" \
            --env HERMES_GID="$HERMES_GID" \
            ${envArgs} \
            ${envFileArgs} \
            ${extraArgs} \
            ${image} \
            ${cmdArgs}
          echo "${identityHash}" > ${identityFile}
        fi
      '';

      script = ''
        exec ${containerBin} start -a ${containerName}
      '';

      preStop = ''
        ${containerBin} stop -t 10 ${containerName} || true
      '';
    };

  # One attrset in, systemd fragment out. Callers build service-specific
  # volumes / extraEnv / command / identity extras; this owns the unit
  # skeleton and appends cfg.extraVolumes.
  #
  # → { dockerEnable, unit, preStart, script, preStop, volumes }
  mkOciJail = {
    name,
    description,
    user,
    cfg,
    volumes,
    extraEnv ? { },
    envFiles ? [ ],
    command,
    identityFile,
    identity ? { },
    after ? [ ],
    wants ? [ ],
    requiresDocker ? false,
    wantedBy ? [ "multi-user.target" ],
  }:
    let
      entrypoint = mkSlimEntrypoint name;
      allVolumes = volumes ++ cfg.extraVolumes;
      containerBinPkg = if cfg.backend == "docker" then pkgs.docker else pkgs.podman;
      fullIdentity = {
        inherit (cfg) image extraVolumes extraOptions;
        inherit extraEnv entrypoint envFiles command;
        volumes = allVolumes;
      } // identity;
      scripts = mkUnitScripts {
        backend = cfg.backend;
        containerName = name;
        image = cfg.image;
        inherit user extraEnv envFiles entrypoint command identityFile;
        volumes = allVolumes;
        extraOptions = cfg.extraOptions;
        identity = fullIdentity;
      };
    in
    {
      dockerEnable = cfg.backend == "docker";
      volumes = allVolumes;
      inherit (scripts) preStart script preStop;
      unit = {
        inherit description wantedBy;
        after = [
          "network-online.target"
        ] ++ optional (cfg.backend == "docker") "docker.service" ++ after;
        wants = [ "network-online.target" ] ++ wants;
        requires = optionals requiresDocker [ "docker.service" ];
        inherit (scripts) preStart script preStop;
        path = [
          containerBinPkg
          pkgs.coreutils
        ];
        serviceConfig = {
          Type = "simple";
          Restart = "on-failure";
          RestartSec = 5;
          TimeoutStartSec = 180;
          TimeoutStopSec = 30;
        };
      };
    };
}
