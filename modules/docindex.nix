# Document index (FTS5 + sqlite-vec) exposed as the `docindex` toolset.
# The engine ships in plugins/docindex; this module supplies its knobs and
# injects the plugin, so nothing has to be listed in services.hermesPnP.plugins.
# Off by default. Does not build an index: the corpus and the database stay
# host-local (they hold raw text of scans, so they are not in git).
{
  config,
  lib,
  pkgs,
  options,
  ...
}:

let
  inherit (lib)
    mapAttrs
    mkDefault
    mkEnableOption
    mkIf
    mkOption
    optionalAttrs
    types
    ;

  pnp = config.services.hermesPnP;
  cfg = pnp.docindex;
  gbrainCfg = if options.services.hermesPnP ? gbrain then pnp.gbrain else null;

  # Follow gbrain unless this module pins a value: the brain and the document
  # index share one vector space, and docvec.py reads these variables too.
  embeddingModel =
    if cfg.embeddingModel != null then
      cfg.embeddingModel
    else if gbrainCfg != null then
      gbrainCfg.embeddingModel
    else
      null;

  embeddingDimensions =
    if cfg.embeddingDimensions != null then
      cfg.embeddingDimensions
    else if gbrainCfg != null then
      gbrainCfg.embeddingDimensions
    else
      null;

  env =
    {
      DOCINDEX_DB = cfg.dbPath;
      DOCINDEX_WORKDIR = cfg.workDir;
      DOCINDEX_PYTHON = cfg.python;
      DOCVEC_PYTHON = cfg.vecPython;
      DOCINDEX_PATH_PREPEND = cfg.pathPrepend;
      DOCINDEX_WORKERS = toString cfg.workers;
      DOCINDEX_TIMEOUT = toString cfg.timeout;
    }
    // optionalAttrs (cfg.libstdcpp != null) {
      DOCINDEX_LIBSTDCPP = cfg.libstdcpp;
    }
    // optionalAttrs (cfg.roots != { }) {
      DOCINDEX_ROOTS = builtins.toJSON cfg.roots;
    }
    // optionalAttrs (cfg.verifyTermsFile != null) {
      DOCINDEX_VERIFY_TERMS_FILE = cfg.verifyTermsFile;
    }
    // optionalAttrs (embeddingModel != null) {
      GBRAIN_EMBEDDING_MODEL = embeddingModel;
    }
    // optionalAttrs (embeddingDimensions != null) {
      GBRAIN_EMBEDDING_DIMENSIONS = toString embeddingDimensions;
    };
in
{
  options.services.hermesPnP.docindex = {
    enable = mkEnableOption ''
      Document index tools: keyword + semantic search over the local document
      corpus (scanned post, letters, tax and insurance paperwork, project
      notes, the GBrain markdown mirror). Injects the `docindex` plugin
      (plugins/docindex) and exports its knobs to the agent, so plugin tools
      and the nightly refresh script read one configuration.

      The index itself stays host-local: this module never builds or moves it.
      Off by default.
    '';

    dbPath = mkOption {
      type = types.str;
      default = "/data/docindex/index.db";
      description = "Index database (`DOCINDEX_DB`).";
    };

    workDir = mkOption {
      type = types.str;
      default = "/tmp/docindex-work";
      description = "Scratch dir for page renders during OCR (`DOCINDEX_WORKDIR`).";
    };

    python = mkOption {
      type = types.str;
      default = "/data/toolbox/bin/python3";
      example = "/data/docindex/embed/venv/bin/python";
      description = ''
        Interpreter for the FTS/OCR CLI (`DOCINDEX_PYTHON`). It must carry the
        PDF and OCR dependencies; `pathPrepend` supplies tesseract and poppler.
      '';
    };

    vecPython = mkOption {
      type = types.str;
      default = "/data/docindex/embed/venv/bin/python";
      description = ''
        Interpreter for the vector CLI (`DOCVEC_PYTHON`). It must carry
        `sqlite_vec`; that extension also needs `libstdcpp` on
        LD_LIBRARY_PATH.
      '';
    };

    libstdcpp = mkOption {
      type = types.nullOr types.str;
      default = "${pkgs.stdenv.cc.cc.lib}/lib";
      description = ''
        libstdc++ dir prepended to LD_LIBRARY_PATH for the vector CLI
        (`DOCINDEX_LIBSTDCPP`). Nix supplies the store path, so a garbage
        collection of the compiler the venv was built against cannot strand
        the extension module. Null leaves LD_LIBRARY_PATH untouched.
      '';
    };

    pathPrepend = mkOption {
      type = types.str;
      default = "/data/toolbox/bin";
      description = ''
        Directory prepended to PATH for engine subprocesses
        (`DOCINDEX_PATH_PREPEND`): tesseract, pdftotext, pdfinfo.
      '';
    };

    workers = mkOption {
      type = types.ints.positive;
      default = 2;
      description = ''
        OCR/embed worker count (`DOCINDEX_WORKERS`). The usual bottleneck is
        I/O, not tesseract, and the box has one CPU: raising this mostly buys
        contention.
      '';
    };

    timeout = mkOption {
      type = types.ints.positive;
      default = 900;
      description = ''
        Per-tool-call timeout in seconds (`DOCINDEX_TIMEOUT`). The index tool
        may run up to four times this, capped at one hour.
      '';
    };

    roots = mkOption {
      type = types.attrsOf (types.listOf types.str);
      default = { };
      example = {
        "/data/workspace/onedrive" = [ ];
        "/data/.hermes/projects" = [ "node_modules" ];
      };
      description = ''
        Roots the `docindex_index` tool scans, as root -> directory names to
        exclude (`DOCINDEX_ROOTS`, JSON). Empty means the tool must be given
        roots explicitly. A root holding secrets or envs must not be listed:
        the index stores raw text and is readable by the agent.
      '';
    };

    verifyTermsFile = mkOption {
      type = types.nullOr types.str;
      default = null;
      example = "/data/docindex/verify-terms.json";
      description = ''
        JSON list of `[term, label]` pairs for `docindex_verify`
        (`DOCINDEX_VERIFY_TERMS_FILE`). Those terms are document identifiers,
        so they live in a host file, not in this repo. Null skips the
        search-sanity pass and keeps the coverage and status checks.
      '';
    };

    embeddingModel = mkOption {
      type = types.nullOr types.str;
      default = null;
      example = "openrouter:voyageai/voyage-4";
      description = ''
        Embedding model for the vector arm (`GBRAIN_EMBEDDING_MODEL`). Null
        follows `services.hermesPnP.gbrain.embeddingModel`, so one change moves
        both indexes.

        Re-pointing an index that already holds vectors is a re-embed, not a
        config flip: the corpus stays in the old model's space until it is
        rebuilt. For the document index that means dropping the vector tables
        and running a full embed pass.
      '';
    };

    embeddingDimensions = mkOption {
      type = types.nullOr types.ints.positive;
      default = null;
      example = 1024;
      description = ''
        Vector width for `embeddingModel` (`GBRAIN_EMBEDDING_DIMENSIONS`).
        Null follows `services.hermesPnP.gbrain.embeddingDimensions`.
      '';
    };
  };

  config = mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.embeddingDimensions == null || embeddingModel != null;
        message = "services.hermesPnP.docindex.embeddingDimensions requires an embeddingModel (set it here or on services.hermesPnP.gbrain).";
      }
    ];

    # mkDefault per variable: the operator can override any knob at the
    # services.hermes-agent level without this module fighting back.
    services.hermes-agent.environment = mapAttrs (_: mkDefault) env;
  };
}
