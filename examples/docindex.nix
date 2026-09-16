# Document index toolset (FTS5 + sqlite-vec over the local document corpus).
# Engine ships in plugins/docindex; this hook injects the plugin and exports
# its knobs. The index database and the corpus stay host-local.
#
# The vector space follows services.hermesPnP.gbrain: docindex.embeddingModel
# and embeddingDimensions default to the gbrain values, so the brain and the
# document index cannot drift into different spaces.
#
# Import: inputs.hermes-pnp.nixosModules.default
{
  services.hermes-agent.enable = true;

  services.hermesPnP = {
    enable = true;
    docindex.enable = true;
    # docindex.dbPath = "/data/docindex/index.db";
    # docindex.python = "/data/toolbox/bin/python3";
    # docindex.vecPython = "/data/docindex/embed/venv/bin/python";
    # docindex.workers = 2;
    # docindex.timeout = 900;
    # docindex.roots = { "/data/workspace/onedrive" = [ ]; };
    # docindex.verifyTermsFile = "/data/docindex/verify-terms.json";
    # docindex.embeddingModel = "openrouter:voyageai/voyage-4";
    # docindex.embeddingDimensions = 1024;
  };
}
