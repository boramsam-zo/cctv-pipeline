#!/bin/bash
set -euo pipefail
app_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export RAG_APP_DIR="$app_dir"
: "${CCTV_COMPOSE_FILE:?Set the DB compose.yaml path}"
: "${CCTV_INPUT_DIR:?Set the directory containing chunks.jsonl}"
compose=(docker compose -f "$CCTV_COMPOSE_FILE" -f "$app_dir/compose.tools.yaml" --profile tools)
command=${1:-help}
shift || true
case "$command" in
  build) "${compose[@]}" build rag ;;
  schema)
    for file in schema.sql grants.sql; do
      "${compose[@]}" exec -T --user postgres db psql -X -v ON_ERROR_STOP=1 -d cctv < "$app_dir/$file"
    done
    ;;
  check|embed|load|search) "${compose[@]}" run --rm -T rag "$command" "$@" ;;
  *) echo 'Usage: compose.sh {build|schema|check|embed|load|search} [CLI arguments]'; exit 2 ;;
esac
