# Native Desktop. Official hermes-backend (serve) + wrapped launcher.
# Incompatible with the Ubuntu jail. Drop any hand-rolled hermes-serve.
#
# Import: inputs.hermes-pnp.nixosModules.default
{
  services.hermes-agent.enable = true;

  services.hermesPnP = {
    enable = true;
    # environmentFiles = [ config.sops.templates.hermesEnv.path ];

    desktop = {
      enable = true;
      users = [ "alice" ];
      # Raw token, one line. hermes:hermes 0440. Not a Nix path literal.
      sessionTokenFile = "/run/secrets/hermes-desktop-token";
      # host = "127.0.0.1";
      # port = 9119;
      # mode = "serve"; # or "dashboard"
    };
  };
}
