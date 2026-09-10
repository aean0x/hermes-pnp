# Home Manager. Official user units + ~/.hermes.
# Import: inputs.hermes-pnp.homeManagerModules.default
#
# Do not set hermesHome or workingDirectory — upstream defaults apply.
# A hosted Desktop client that is not using this shortcut sets
# HERMES_DESKTOP_REMOTE_URL on the official module.
{ config, ... }:
{
  services.hermesPnP = {
    enable = true;
    # environmentFiles = [ config.sops.secrets."hermes/env".path ];
    # pythonExtras = [ "google-cloud-pubsub" ]; # sealed venv; see examples/python-extras.nix

    desktop = {
      enable = true;
      sessionTokenFile = "${config.home.homeDirectory}/.hermes/desktop-token";
      # host = "127.0.0.1";
      # port = 9119;
      # mode = "serve"; # or "dashboard"
    };
  };

  # services.hermes-agent.extraDependencyGroups = [ "google" "messaging" ];
  # services.hermes-agent.extraPackages = [ pkgs.sops ];
}
