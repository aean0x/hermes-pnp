# docindex

Hybrid keyword + semantic search over the local document index: scanned post,
letters, tax and insurance paperwork, contracts, project notes and the GBrain
markdown mirror. One SQLite file, no resident service.

- Index: `DOCINDEX_DB` (default `/data/docindex/index.db`), FTS5 + sqlite-vec.
- Engine: `bin/docindex.py` (FTS5 + OCR pipeline), `bin/docvec.py` (vector arm).
- Tools: `docindex_search`, `docindex_stats`, `docindex_show`, `docindex_index`,
  `docindex_verify`.

## Why the engine runs as a subprocess

`sqlite-vec`, tesseract and poppler live in interpreters the agent runtime does
not carry, so each tool shells out to the engine under a configured
interpreter. Nothing here loads the index in-process.

| Env | Meaning | Default |
|---|---|---|
| `DOCINDEX_DB` | index database | `/data/docindex/index.db` |
| `DOCINDEX_PYTHON` | interpreter for the FTS/OCR CLI | `/data/toolbox/bin/python3` |
| `DOCVEC_PYTHON` | interpreter carrying sqlite-vec | `/data/docindex/embed/venv/bin/python` |
| `DOCINDEX_LIBSTDCPP` | libstdc++ dir for the venv's extension module | unset |
| `DOCINDEX_PATH_PREPEND` | bin dir prepended to PATH (tesseract, pdftotext) | `/data/toolbox/bin` |
| `DOCINDEX_ROOTS` | JSON `{"root": ["exclude", ...]}` for `docindex_index` | unset |
| `DOCINDEX_WORKERS` | OCR worker count | `2` (the box has one CPU) |
| `DOCINDEX_TIMEOUT` | per-call timeout, seconds | `900` |
| `DOCINDEX_VERIFY_TERMS_FILE` | JSON sanity pairs for `docindex_verify` | unset |

`services.hermesPnP.docindex` sets all of them, and its `embeddingModel` /
`embeddingDimensions` default to the `gbrain` values, so the brain and this
index stay in one vector space.

## Vector space

Model and width are not pinned in code. `bin/docvec.py` resolves
`DOCVEC_MODEL`/`DOCVEC_DIMS` > `GBRAIN_EMBEDDING_MODEL`/`GBRAIN_EMBEDDING_DIMENSIONS`
> `~/.gbrain/config.json` > `voyageai/voyage-4` @ 1024, and `docvec stats`
prints which source won. A GBrain target on a provider this tool cannot call
exits 1 instead of embedding into a different space.

## Full-corpus refresh

Nightly, outside this plugin: the `docindex-refresh` cron script scans the
configured roots, extracts/OCRs the queue, then embeds the vector delta and
prints nothing when healthy. `docindex_index` is for targeted runs only.
