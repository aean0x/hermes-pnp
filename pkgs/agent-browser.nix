{ lib, stdenv, fetchurl }:

# Pin the upstream musl-static release, not nixpkgs' 0.27.0.
# Stream keyboard input (captcha typing) was broken until 0.33.2.
# Static musl: no interpreter, no libc surprise on NixOS.
let
  version = "0.34.0";

  assets = {
    "x86_64-linux" = {
      url = "https://github.com/vercel-labs/agent-browser/releases/download/v${version}/agent-browser-linux-musl-x64";
      hash = "sha256-3UdSuh3vgcdENQTChLZVnSja2OzQK1+uymyvT8H7lI4=";
    };
    "aarch64-linux" = {
      url = "https://github.com/vercel-labs/agent-browser/releases/download/v${version}/agent-browser-linux-musl-arm64";
      hash = "sha256-wIZPsgbjIa9IpG+4MxzwiuYLP8wQRiMsHRyELbT8QMo=";
    };
  };

  asset =
    assets.${stdenv.hostPlatform.system}
      or (throw "agent-browser: no musl-static release for ${stdenv.hostPlatform.system}");
in
stdenv.mkDerivation {
  pname = "agent-browser";
  inherit version;

  src = fetchurl {
    inherit (asset) url hash;
  };

  dontUnpack = true;
  dontConfigure = true;
  dontBuild = true;
  # Fully static. Fixup/patchelf would only risk breaking it.
  dontFixup = true;

  installPhase = ''
    runHook preInstall
    install -Dm755 $src $out/bin/agent-browser
    runHook postInstall
  '';

  meta = {
    description = "Browser CLI with a human-in-the-loop dashboard (CDP attach + screencast)";
    homepage = "https://github.com/vercel-labs/agent-browser";
    changelog = "https://github.com/vercel-labs/agent-browser/releases/tag/v${version}";
    license = lib.licenses.asl20;
    sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
    mainProgram = "agent-browser";
    platforms = [
      "x86_64-linux"
      "aarch64-linux"
    ];
  };
}
