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

# Prefer a per-example data file (csv, parquet, db, sqlite); fall back to
# the shared fake-data CSV used by the vehicle_safety hello-world example.
DATA_FILE=""
for ext in csv parquet db sqlite; do
  if [[ -f "$EXAMPLE_DIR/data.$ext" ]]; then
    DATA_FILE="$EXAMPLE_DIR/data.$ext"
    break
  fi
done

if [[ -z "$DATA_FILE" ]]; then
  DATA_FILE="$ROOT/fake_data/sample.csv"
fi

if [[ ! -f "$DATA_FILE" ]]; then
  echo "error: no source data file found (tried $EXAMPLE_DIR/data.{csv,parquet,db,sqlite} and $ROOT/fake_data/sample.csv)" >&2
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

echo "==> load data → $PROJECT_DIR/data/$DB_NAME (table: records, pk: id)"
echo "    source: $DATA_FILE"
mkdir -p "$PROJECT_DIR/data"
rm -f "$PROJECT_DIR/data/$DB_NAME"
python "$ROOT/scripts/ingest_data.py" "$DATA_FILE" "$PROJECT_DIR/data/$DB_NAME"

echo "==> bootstrap auth (hash users.yaml → metadata.yml, seed users table)"
python "$ROOT/scripts/bootstrap_auth.py" "$EXAMPLE" "$PROJECT_DIR"

echo
echo "✓ generated: $PROJECT_DIR"
echo "  next: scripts/serve.sh $PROJECT_DIR"
echo
echo "  credentials (from examples/$EXAMPLE/users.yaml):"
python -c "
import yaml, sys
users = yaml.safe_load(open('$EXAMPLE_DIR/users.yaml'))['users']
for u in users:
    print(f'    {u[\"username\"]:12s}  {u[\"password\"]:16s}  {u[\"role\"]}')
"
