{ pkgs }:

pkgs.runCommand "mcp-proxy-tests"
  {
    nativeBuildInputs = [
      pkgs.python3
      pkgs.git
    ];
  }
  ''
    export PYTHONPATH=${../services/mcp-proxy/src}
    python3 -m unittest discover -s ${../services/mcp-proxy/tests} -v
    touch $out
  ''
