# Home Manager composer. Official identity: the login user and
# upstream hermesHome (~/.hermes). No NixOS jails, WebUI, toolbox,
# or system hermes user.
{
  imports = [
    ../enable.nix
    ../models.nix
    ../package.nix
    ../plugins.nix
    ./plugins.nix
    ../skills.nix
    ./skills.nix
    ../gbrain.nix
    ./gbrain.nix
    ../hmc.nix
    ./hmc.nix
    ./pairing.nix
  ];
}
