#!/usr/bin/env bash
# Start the OpenTelemetry Demo with my changes (demo/compose.local.yaml) loaded last.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
DEMO="${DEMO_DIR:-$HOME/opentelemetry-demo}"

cd "$DEMO"
make start DOCKER_COMPOSE_FILES_EXTRAS="-f compose.extras.yaml -f $REPO/demo/compose.local.yaml"
