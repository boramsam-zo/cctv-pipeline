#!/bin/bash
set -euo pipefail
export BE_APP_DIR
BE_APP_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export BE_SCENE_APP_DIR="$BE_APP_DIR/../scene_description"
export BE_CONTAINER_UID="${BE_CONTAINER_UID:-$(id -u)}"
export BE_CONTAINER_GID="${BE_CONTAINER_GID:-$(id -g)}"
: "${CCTV_COMPOSE_FILE:?Set the existing DB compose.yaml path}"
command=${1:-help}
shift || true
if [ "$command" = schema ] || [ "$command" = scene-schema ]; then
  schema_path="$BE_APP_DIR/queue_schema.sql"
  if [ "$command" = scene-schema ]; then schema_path="$BE_SCENE_APP_DIR/schema.sql"; fi
  docker compose -f "$CCTV_COMPOSE_FILE" exec -T --user postgres db \
    psql -X -v ON_ERROR_STOP=1 -d cctv < "$schema_path"
  exit
fi
compose=(docker compose -f "$CCTV_COMPOSE_FILE" -f "$BE_APP_DIR/queue.compose.yaml" --profile be-tools)
case "$command" in
  service-build|service-start|service-status)
    service=(docker compose -f "$CCTV_COMPOSE_FILE" -f "$BE_APP_DIR/queue.compose.yaml" -f "$BE_APP_DIR/service.compose.yaml" --profile cctv-ui)
    if [ "$command" = service-build ]; then "${service[@]}" build cctv-ui
    elif [ "$command" = service-start ]; then "${service[@]}" up -d --no-deps be-api be-worker cctv-ui
    else "${service[@]}" ps be-api be-worker cctv-ui; fi ;;
  build) "${compose[@]}" build be-queue ;;
  enqueue|list|collect|recover-s3|scene-fetch) "${compose[@]}" run --rm -T --no-deps be-queue "$command" "$@" ;;
  scene-report|scene-describe|scene-diagnose|scene-probe)
    : "${BE_SECRETS_DIR:?Set the private queue secret directory}"
    if [ -s "$BE_SECRETS_DIR/gemini_api_key" ]; then
      "${compose[@]}" run --rm -T --no-deps \
        --volume "$BE_SECRETS_DIR/gemini_api_key:/run/secrets/be_gemini_api_key:ro" \
        be-queue "$command" --key-file /run/secrets/be_gemini_api_key "$@"
    else
      # DB-only recovery of an existing result works without a model key.
      "${compose[@]}" run --rm -T --no-deps be-queue "$command" "$@"
    fi
    ;;
  dispatch|recover|retarget) "${compose[@]}" run --rm -T --no-deps be-queue "$command" --token-file /run/secrets/runpod_worker_token "$@" ;;
  *) echo 'Usage: bash queue.sh {schema|scene-schema|build|enqueue|list|dispatch|collect|recover|recover-s3|retarget|scene-fetch|scene-describe|scene-report|scene-diagnose|scene-probe} [arguments]'; exit 2 ;;
esac
