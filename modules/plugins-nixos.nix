# NixOS plugin materialize. Not imported by Home Manager
# (systemd.services / tmpfiles do not exist there).
{
  config,
  lib,
  pkgs,
  ...
}:

let
  pnp = config.services.hermesPnP;
  install = pnp.pluginInstall;
  names = pnp.internal.pnpPluginNames;
  sources = pnp.internal.pnpPluginSources;
  dest = "${install.stateDir}/plugins";
  linkroot = "${install.stateDir}/.hermes/plugins";
in
{
  config = lib.mkIf (names != [ ]) {
    systemd.tmpfiles.rules = [
      "d ${dest} 2770 ${install.user} ${install.group} -"
      "d ${linkroot} 2770 ${install.user} ${install.group} -"
    ];

    systemd.services.hermes-agent-plugins = {
      description = "Materialize hermes-pnp plugins";
      wantedBy = [ "multi-user.target" ];
      before = [ "hermes-agent.service" ];
      requiredBy = [ "hermes-agent.service" ];

      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        User = install.user;
        Group = install.group;
      };

      script = ''
        set -euo pipefail
        mkdir -p ${lib.escapeShellArg dest} ${lib.escapeShellArg linkroot}
        ${lib.concatMapStrings (name: ''
          chmod -R u+w ${lib.escapeShellArg "${dest}/${name}"} 2>/dev/null || true
          rm -rf ${lib.escapeShellArg "${dest}/${name}"}
          ${pkgs.rsync}/bin/rsync -a --delete --chmod=D2770,F0640 \
            --exclude 'webui/' --exclude '__pycache__/' --exclude '*.pyc' \
            ${sources.${name}}/ ${lib.escapeShellArg "${dest}/${name}"}/
          ln -sfn "../../plugins/${name}" ${lib.escapeShellArg "${linkroot}/${name}"}
        '') names}
        printf '%s\n' ${lib.escapeShellArgs names} > ${lib.escapeShellArg "${dest}/.enabled"}
      '';
    };
  };
}
