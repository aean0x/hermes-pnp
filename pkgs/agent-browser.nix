{ lib, stdenv, fetchurl }:

# Upstream musl-static release, not nixpkgs' 0.27.0. The browser tool and
# the vault supervisor exec this CLI; streamed keyboard input was broken
# until 0.33.2. Static: no interpreter, no libc surprise, and it runs
# unchanged in the Ubuntu jail.
let
  version = "0.39.1";

  assets = {
    "x86_64-linux" = {
      url = "https://github.com/vercel-labs/agent-browser/releases/download/v${version}/agent-browser-linux-musl-x64";
      hash = "sha256-dFB82rmKqRvcFDRsVFr/l0ZAoiXkP4w9bCMWT4wPm7k=";
    };
    "aarch64-linux" = {
      url = "https://github.com/vercel-labs/agent-browser/releases/download/v${version}/agent-browser-linux-musl-arm64";
      hash = "sha256-xJWfPhk8W8jzM2EzY/JfLqzuyv4loIIYQ7JxkGR0og8=";
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
    description = "Browser CLI that drives a Chromium-family engine over CDP";
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
