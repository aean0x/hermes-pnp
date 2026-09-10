# Home Manager plugin materialize. Only PnP-owned trees — official
# extraPlugins already land as nix-managed-* in this same directory.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  pnp = config.services.hermesPnP;
  agent = config.services.hermes-agent;
  names = pnp.internal.pnpPluginNames;
  sources = pnp.internal.pnpPluginSources;
  dest = "${agent.hermesHome}/plugins";
in
{
  config = lib.mkIf (names != [ ]) {
    home.activation.hermesPnPPlugins = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
      set -euo pipefail
      $DRY_RUN_CMD mkdir -p ${lib.escapeShellArg dest}
      ${lib.concatMapStrings (name: ''
        $DRY_RUN_CMD chmod -R u+w ${lib.escapeShellArg "${dest}/${name}"} 2>/dev/null || true
        $DRY_RUN_CMD rm -rf ${lib.escapeShellArg "${dest}/${name}"}
        $DRY_RUN_CMD ${pkgs.rsync}/bin/rsync -a --delete --chmod=D0750,F0640 \
          --exclude 'webui/' --exclude '__pycache__/' --exclude '*.pyc' \
          ${sources.${name}}/ ${lib.escapeShellArg "${dest}/${name}"}/
      '') names}
      printf '%s\n' ${lib.escapeShellArgs names} | $DRY_RUN_CMD tee ${lib.escapeShellArg "${dest}/.enabled"} >/dev/null
    '';
  };
}
