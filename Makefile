.PHONY: up down logs status test clean distclean help

all: up

up:
	./run.sh

down:
	docker compose down

logs:
	docker compose logs -f

status:
	./test.sh

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
	@echo "  test      - Run unit tests in a throwaway Docker image"
	@echo "  clean     - Remove VM disk and state (keeps ISO)"
	@echo "  distclean - Remove everything including ISO"
