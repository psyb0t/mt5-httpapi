.PHONY: up down logs status clean distclean help

all: up

up:
	./run.sh

down:
	docker compose down

logs:
	docker compose logs -f

status:
	./test.sh

clean: down
	sudo rm -rf data/storage data/metatrader5 data/oem run.log

distclean: clean
	rm -f data/win.iso

help:
	@echo "Available targets:"
	@echo "  up        - Start the Windows VM with MT5 (downloads ISO if needed)"
	@echo "  down      - Stop the VM"
	@echo "  logs      - Follow container logs"
	@echo "  status    - Check VM and MT5 HTTP API status"
	@echo "  clean     - Remove VM disk and state (keeps ISO)"
	@echo "  distclean - Remove everything including ISO"
