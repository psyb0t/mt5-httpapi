.PHONY: up down logs status lint format test test-unit test-integration test-go verify-binaries clean distclean help

GO_TEST_IMAGE := golang@sha256:3aff6657219a4d9c14e27fb1d8976c49c29fddb70ba835014f477e1c70636647

all: up

up:
	./run.sh

down:
	docker compose down

logs:
	docker compose logs -f

status:
	./scripts/status.sh

# Lint every script the stack runs, in a throwaway Docker image (same
# build-run-rmi pattern as test). Repo is mounted read-only — lint never
# rewrites your files.
#
# Covers .ps1 (non-ASCII gate + parse check + PSScriptAnalyzer) and .sh
# (shellcheck + shfmt). The non-ASCII gate exists because an em-dash in a
# string literal in acquire_lock.ps1 mojibaked under Windows PowerShell 5.1,
# threw a parse error, and start.bat read that exit code as "lock held" —
# deadlocking every boot. That class of bug is invisible until the VM starts,
# so it gets caught here instead.
lint:
	@TAG=mt5-httpapi-lint:$$(date +%s)-$$RANDOM; \
	docker build -f Dockerfile.lint -t $$TAG . && \
	(docker run --rm -v "$$PWD":/work:ro $$TAG; STATUS=$$?; docker rmi -f $$TAG >/dev/null; exit $$STATUS)

# Apply shfmt formatting in place. `make lint` reports what is unformatted;
# this fixes it. Mount is writable here, unlike lint's read-only one.
#
# Delegates to the same lint.sh --format so the file selection is shared with
# `make lint`. When this target had its own `git ls-files` list it skipped
# untracked files that lint still flagged, and lint stayed red after a
# successful format.
format:
	@TAG=mt5-httpapi-lint:$$(date +%s)-$$RANDOM; \
	docker build -f Dockerfile.lint -t $$TAG . && \
	(docker run --rm -v "$$PWD":/work $$TAG --format; \
	STATUS=$$?; docker rmi -f $$TAG >/dev/null; exit $$STATUS)

# Run every automated test suite. Keep the scoped targets for fast local runs,
# but CI and the canonical contributor command use this complete gate.
test:
	$(MAKE) verify-binaries
	$(MAKE) test-unit
	$(MAKE) test-integration
	$(MAKE) test-go

# Every tracked executable must be declared in assets/binaries.lock.json with
# its upstream and sha256. Runs on the host rather than in a container because
# it needs the COMPLETE checkout -- the test image COPYs a subset, and a gate
# that reports "0 binaries checked" because it could not see them is worse than
# no gate. Needs nothing but python3 and the repo.
verify-binaries:
	@python3 scripts/verify_binaries.py

# Run unit tests in a throwaway Docker image. The image is built with a
# unique tag, run, and removed afterwards (--rm + rmi) so nothing lingers
# on the host. The MT5 SDK is mocked; this covers offline logic and handler
# contracts without touching a live trading terminal.
test-unit:
	@TAG=mt5-httpapi-tests:$$(date +%s)-$$RANDOM; \
	docker build -f Dockerfile.test -t $$TAG . && \
	(docker run --rm $$TAG; STATUS=$$?; docker rmi -f $$TAG >/dev/null; exit $$STATUS)

# Every container-backed suite, via testcontainers:
#   - nginx routing: boots real nginx against the config config_helper.py
#     generates, with one VM deliberately absent. The unit tests assert that
#     config's SHAPE with regexes; this asserts nginx accepts and serves it.
#   - MCP unifier: builds the shipped image and stands it up beside a stub
#     terminal, asserting the tool surface, routing, and that a down terminal
#     fails alone.
#
# Runs on the host rather than inside the test image because testcontainers
# spawns sibling containers through the host docker socket — wrapping that in
# another container buys nothing.
test-integration:
	python3 -m venv .venv-test
	.venv-test/bin/pip install --quiet --upgrade pip
	.venv-test/bin/pip install --quiet -r requirements-test.txt
	.venv-test/bin/pytest -v tests/integration/

# Compile and race-test the public Go client in the same pinned Go toolchain
# declared by its module. The source mount is read-only; caches are ephemeral.
test-go:
	docker run --rm \
		-e GOCACHE=/tmp/go-build \
		-e GOMODCACHE=/tmp/go-mod \
		-v "$$PWD/clients/go:/src:ro" \
		-w /src \
		$(GO_TEST_IMAGE) \
		sh -c '/usr/local/go/bin/go test -race ./...'

clean: down
	sudo rm -rf data/storage data/shared data/metatrader5 data/oem run.log

distclean: clean
	rm -f data/win.iso

help:
	@echo "Available targets:"
	@echo "  up        - Start the Windows VM with MT5 (downloads ISO if needed)"
	@echo "  down      - Stop the VM"
	@echo "  logs      - Follow container logs"
	@echo "  status    - Check VM and MT5 HTTP API status"
	@echo "  lint      - Lint all .ps1/.sh scripts in a throwaway Docker image"
	@echo "  format    - Apply shfmt formatting to all .sh files in place"
	@echo "  test      - Run the complete automated test suite"
	@echo "  test-unit - Run unit and contract tests in a throwaway Docker image"
	@echo "  test-integration - Container-backed suites: nginx routing + MCP unifier (needs docker)"
	@echo "  test-go  - Compile and race-test the public Go client in a pinned container"
	@echo "  clean     - Remove VM disk and state (keeps ISO)"
	@echo "  distclean - Remove everything including ISO"
