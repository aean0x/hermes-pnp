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

  # Official option — do not wrap. Extra binds for the gateway jail.
  services.hermes-agent.container.extraVolumes = [
    # "/home/alice/src:/data/src"
  ];

  # Independent of the agent list. Terminals spawned from the WebUI
  # see only these binds (+ /nix/store, state, CA farm).
  services.hermesPnP.webui.container.extraVolumes = [
    # "/home/alice/src:/data/src"
  ];
}
