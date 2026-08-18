{ pkgs }:

pkgs.runCommand "mcp-proxy-tests"
  {
    nativeBuildInputs = [
      pkgs.python3
      pkgs.git
    ];
  }
  ''
    export PYTHONPATH=${../pkgs/mcp-proxy/src}
    python3 -m unittest discover -s ${../pkgs/mcp-proxy/tests} -v
    touch $out
  ''
