.PHONY: up down logs status lint format test clean distclean help

all: up

up:
	./run.sh

down:
	docker compose down

logs:
	docker compose logs -f

status:
	./test.sh

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

# Run unit tests in a throwaway Docker image. The image is built with a
# unique tag, run, and removed afterwards (--rm + rmi) so nothing lingers
# on the host. MT5 SDK is mocked — these tests cover pure logic only.
test:
	@TAG=mt5-httpapi-tests:$$(date +%s)-$$RANDOM; \
	docker build -f Dockerfile.test -t $$TAG . && \
	(docker run --rm $$TAG; STATUS=$$?; docker rmi -f $$TAG >/dev/null; exit $$STATUS)

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
	@echo "  test      - Run unit tests in a throwaway Docker image"
	@echo "  clean     - Remove VM disk and state (keeps ISO)"
	@echo "  distclean - Remove everything including ISO"
