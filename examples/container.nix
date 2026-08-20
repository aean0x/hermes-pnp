# Official agent container + composer WebUI/browser jails.
#
# Import: inputs.hermes-pnp.nixosModules.default
{
  services.hermes-agent.enable = true;

  services.hermesPnP = {
    enable = true;
    container.enable = true;
    # container.backend = "docker";
    # container.image = "ubuntu:24.04";
  };

  # Official pairing: host CLI shares ~/.hermes with the jail.
  # services.hermes-agent.container.hostUsers = [ "alice" ];
  # Official extraPackages fold into the toolbox (native + jail).
  # services.hermes-agent.extraPackages = [ pkgs.sops ];

  # Official option — do not wrap. Extra binds for the gateway jail.
  services.hermes-agent.container.extraVolumes = [
    # "/home/alice/src:/data/src"
  ];

  # Independent of the agent list. Terminals spawned from the WebUI
  # see only these binds (+ /nix/store, state, CA farm).
  services.hermesPnP.webui.container.extraVolumes = [
    # "/home/alice/src:/data/src"
  ];

  # Typed resource grants. Prefer these over extraOptions RAM flags.
  # services.hermesPnP.webui.container.memory = "2g";
  # services.hermesPnP.webui.container.cpus = 2;
  # services.hermesPnP.browser.container.memory = "1g";
  # services.hermesPnP.browser.container.shmSize = "256m";

  # services.hermesPnP.admin.enable = true;
  # services.hermesPnP.browser.maxTabs = 2;
}
