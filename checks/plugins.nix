{ pkgs }:

pkgs.runCommand "hermes-pnp-plugin-tests"
  {
    nativeBuildInputs = [
      pkgs.python3
      pkgs.git
    ];
  }
  ''
    ( cd ${../plugins/secret-handoff} && PYTHONPATH=. python3 -m unittest discover -s tests -v )
    ( cd ${../plugins/model-router} && PYTHONPATH=. python3 -m unittest discover -s tests -v )
    ( cd ${../plugins/git-hook} && PYTHONPATH=. python3 -m unittest discover -s tests -v )
    touch $out
  ''
