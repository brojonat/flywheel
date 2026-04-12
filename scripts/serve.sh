#!/usr/bin/env bash
# Start Datasette against a generated flywheel project.
#
# Usage: scripts/serve.sh <project_dir> [port]
set -euo pipefail

PROJECT_DIR="${1:?usage: serve.sh <project_dir> [port]}"
PORT="${2:-8001}"

if [[ ! -f "$PROJECT_DIR/flywheel.yaml" ]]; then
  echo "error: not a flywheel project (no flywheel.yaml): $PROJECT_DIR" >&2
  exit 1
fi
if [[ ! -f "$PROJECT_DIR/data/labeling.db" ]]; then
  echo "error: data/labeling.db missing — run scripts/generate.sh first" >&2
  exit 1
fi

cd "$PROJECT_DIR"
export FLYWHEEL_CONFIG="$PWD/flywheel.yaml"

METADATA_ARGS=()
if [[ -f "$PWD/metadata.yml" ]]; then
  METADATA_ARGS=(--metadata "$PWD/metadata.yml")
fi

echo "==> datasette serving on http://localhost:$PORT/flywheel"
echo "    log in at http://localhost:$PORT/-/login"
echo "    (Ctrl-C to stop)"
exec datasette serve data/labeling.db \
  --plugins-dir plugin \
  --port "$PORT" \
  "${METADATA_ARGS[@]}"
