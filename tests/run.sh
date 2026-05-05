#!/usr/bin/env bash
# Build and run the unit-test container. MT5 SDK is mocked (Windows-only,
# can't pip install on Linux); these tests cover pure logic — parsers,
# time conversion, duration parsing.
set -euo pipefail

cd "$(dirname "$0")/.."

IMG=mt5-httpapi-tests
docker build -f Dockerfile.test -t "$IMG" .
docker run --rm "$IMG" "$@"
