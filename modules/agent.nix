# Pair plugin dest with the official agent identity.
# First-party plugins materialize to $stateDir/plugins/<name>.
# services.hermes-agent.extraPlugins remains the official listOf package path.
{
  config,
  lib,
  options,
  ...
}:

let
  inherit (lib) mkDefault mkIf;

  inherit (import ../lib { inherit lib; }) agentContainerNetwork remapStatePath;

  pnp = config.services.hermesPnP;
  agent = config.services.hermes-agent;
  containerNetwork = agentContainerNetwork options config;
in
{
  config = lib.mkMerge [
    (mkIf pnp.enable {
      services.hermes-agent.environmentFiles = pnp.environmentFiles;
      # Official host CLI pairing: PATH + HERMES_HOME for interactive shells.
      services.hermes-agent.addToSystemPackages = mkDefault true;
      services.hermesPnP.pluginInstall.stateDir = mkDefault agent.stateDir;
      services.hermesPnP.pluginInstall.user = mkDefault agent.user;
      services.hermesPnP.pluginInstall.group = mkDefault agent.group;
    })
    (mkIf (pnp.enable && pnp.workspace != null) {
      # Plain value, not mkDefault: settings is deepConfigType, where mkDefault
      # is stored literally and never merges. The workspace option is the single
      # knob, so a literal value is correct here.
      services.hermes-agent.settings.terminal.cwd = remapStatePath {
        inherit (agent) stateDir;
        path = pnp.workspace;
      };
    })
    (mkIf pnp.container.enable {
      services.hermes-agent.container.enable = mkDefault true;
      services.hermes-agent.container.backend = mkDefault pnp.container.backend;
      services.hermes-agent.container.image = mkDefault pnp.container.image;
    })
    (mkIf (pnp.enable && agent.container.enable && agent.container.hostUsers == [ ]) {
      warnings = [
        ''
          services.hermesPnP: container mode is on but
          services.hermes-agent.container.hostUsers is empty. Set it to
          the interactive login so ~/.hermes shares the jail state
          (official pairing).
        ''
      ];
    })
    (mkIf (pnp.enable && agent.container.enable && containerNetwork != "host") {
      warnings = [
        ''
          services.hermesPnP: container.network is "${containerNetwork}", not "host".
          Composer pairing still uses 127.0.0.1 for WebUI, CDP (:9222),
          GBrain, and mcp-proxy. All OCI jails join that same network so
          they can leave host net together; remap those URLs (or stay on
          host) before switching.
        ''
      ];
    })
  ];
}
