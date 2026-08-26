{ pkgs }:

pkgs.runCommand "hermes-github-token-tests"
  {
    nativeBuildInputs = [
      pkgs.bash
      pkgs.coreutils
      pkgs.gnugrep
      pkgs.gnused
    ];
  }
  ''
    script=${../scripts/hermes-github-token}
    tmp=$(mktemp -d)
    export HOME="$tmp"
    unset GITHUB_TOKEN GH_TOKEN HERMES_HOME || true

    echo 'GITHUB_TOKEN=from-home-env' > "$HOME/.env"
    got=$(bash "$script")
    test "$got" = "from-home-env"

    export GITHUB_TOKEN=from-env
    got=$(bash "$script")
    test "$got" = "from-env"
    unset GITHUB_TOKEN

    rm -f "$HOME/.env"
    mkdir -p "$HOME/.hermes"
    echo 'GH_TOKEN=from-gh-file' > "$HOME/.hermes/.env"
    got=$(bash "$script")
    test "$got" = "from-gh-file"

    rm -rf "$HOME/.hermes"
    if bash "$script"; then
      echo "expected fail-open exit 1 with no token" >&2
      exit 1
    fi

    touch $out
  ''
