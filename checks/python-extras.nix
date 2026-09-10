{ pkgs }:

pkgs.runCommand "hermes-pnp-python-extras-filter-tests"
  {
    nativeBuildInputs = [ pkgs.python3 ];
  }
  ''
    PYTHONPATH=${../modules} python3 -m unittest discover -s ${../modules/tests} -v
    touch $out
  ''
