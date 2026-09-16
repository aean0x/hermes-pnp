#!/bin/bash
# Full index run: scan OneDrive tree, then extract/OCR everything pending. Resumable.
# Paths come from the docindex env with the historical host values as defaults.
export PATH="${DOCINDEX_PATH_PREPEND:-/data/toolbox/bin}:$PATH"
PY="${DOCINDEX_PYTHON:-/data/toolbox/bin/python3}"
BIN=$(cd "$(dirname "$0")" && pwd)
DB="${DOCINDEX_DB:-/data/docindex/index.db}"
ROOT="${1:-${DOCINDEX_ROOT:-/data/workspace/onedrive}}"
LOG="${DOCINDEX_LOG:-/data/docindex/run.log}"
echo "=== RUN START $(date -Is) ===" >> "$LOG"
$PY "$BIN/docindex.py" --db "$DB" scan "$ROOT" >> "$LOG" 2>&1
echo "=== SCAN DONE $(date -Is) ===" >> "$LOG"
$PY "$BIN/docindex.py" --db "$DB" stats >> "$LOG" 2>&1
$PY "$BIN/docindex.py" --db "$DB" extract --workers "${DOCINDEX_WORKERS:-2}" >> "$LOG" 2>&1
echo "=== EXTRACT DONE $(date -Is) ===" >> "$LOG"
$PY "$BIN/docindex.py" --db "$DB" stats >> "$LOG" 2>&1
