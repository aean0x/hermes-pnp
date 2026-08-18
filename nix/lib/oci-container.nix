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
{ pkgs, lib }:

let
  inherit (lib) escapeShellArg concatMapStringsSep concatStringsSep mapAttrsToList;
in
{
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
}
