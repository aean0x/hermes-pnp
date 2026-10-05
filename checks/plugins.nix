{ pkgs, pluginSources }:

# Plugins that carry a catalog entry upstream (git-hook, secret-handoff,
# gbrain-retrieval-reflex) are delivered by `hermes plugins install`, so their
# suites run in their own repos' CI instead of here. This check covers the
# plugin still pinned as a source tree plus the in-repo trees.
pkgs.runCommand "hermes-pnp-plugin-tests"
  {
    nativeBuildInputs = [
      pkgs.python3
      pkgs.git
    ];
  }
  ''
    ( cd ${pluginSources.model-picker} && PYTHONPATH=. python3 -m unittest discover -s tests -v )
    ( cd ${../plugins/tool-call-coherency} && PYTHONPATH=. python3 -m unittest discover -s tests -v )
    ( cd ${../plugins/browser-lease} && PYTHONPATH=. python3 -m unittest discover -s tests -t . -v )
    ( cd ${../plugins/refusal-advice} && PYTHONPATH=. python3 -m unittest discover -s tests -t . -v )
    touch $out
  ''
