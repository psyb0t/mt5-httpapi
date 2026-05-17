#!/usr/bin/env bash
# Run the live-API integration suite against the MT5 HTTP API URL in .env.
#
# Two modes:
#   ./run.sh           # host-side: uses local python + pip install + pytest
#   ./run.sh --docker  # builds a small image and runs --network=host so
#                      # Tailscale-routed hostnames (*.ts.51k.eu) resolve
set -euo pipefail

cd "$(dirname "$0")/../.."

if [[ ! -f tests/real/.env ]]; then
    echo "error: tests/real/.env not found" >&2
    echo "copy tests/real/.env.example to tests/real/.env and fill in MT5_API_TOKEN" >&2
    exit 1
fi

MODE="local"
PYTEST_ARGS=()
for arg in "$@"; do
    case "$arg" in
        --docker) MODE="docker" ;;
        *) PYTEST_ARGS+=("$arg") ;;
    esac
done

if [[ "$MODE" == "docker" ]]; then
    IMG=mt5-httpapi-real-tests
    docker build -f - -t "$IMG" . <<'DOCKERFILE'
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir pytest requests
COPY tests/ tests/
ENV PYTHONPATH=/app
ENTRYPOINT ["pytest", "-v", "tests/real/"]
DOCKERFILE
    # --network=host so Tailscale magic-DNS hostnames resolve against the
    # host's tailscaled. Bind-mount .env so the token never bakes into the
    # image layer cache.
    exec docker run --rm \
        --network=host \
        -v "$PWD/tests/real/.env:/app/tests/real/.env:ro" \
        "$IMG" "${PYTEST_ARGS[@]}"
fi

# Local mode
if ! python -c "import pytest, requests" 2>/dev/null; then
    echo "installing pytest + requests..."
    pip install --quiet pytest requests
fi
exec python -m pytest -v tests/real/ "${PYTEST_ARGS[@]}"
