# Collision-safe extras for the sealed Hermes venv.
# Names are python312Packages attrs from the hermes-agent flake's
# nixpkgs — not pkgs.python312Packages on the host.
#
# Import: inputs.hermes-pnp.nixosModules.default
{
  services.hermes-agent.enable = true;

  # Pyproject extras (google-api-python-client, google-auth, …) still
  # belong on the official uv2nix channel:
  # services.hermes-agent.extraDependencyGroups = [ "google" ];

  services.hermesPnP = {
    enable = true;
    pythonExtras = [ "google-cloud-pubsub" ];
  };
}
