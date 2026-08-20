# WebUI / browser OCI jail: docker create, /nix/store:ro, identity hash,
# start -a. Runs as the host hermes uid with --cap-drop=ALL, --read-only,
# no-new-privileges. Identity is /var/lib/hermes-oci/<name> (root 0700).
# Network follows official services.hermes-agent.container.network when
# that option exists (else "host") so the stack can leave host net
# together. Loopback pairing (CDP, WebUI, GBrain, mcp-proxy) still
# uses 127.0.0.1 until those URLs are remapped. Service binds stay at
# the call site.
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

  mkSlimEntrypoint =
    name:
    pkgs.writeShellScript "${name}-oci-entrypoint" ''
      set -euo pipefail
      umask 0077
      export HOME="''${HOME:-/home/hermes}"
      export USER="''${USER:-hermes}"
      export LOGNAME="''${LOGNAME:-hermes}"
      exec "$@"
    '';

  # Returns { preStart, script, preStop, identityHash }.
  mkUnitScripts =
    {
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
      network ? "host",
      publish ? [ ],
    }:
    let
      containerBin =
        if backend == "docker" then "${pkgs.docker}/bin/docker" else "${pkgs.podman}/bin/podman";

      identityHash = builtins.hashString "sha256" (builtins.toJSON identity);

      networkIsNamed =
        !(lib.elem network [
          "host"
          "bridge"
          "none"
        ]);
      networkIsHost = network == "host";

      volumeArgs = concatMapStringsSep " " (v: "--volume ${escapeShellArg v}") volumes;
      envArgs = concatStringsSep " " (mapAttrsToList (n: v: "--env ${n}=${escapeShellArg v}") extraEnv);
      envFileArgs = concatMapStringsSep " " (f: "--env-file ${escapeShellArg f}") envFiles;
      extraArgs = concatMapStringsSep " " escapeShellArg extraOptions;
      forcedArgs = concatMapStringsSep " " escapeShellArg forcedCreateArgs;
      cmdArgs = concatMapStringsSep " " escapeShellArg command;
      publishArgs =
        if networkIsHost then "" else concatMapStringsSep " " (p: "--publish ${escapeShellArg p}") publish;
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
          ${lib.optionalString networkIsNamed ''
            if ! ${containerBin} network inspect ${escapeShellArg network} >/dev/null 2>&1; then
              echo "Creating network ${network}..."
              ${containerBin} network create ${escapeShellArg network}
            fi
          ''}
          echo "Creating ${containerName} (uid=$HERMES_UID gid=$HERMES_GID)..."
          ${containerBin} create \
            --name ${containerName} \
            --network ${escapeShellArg network} \
            ${publishArgs} \
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
rec {
  inherit identityDir;

  nixStoreBind = "/nix/store:/nix/store:ro";

  mkOciServiceOptions =
    {
      extraOptions ? [ ],
      description ? ''
        Run inside an OCI container (docker create, /nix/store:ro).
        Network follows official services.hermes-agent.container.
        Defaults on when the official agent container is on.
      '',
      extraOptionsDescription ? "Extra docker create args. Privilege-regain locks are always injected.",
      memory ? null,
      memorySwap ? null,
      cpus ? null,
      oomScoreAdj ? null,
      shmSize ? null,
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
      publish = mkOption {
        type = types.listOf types.str;
        default = [ ];
        description = ''
          --publish entries (ip:hostPort:containerPort). Ignored when
          the jail network is "host". For leaving host net with the
          official container.network PR.
        '';
        example = [ "127.0.0.1:8787:8787" ];
      };
      memory = mkOption {
        type = types.nullOr types.str;
        default = memory;
        example = "2g";
        description = "Docker --memory. Null = no cap. Prefer this over extraOptions.";
      };
      memorySwap = mkOption {
        type = types.nullOr types.str;
        default = memorySwap;
        example = "2g";
        description = "Docker --memory-swap. Null = omit (docker default). Set equal to memory to disable swap.";
      };
      cpus = mkOption {
        type = types.nullOr types.number;
        default = cpus;
        example = 2;
        description = "Docker --cpus. Null = no quota.";
      };
      oomScoreAdj = mkOption {
        type = types.nullOr (types.ints.between (-1000) 1000);
        default = oomScoreAdj;
        example = 500;
        description = "Docker --oom-score-adj. Positive = die first.";
      };
      shmSize = mkOption {
        type = types.nullOr types.str;
        default = shmSize;
        example = "256m";
        description = "Docker --shm-size. Chromium jails usually need this.";
      };
    };

  # Follow the official agent container, not hermesPnP.container.
  # hermesPnP.container.enable only mkDefaults the official option.
  followAgentContainer = agent: {
    enable = mkDefault agent.container.enable;
    backend = mkDefault agent.container.backend;
    image = mkDefault agent.container.image;
  };

  # Back-compat name. New callers should pass the official agent.
  followComposerContainer = followAgentContainer;

  # Callers supply volumes / extraEnv / command / identity extras.
  # Returns { dockerEnable, unit, preStart, script, preStop, volumes, identityFile }.
  mkOciJail =
    {
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
      network ? "host",
      publish ? [ ],
    }:
    let
      entrypoint = mkSlimEntrypoint name;
      allVolumes = volumes ++ cfg.extraVolumes;
      containerBinPkg = if cfg.backend == "docker" then pkgs.docker else pkgs.podman;
      needsDocker = cfg.backend == "docker";
      identityFile = "${identityDir}/${name}";
      jailPublish = if publish != [ ] then publish else (cfg.publish or [ ]);
      resourceFlags =
        (lib.optional (cfg.memory != null) "--memory=${cfg.memory}")
        ++ (lib.optional (cfg.memorySwap != null) "--memory-swap=${cfg.memorySwap}")
        ++ (lib.optional (cfg.cpus != null) "--cpus=${toString cfg.cpus}")
        ++ (lib.optional (cfg.oomScoreAdj != null) "--oom-score-adj=${toString cfg.oomScoreAdj}")
        ++ (lib.optional (cfg.shmSize != null) "--shm-size=${cfg.shmSize}");
      effectiveExtraOptions = cfg.extraOptions ++ resourceFlags;
      fullIdentity = {
        inherit (cfg)
          image
          extraVolumes
          extraOptions
          memory
          memorySwap
          cpus
          oomScoreAdj
          shmSize
          ;
        inherit
          extraEnv
          entrypoint
          envFiles
          command
          forcedCreateArgs
          network
          ;
        publish = jailPublish;
        volumes = allVolumes;
      }
      // identity;
      scripts = mkUnitScripts {
        backend = cfg.backend;
        containerName = name;
        image = cfg.image;
        inherit
          user
          extraEnv
          envFiles
          entrypoint
          command
          identityFile
          network
          ;
        volumes = allVolumes;
        extraOptions = effectiveExtraOptions;
        identity = fullIdentity;
        publish = jailPublish;
      };
    in
    {
      dockerEnable = needsDocker;
      volumes = allVolumes;
      inherit (scripts)
        preStart
        script
        preStop
        identityFile
        ;
      unit = {
        inherit description wantedBy;
        after = [
          "network-online.target"
        ]
        ++ optional needsDocker "docker.service"
        ++ after;
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
