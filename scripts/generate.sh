#!/usr/bin/env bash
# Generate a flywheel project from an example into _tmp_output/<example>/
# and load the shared fake CSV into its SQLite file.
#
# Usage: scripts/generate.sh <example_name>
#   e.g. scripts/generate.sh vehicle_safety
set -euo pipefail

EXAMPLE="${1:-vehicle_safety}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${OUT_DIR:-$ROOT/_tmp_output}"
PROJECT_DIR="$OUT_DIR/$EXAMPLE"
EXAMPLE_DIR="$ROOT/examples/$EXAMPLE"
DB_NAME="labeling.db"

if [[ ! -d "$EXAMPLE_DIR" ]]; then
  echo "error: example dir not found: $EXAMPLE_DIR" >&2
  exit 1
fi

# Prefer a per-example CSV (examples/<slug>/data.csv); fall back to the
# shared fake-data CSV used by the vehicle_safety hello-world example.
if [[ -f "$EXAMPLE_DIR/data.csv" ]]; then
  CSV="$EXAMPLE_DIR/data.csv"
else
  CSV="$ROOT/fake_data/sample.csv"
fi

if [[ ! -f "$CSV" ]]; then
  echo "error: no source CSV found (tried $EXAMPLE_DIR/data.csv and $ROOT/fake_data/sample.csv)" >&2
  echo "       run 'make data' to produce the default fake dataset" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"
rm -rf "$PROJECT_DIR"

PROJECT_NAME="$(grep -m1 '^  name:' "$EXAMPLE_DIR/flywheel.yaml" | sed 's/^  name: //')"
LABELERS_PER_RECORD="$(grep -m1 '  min_labelers:' "$EXAMPLE_DIR/flywheel.yaml" | awk '{print $2}')"
LABELERS_PER_RECORD="${LABELERS_PER_RECORD:-2}"

echo "==> cookiecutter scaffold → $PROJECT_DIR"
echo "    project_name=$PROJECT_NAME labelers_per_record=$LABELERS_PER_RECORD"
cd "$ROOT"
cookiecutter . \
  --no-input \
  --output-dir "$OUT_DIR" \
  --overwrite-if-exists \
  "project_slug=$EXAMPLE" \
  "project_name=$PROJECT_NAME" \
  "labelers_per_record=$LABELERS_PER_RECORD"

echo "==> copy example flywheel.yaml"
cp "$EXAMPLE_DIR/flywheel.yaml" "$PROJECT_DIR/flywheel.yaml"
if [[ -d "$EXAMPLE_DIR/choices" ]]; then
  cp -r "$EXAMPLE_DIR/choices" "$PROJECT_DIR/choices"
fi

echo "==> load fake CSV → $PROJECT_DIR/data/$DB_NAME (table: records, pk: id)"
mkdir -p "$PROJECT_DIR/data"
rm -f "$PROJECT_DIR/data/$DB_NAME"
sqlite-utils insert "$PROJECT_DIR/data/$DB_NAME" records "$CSV" --csv --detect-types --pk=id

echo "==> bootstrap auth (hash users.yaml → metadata.yml, seed users table)"
python "$ROOT/scripts/bootstrap_auth.py" "$EXAMPLE" "$PROJECT_DIR"

echo
echo "✓ generated: $PROJECT_DIR"
echo "  next: scripts/serve.sh $PROJECT_DIR"
