{ pkgs, pluginSources }:

# Four plugins used to be vendored here; they now live in their own repos and
# arrive as flake inputs. Their suites run from those pinned sources.
# tool-call-coherency is still an in-repo plugin.
pkgs.runCommand "hermes-pnp-plugin-tests"
  {
    nativeBuildInputs = [
      pkgs.python3
      pkgs.git
    ];
  }
  ''
    ( cd ${pluginSources.secret-handoff} && PYTHONPATH=. python3 -m unittest discover -s tests -v )
    ( cd ${pluginSources.model-picker} && PYTHONPATH=. python3 -m unittest discover -s tests -v )
    ( cd ${pluginSources.gbrain-retrieval-reflex} && PYTHONPATH=. python3 -m unittest discover -s tests -v )
    ( cd ${pluginSources.git-hook} && PYTHONPATH=. python3 -m unittest discover -s tests -v )
    ( cd ${../plugins/tool-call-coherency} && PYTHONPATH=. python3 -m unittest discover -s tests -v )
    touch $out
  ''
