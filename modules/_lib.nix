# One-hop import of flake lib from any modules/*.nix.
{ pkgs, lib }:
import ../lib { inherit pkgs lib; }
