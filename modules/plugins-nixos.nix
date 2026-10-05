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
  agent = config.services.hermes-agent;
  install = pnp.pluginInstall;
  names = pnp.internal.pnpPluginNames;
  sources = pnp.internal.pnpPluginSources;
  catalogNames = pnp.catalogInstall;
  hermesBin = "${agent.package}/bin/hermes";
  dest = "${install.stateDir}/plugins";
  linkroot = "${install.stateDir}/.hermes/plugins";
in
{
  config = lib.mkIf (names != [ ] || catalogNames != [ ]) {
    systemd.tmpfiles.rules = [
      "d ${dest} 2770 ${install.user} ${install.group} -"
      "d ${linkroot} 2770 ${install.user} ${install.group} -"
    ];

    systemd.services.hermes-agent-plugins = {
      description = "Materialize hermes-pnp plugins";
      wantedBy = [ "multi-user.target" ];
      before = [ "hermes-agent.service" ];
      requiredBy = [ "hermes-agent.service" ];

      # The catalog installer shells out to git; the script uses timeout/rm.
      path = [
        pkgs.coreutils
        pkgs.git
        pkgs.rsync
      ];

      environment.HERMES_HOME = "${install.stateDir}/.hermes";

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

        ${lib.concatMapStrings (name: ''
          # Catalog-delivered plugin: drop what this module used to materialize
          # (a catalog install is a git clone, so it survives), then install it
          # when it is missing. Best effort on purpose: a boot without network
          # must not hold up hermes-agent.
          if [ -L ${lib.escapeShellArg "${linkroot}/${name}"} ]; then
            rm -f ${lib.escapeShellArg "${linkroot}/${name}"}
          fi
          if [ ! -d ${lib.escapeShellArg "${dest}/${name}/.git"} ]; then
            rm -rf ${lib.escapeShellArg "${dest}/${name}"}
          fi
          if [ ! -e ${lib.escapeShellArg "${linkroot}/${name}"}/plugin.yaml ]; then
            timeout 300 ${hermesBin} plugins install ${lib.escapeShellArg name} --enable \
              </dev/null >/dev/null 2>&1 || true
          fi
        '') catalogNames}
      '';
    };
  };
}
