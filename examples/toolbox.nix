# Everyday CLI buildEnv on the agent PATH
# (host: /var/lib/hermes/toolbox/bin, jail: /data/toolbox/bin).
# Defaults already include git, curl, jq, ripgrep, python3, …
# Prefer official extraPackages — folded into this env (native + jail).
# toolbox.extraPackages remains an append-only alias.
#
# Import: inputs.hermes-pnp.nixosModules.toolbox
# (or nixosModules.default — on by default when the composer is on)
{ pkgs, ... }:

{
  services.hermes-agent.enable = true;
  # services.hermes-agent.extraPackages = [ pkgs.sops ];

  services.hermesPnP = {
    enable = true;
    toolbox = {
      enable = true;
      extraPackages = [
        pkgs.sops
      ];
      # pythonPackages = ps: with ps; [ requests pyyaml toml ];
    };
  };
}
