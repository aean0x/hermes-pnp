# Optional hermes-context-manager. Pins upstream as extraPluginDirs and
# creates $stateDir/.hermes/hmc_state. Native compact stays the LLM
# summarizer; HMC does cheap per-tool work only.
#
# Import: inputs.hermes-pnp.nixosModules.default
# `nix flake check` force-disables this (the pin fetches GitHub).
{
  services.hermes-agent.enable = true;

  services.hermesPnP = {
    enable = true;
    hmc = {
      enable = true;
      compressPercent = 0.30;
      # src.rev / src.hash — bump when you want a newer pin.
    };
  };
}
