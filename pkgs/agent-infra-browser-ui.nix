# Vendored @agent-infra/browser-ui bundle (web-infra-dev/agent-browser-sdk).
# It is a self-contained UMD bundle that casts a CDP browser and lets a
# human drive it (tabs, nav, mouse/keyboard, dialogs, clipboard) over a
# browser-level WebSocket endpoint. No build step, no npm at runtime.
#
# The npm tarball ships the prebuilt dist/bundle/index.js; the repo does
# not commit dist/. Pin the tarball like any binary release. Extract the
# single file explicitly (the tarball's top level is `package/`, so the
# auto sourceRoot would strip it and the relative path would shift).
{
  lib,
  stdenv,
  fetchurl,
}:

let
  version = "0.2.2";
in
stdenv.mkDerivation {
  pname = "agent-infra-browser-ui";
  inherit version;

  src = fetchurl {
    url = "https://registry.npmjs.org/@agent-infra/browser-ui/-/browser-ui-${version}.tgz";
    hash = "sha256-cWa6F5SnUboJJ5uizHkDNAZBst1T1zuUuPT+M35k/XM=";
  };

  dontUnpack = true;
  dontConfigure = true;
  dontBuild = true;

  installPhase = ''
    runHook preInstall
    install -d "$out/share/browser-ui"
    tar -xzf "$src" -C "$out/share/browser-ui" package/dist/bundle/index.js
    mv "$out/share/browser-ui/package/dist/bundle/index.js" "$out/share/browser-ui/index.js"
    rm -rf "$out/share/browser-ui/package"
    runHook postInstall
  '';

  meta = with lib; {
    description = "Agent Infra Browser UI — CDP-based remote browser casting web component";
    homepage = "https://github.com/web-infra-dev/agent-browser-sdk";
    changelog = "https://github.com/web-infra-dev/agent-browser-sdk/blob/main/packages/browser-ui/CHANGELOG.md";
    license = licenses.asl20;
    sourceProvenance = [ sourceTypes.binaryNativeCode ];
    platforms = [
      "x86_64-linux"
      "aarch64-linux"
    ];
  };
}
