#!/usr/bin/env bash
# Stop the OpenTelemetry Demo (same compose files as start_demo.sh).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
DEMO="${DEMO_DIR:-$HOME/opentelemetry-demo}"

cd "$DEMO"
make stop DOCKER_COMPOSE_FILES_EXTRAS="-f compose.extras.yaml -f $REPO/demo/compose.local.yaml"
