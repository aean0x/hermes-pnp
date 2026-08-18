# Persistent CDP browser + agent-browser dashboard gate.
# Agent talks to 127.0.0.1:9222. Humans use the Caddy URL, not :4848
# on the LAN. Engine follows package.meta.mainProgram.
#
# Import: inputs.hermes-pnp.nixosModules.browser
# (or nixosModules.default — on by default when the composer is on)
{ pkgs, ... }:

{
  services.hermes-agent.enable = true;

  services.hermesPnP = {
    enable = true;
    browser = {
      package = pkgs.brave;
      # engine = "brave"; # only if mainProgram is wrong
      cdpPort = 9222;
      # cdpAllowOrigins = [ "*" ]; # Chromium origins, not CIDR
      gate = {
        enable = true;
        listenAddress = "127.0.0.1";
        port = 4848;
        publicUrl = "https://browser.example.com/";
      };
      extraArgs = [ ];
    };
  };

  # Consumer Caddy (not shipped here):
  #   services.caddy.proxyServices."browser.example.com" = 4848;
}
