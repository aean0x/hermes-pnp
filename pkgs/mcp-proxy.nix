{
  lib,
  python3,
  writeShellApplication,
}:
let
  src = lib.cleanSource ../services/mcp-proxy/src;
in
writeShellApplication {
  name = "mcp-proxy";
  runtimeInputs = [ python3 ];
  text = ''
    export PYTHONPATH=${src}
    exec python3 -m mcp_proxy "$@"
  '';
}
