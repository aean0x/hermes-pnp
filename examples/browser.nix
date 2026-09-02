# Persistent CDP browser + browser-ui cast gate.
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
      # Build-time auth import: seed the sticky profile with cookies /
      # saved logins / preferences from a Chromium user-data dir on the
      # BUILD machine. Reads an absolute path at eval, so switch with
      # `nixos-rebuild switch --impure` (or pass a store path from a
      # flake input for a pure build). One-shot: applied only when the
      # profile is empty (overwrite=true replaces the whole profile).
      # profileImport = {
      #   enable = true;
      #   source = "/home/alice/.config/BraveSoftware/Brave-Browser";
      #   # profileName = "Default"; # profile dir inside source
      #   # overwrite = false;
      # };
    };
  };

  # Consumer Caddy (not shipped here):
  #   services.caddy.proxyServices."browser.example.com" = 4848;
}
