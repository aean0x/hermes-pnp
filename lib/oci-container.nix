# WebUI / browser OCI jail: docker create --network=host, /nix/store:ro,
# identity hash, start -a. Runs as the host hermes uid with --cap-drop=ALL,
# --read-only, no-new-privileges. Identity is /var/lib/hermes-oci/<name>
# (root 0700). Host-network so the jail reaches the same loopback services
# as native hermes. Service binds stay at the call site.
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

  identityDir = "/var/lib/hermes-oci";

  # After extraOptions so the consumer list cannot drop these.
  # --init (tini) reaps zombies. WebUI PID 1 is a Python server; without
  # it, dead git/browser-tool children stay as Z until the jail restarts.
  forcedCreateArgs = [
    "--init"
    "--security-opt=no-new-privileges"
    "--cap-drop=ALL"
    "--read-only"
    "--tmpfs=/tmp:nosuid,nodev,mode=1777"
    "--tmpfs=/run:nosuid,nodev,mode=0755"
  ];

  mkSlimEntrypoint = name:
    pkgs.writeShellScript "${name}-oci-entrypoint" ''
      set -euo pipefail
      umask 0077
      export HOME="''${HOME:-/home/hermes}"
      export USER="''${USER:-hermes}"
      export LOGNAME="''${LOGNAME:-hermes}"
      if command -v setpriv >/dev/null 2>&1; then
        exec setpriv --no-new-privs -- "$@"
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
      extraArgs = concatMapStringsSep " " escapeShellArg extraOptions;
      forcedArgs = concatMapStringsSep " " escapeShellArg forcedCreateArgs;
      cmdArgs = concatMapStringsSep " " escapeShellArg command;
    in
    {
      inherit identityHash identityFile containerBin;

      preStart = ''
        set -euo pipefail
        ${pkgs.coreutils}/bin/mkdir -p ${identityDir}
        ${pkgs.coreutils}/bin/chmod 700 ${identityDir}

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
            --user "$HERMES_UID:$HERMES_GID" \
            --entrypoint ${entrypoint} \
            ${volumeArgs} \
            ${envArgs} \
            ${envFileArgs} \
            ${extraArgs} \
            ${forcedArgs} \
            ${image} \
            ${cmdArgs}
          echo "${identityHash}" > ${identityFile}
          ${pkgs.coreutils}/bin/chmod 600 ${identityFile}
        fi
      '';

      script = ''
        exec ${containerBin} start -a ${containerName}
      '';

      preStop = ''
        ${containerBin} stop -t 10 ${containerName} || true
      '';
    };
in
{
  inherit identityDir;

  nixStoreBind = "/nix/store:/nix/store:ro";

  mkOciServiceOptions =
    {
      extraOptions ? [ ],
      description ? ''
        Run inside an OCI container (docker create --network=host,
        /nix/store:ro). Defaults on when hermesPnP.container.enable is set.
      '',
      extraOptionsDescription ? "Extra docker create args. Privilege-regain locks are always injected.",
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

  # Callers supply volumes / extraEnv / command / identity extras.
  # Returns { dockerEnable, unit, preStart, script, preStop, volumes, identityFile }.
  mkOciJail = {
    name,
    description,
    user,
    cfg,
    volumes,
    extraEnv ? { },
    envFiles ? [ ],
    command,
    identity ? { },
    after ? [ ],
    wants ? [ ],
    wantedBy ? [ "multi-user.target" ],
  }:
    let
      entrypoint = mkSlimEntrypoint name;
      allVolumes = volumes ++ cfg.extraVolumes;
      containerBinPkg = if cfg.backend == "docker" then pkgs.docker else pkgs.podman;
      needsDocker = cfg.backend == "docker";
      identityFile = "${identityDir}/${name}";
      fullIdentity = {
        inherit (cfg) image extraVolumes extraOptions;
        inherit extraEnv entrypoint envFiles command forcedCreateArgs;
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
      dockerEnable = needsDocker;
      volumes = allVolumes;
      inherit (scripts) preStart script preStop identityFile;
      unit = {
        inherit description wantedBy;
        after = [
          "network-online.target"
        ] ++ optional needsDocker "docker.service" ++ after;
        wants = [ "network-online.target" ] ++ wants;
        requires = optionals needsDocker [ "docker.service" ];
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
