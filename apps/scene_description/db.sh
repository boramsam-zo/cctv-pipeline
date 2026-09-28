#!/bin/bash
set -euo pipefail
export SCENE_APP_DIR
SCENE_APP_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
base=${CCTV_COMPOSE_FILE:-/opt/cctv-stack/compose.yaml}
command=${1:-help}
case "$command" in
  schema)
    docker compose -f "$base" exec -T --user postgres db \
      psql -X -v ON_ERROR_STOP=1 -d cctv < "$SCENE_APP_DIR/schema.sql"
    ;;
  load|show)
    : "${2:?Pass the existing result.json path}"
    export SCENE_RESULT_FILE
    SCENE_RESULT_FILE=$(python3 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve(strict=True))' "$2")
    docker compose -f "$base" -f "$SCENE_APP_DIR/compose.tools.yaml" --profile tools \
      run --rm -T --no-deps scene-db "$command"
    ;;
  *) echo 'Usage: db.sh schema | {load|show} /absolute/path/result.json'; exit 2 ;;
esac
