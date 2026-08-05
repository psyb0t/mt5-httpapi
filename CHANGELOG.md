# Changelog

All notable changes to **mt5-httpapi**. Annotated git tags carry the full message for each release (`git show <tag>` or the GitHub Releases page) — this file is the readable digest.

The project follows [Semantic Versioning](https://semver.org/): patch = bug fixes / docs, minor = backwards-compatible features, major = breaking changes.

---

## [Unreleased] — Chart Deployments (chartctl)

Remote EA deployment: attach Expert Advisors to charts with set files over the HTTP API, no RDP and no terminal restart.

## [v4.11.4] — 2026-08-01

### Changed

- CI/infrastructure only. No code in this repo changed — the entire diff since v4.11.3 is under `.github/workflows/`.
- The pipeline was split: building and publishing stay in `pipeline.yml`, and everything that leaves the host now lives in its own file beside it.
- The repo is mirrored to Codeberg as well as GitLab.
- The repo is archived to the Wayback Machine, Software Heritage and archive.org.
- Issues opened on either mirror are copied back to GitHub every six hours, and closed here when the original closes.
- Pull requests are switched off on both mirrors. The mirrors are force-pushed from GitHub, so anything merged on them would be destroyed by the next sync. Issues and forking stay enabled.

## [v4.11.3] — 2026-07-31

### Fixed

- The pipeline referenced the shared reusable workflows by commit SHA, which pinned this repo to a revision predating the fix it was actually failing on: `publish-plugins` failed the v4.11.1 and v4.11.2 tag runs because ClawHub's own Plugin Inspector could not start (`ENOENT ... mkdir '/home/sbx_user…'`, a stack trace from their backend), and the retry-and-defer handling that survives exactly that had already shipped upstream. Both references now track `@master`, so a fix to the shared workflows applies on the next run instead of waiting for someone to hand-carry a new SHA into every repo.

## [v4.11.2] — 2026-07-31

### Fixed

- Quick Start now makes the required broker/account/installer-name match explicit instead of pretending the template's placeholder terminals will start a real broker install.
- It now shows token generation, a minimal matching terminal shape, the resulting API route, and `make status` as the readiness check after `make up` boots the VM.

## [v4.11.1] — 2026-07-30

### Changed

- The root README, every focused guide, the published agent skill, its setup reference, and the OpenClaw plugin README now sound like the rest of the psyb0t shit instead of corporate-ass generated copy.
- Commands, API contracts, safety rules, examples, and cross-document links are unchanged; this release changes the voice, not the runtime behavior.

## [v4.11.0] — 2026-07-30

### Added

- Typed per-terminal and unified MCP tools now expose the REST API's complete range-query model: `get_ticks` and `get_rates_ta` accept `from_` and `to`, with parity tests preventing the two MCP catalogs from drifting.
- The public Go client now decodes the ping process mode and terminal broker-UTC-offset fields, with an HTTP-backed regression test for the current response shape.
- `make test-go` compiles and race-tests the Go client in a digest-pinned Go container, and the canonical `make test` gate now runs it alongside the unit and container-backed integration suites.
- The Python test image now pins its base image by digest, matching the Go test container's immutable toolchain pin.

### Changed

- The root README is now a concise project overview and quick start. Detailed installation, REST API, market-data, trading, backtesting, MCP/agent, client/example, multi-VM, and operations guidance lives in focused `docs/*.md` guides.
- README, setup guidance, the published skill, and the OpenClaw bridge metadata now match the complete REST/MCP surface, process-mode semantics, backtest controls, JSON-versus-multipart boundaries, and exact terminal shutdown/restart behavior.

### Fixed

- A clean `make up` no longer treats `vms.yaml.example` as an active multi-VM topology. Without an explicit `vms.yaml`, it seeds the documented single-VM compose example.
- MCP-unifier integration fixtures now use and assert the supported process modes (`live` and `backtest`) rather than the unrelated brokerage-account labels (`live` and `demo`).
- Removed the erroneous `v4.10.0` release-note claim that per-VM `MT5_HTTPAPI_MAX_IN_FLIGHT_*` controls existed; the implementation never exposed those variables.

## [v4.10.1] — 2026-07-30

### Changed

- `make test` is now the complete automated gate, running both the offline unit/contract suite and the container-backed nginx/MCP-unifier suite. `make test-unit` and `make test-integration` remain available for scoped local runs, while CI reports one test job instead of splitting tests by implementation detail.
- The live deployment probe moved from the misleading root-level `test.sh` name to `scripts/status.sh`; `make status` remains the public command.

### Fixed

- The pipeline caller and its ClawHub reusable workflow no longer use the same concurrency-group name. Their identical groups caused GitHub Actions to detect a parent/child deadlock and fail tag runs after every actual test had passed.
- Read-only `config_helper.py` commands no longer import Jinja2. Template support is loaded only by `generate_compose`, so `make status` does not fail merely because the host Python environment lacks the unrelated compose-template dependency.
- `make status` no longer combines `pipefail` with `head` while discovering the first configured terminal. That pipeline killed `config_helper.py` with SIGPIPE whenever the configuration contained multiple terminals.

## [v4.10.0] — 2026-07-30

### Added

- **Config-driven N-VM topology.** `vms.yaml` (copy `vms.yaml.example`) declares each Windows VM's resources — cpuset, RAM, cores, disk, storage path, noVNC port, wickworks sidecar — and every terminal in `config.yaml` binds to one through a new `vm:` field. `config_helper.py` generates nginx routes aimed at the owning VM's container, `run.sh` loops each VM for DNAT and per-VM group files, and `docker-compose.yml` renders from the new `docker-compose.yml.j2` via `config_helper.py generate_compose`. Backwards compatible by construction: no `vms.yaml` means single-VM, and a terminal with no `vm:` field routes to `mt5` exactly as before. Walkthrough in `docs/multi-vm-setup.md`.
- **Multi-VM configuration commands.** `config_helper.py` adds `vms`, `vm_group <name>`, `vm_info <name> [field]`, `port_list --vm <name>` and `generate_compose` for inspecting and rendering the topology.
- **Contract tests for every handler that moves money.** `tests/test_handlers_orders.py`, `tests/test_handlers_positions.py` and `tests/test_handlers_readonly.py` drive the real Flask routes with the MT5 SDK faked at the `m()` seam, asserting the exact request that would reach `order_send` — a market BUY priced at ask and a SELL at bid, closing a BUY sending a SELL at bid, a partial close sending only the requested volume, an sl-only modify preserving the existing tp. Every failure path also asserts `order_send` was never called, because a handler that errors after sending has already traded.
- **Tests for the files `config_helper.py` generates.** `tests/test_config_generation.py` asserts the nginx config emits no literal `proxy_pass http://host:port`, that every terminal route carries a resolver, that terminals reach their own VM's container, that an absent `vms.yaml` still routes everything to `mt5`, that a live terminal's INI declares no `[StartUp]` expert, and that two VMs never share a host port.
- **`make test-integration`** — container-backed suites under `tests/integration/`, driven by pytest and testcontainers. Boots real nginx against the generated config with one VM deliberately absent, and stands the MCP unifier up beside a stub terminal. Runs on the host because it starts sibling containers through the docker socket, with its dependencies isolated in a gitignored `.venv-test/`.
- **CI now runs the tests.** `pipeline.yml` previously triggered only on `v*` tags and did nothing but publish badges and ClawHub skills, so the suite under `tests/` had never run in CI at all. It now runs `test`, `integration` and `lint` on pushes to `master` and on every non-draft pull request (including the transition from draft to ready for review), and the ClawHub publish is gated on all three so a tag with failing tests cannot publish.

### Changed

- **`make test-mcpunifier` folded into `make test-integration`.** Its seven assertions moved from `scripts/test-mcpunifier.sh` to `tests/integration/test_mcpunifier.py` unchanged — health, the 25-tool surface, both configured terminals listed, a live terminal routing to its own port, a down terminal failing only the calls that name it, an unconfigured broker/account pair refused, and the endpoint still healthy after both failures. The shell script and its make target are gone; there is now one integration harness in one language.
- nginx terminal routes resolve their upstream per request (a `resolver` directive plus a variable) instead of at config-parse time. With a literal upstream, a single absent VM container stopped nginx starting at all, taking every healthy VM's routes, the REST API and `/mcp/` down with it.
- The test stub in `tests/conftest.py` now defines `TRADE_RETCODE_DONE` and gives every mocked SDK function a `__name__`. Without the first, no test could reach an order-result success branch; without the second, every SDK call identified as `"?"`, including in `mt5client`'s per-call timing log.

### Fixed

- `config_helper.py` reports a failed `pip install` of its own dependencies instead of surfacing a bare `ImportError` about the module it was trying to install.
- `run.sh` is `shfmt`-clean again.
- `scripts/lint.sh` skips tracked files deleted in the working tree, so release-preparation lint can validate a script-removal commit before it is staged.
- Host-side integration tests use the non-vulnerable `pytest` 9.0.3 release and the age-gate-eligible `testcontainers` 4.14.2 release.
- Reusable GitHub Actions workflows are pinned to an immutable commit instead of the mutable `master` ref.

## [v4.9.4] — 2026-07-30

### Fixed

- Fresh terminal API and MCP-unifier installs now pin MCP SDK 1.28.0. MCP 2.0 removed `mcp.server.fastmcp`, causing both processes to fail during import before binding their HTTP ports.

## [v4.9.3] — 2026-07-28

### Fixed

- **The README's "Make Targets" list was missing `make test-mcpunifier`**, added in v4.9.2. That list is where a contributor looks for the supported operations, so a target absent from it is a target nobody runs. It now matches `make help` exactly, and carries a short note on what the target checks and that it needs the docker socket.

## [v4.9.2] — 2026-07-28

### Added

- **`make test-mcpunifier`** — an end-to-end test for the MCP unifier, which had no automated coverage.

## [v4.9.1] — 2026-07-28

### Fixed

- **A missing `mcpunifier` container no longer stops nginx from starting and takes every other route down with it.**

## [v4.9.0] — 2026-07-28

### Added

- **Unified MCP endpoint at `/mcp`, spanning every configured terminal.**
- **`list_terminals`**, reporting each configured terminal's broker, account, instance and whether it is a live or demo account.
- **`mcpunifier` service** (`mcpunifier/`, `Dockerfile.mcpunifier`).

### Changed

- **Docs and plugin manifests now describe both MCP endpoints.**

## [v4.8.4] — 2026-07-27

### Fixed

- The README's Codex subsection under `## Agent integrations` stopped after `codex plugin marketplace add psyb0t/agents` and never told the reader how to actually install the plugin.

## [v4.8.3] — 2026-07-27

### Added

- **Codex plugin manifest** (`.agents/.codex-plugin/plugin.json`).
- **`## Agent integrations` README section** with copy-pasteable install commands.

### Removed

- Deleted the stray `.claude-plugin/marketplace.json` from this repo.


## [v4.3.1] — 2026-05-17

Critical trading-path fixes + integration test suite.

### Fixed

- `order_send` / `order_check` now use `**kwargs` unpacking. MetaTrader5 wheel `5.0.5735` `_core.pyd` is keyword-only for these calls — a positional dict fails in 0ms with the misleading `(-2, 'Unnamed arguments not allowed')` before any IPC to the terminal. Read-only calls were unaffected, which is why the regression hid for so long. Applies to `mt5api/handlers/orders.py` (create / update / cancel) and `mt5api/handlers/positions.py` (SL/TP modify, close).
- `update_order` referenced the non-existent `TradeOrder.expiration` field — now uses `time_expiration`. Previously surfaced as a 500 with HTML body on `PUT /orders/<ticket>`.
- `create_order` now waits for the first non-zero tick after auto-selecting a freshly-added symbol (10 × 0.2s). Fixes "Cannot get price" on the very first market order after a cold boot, when `symbol_select` succeeds but the tick subscription hasn't filled yet.

### Added

- `mt5client.ensure_symbol()` — moved out of `symbols.py` so order handlers can auto-select the symbol on every trade entry, not just on `/symbols/<s>` reads. Trade endpoints no longer require the caller to pre-warm a symbol via `/symbols/<s>/tick`.
- `tests/real/` — live-API integration suite (pytest). 35 tests covering account, ping/terminal, symbols (info/tick/rates/ta/ticks), orders (list/create/get/update/cancel), positions (list/get/update/close), history (orders/deals). Magic-number-tagged so it can run against a live demo account in parallel with manual trading without disturbing it.
- `scripts/start.bat`: auto-reboot when pip installs or upgrades a package on boot. Detects "Successfully installed" in pip output and triggers `shutdown /r` so already-running `api_runner` processes don't keep stale imports.

### Infra

- Pin `numpy<2` in `scripts/install.bat` and `scripts/start.bat`. The MetaTrader5 wheel is built against numpy 1.x ABI; defensive measure even though the `unnamed arguments` regression was caused by the kwargs issue, not the numpy ABI.

## [v4.3.0] — 2026-05-14

**MT5 Strategy Tester HTTP API + per-terminal live/backtest mode.** Lands PR #2 from `algotradingspace/backtester` (Marin). A tester-mode terminal now runs alongside live terminals on the same Docker / Windows VM deployment, behind the same `/<broker>/<account>/...` nginx routing.

### Why `mode` exists

MT5 is single-instance per portable data directory. A running `terminal64.exe` holds an exclusive lock on its dir; any second `terminal64.exe` spawned against the same dir exits silently with code 0. That makes it impossible to drive the Strategy Tester through a terminal that's also backing the live SDK. `mode` declares intent at terminal-startup time so a single install can run both kinds side by side.

### Added

- New endpoints: `POST /backtest/build-ini`, `POST /backtest`, `GET /backtest/<job_id>`, `GET /backtest/<job_id>/report`, `GET /backtest/<job_id>/log`.
- `GET /ping` now echoes `{"status":"ok","mode":"<live|backtest>"}`.
- New per-terminal field `mode: live | backtest` in `config.yaml` (defaults to `live`).
- New per-terminal field `symbol_suffix` — optional broker-specific suffix appended to `[Tester].Symbol` so the same EA/.set/.ini can run against multiple brokers with different symbol naming.
- Configurable backtest timeout with 4-tier override chain: POST form field `timeout` → `config.yaml.backtest_timeout` → `BACKTEST_TIMEOUT` env → hardcoded `DEFAULT_BACKTEST_TIMEOUT="6h"`. Reuses `parse_duration_to_seconds` (same grammar as `utc_offset`).
- Host-managed asset pool: `./assets/experts/` + `./assets/sets/` mounted read-only into the VM, referenced from `POST /backtest` via `expert_name` / `set_name` (inline upload still works).
- Parsed report summary now includes `bars` / `ticks` / `symbols` so empty-history failures are visible in JSON without opening the HTML.
- `psutil` added to base pip install (was already imported by `mt5client.py` but missing from the install list).

### Changed

- Generated nginx config now sets `client_max_body_size 25m` + `client_body_timeout 120s` so EA + `.set` uploads up to ~25 MB succeed.
- `reboot_interval=0` is now respected at startup so long backtest runs aren't interrupted by the scheduled auto-reboot.

### Security / safety

- Tester INI is re-encoded UTF-16-LE+BOM+CRLF before MT5 reads it (MT5 silently rejects `[Tester] Login` under UTF-8).
- `[Common].Login/Password/Server` are always overwritten from the URL-selected account in `config.yaml` — the caller cannot inject credentials.
- Path traversal in `expert_name` / `set_name` is rejected.
- Concurrency: one tester per API process, serialized by an internal `RUN_LOCK`; additional submissions queue. Jobs left in-flight when the API restarts are marked failed by `sweep_orphans()` at next boot.

### Compatibility

Fully additive. `mode` defaults to `live`; existing config files keep working verbatim. Live terminal API surface, multi-terminal routing, Docker/VM deployment, and the v4 single-file config model are all unchanged.

---

## [v4.2.2] — 2026-05-13

Sync docs + example compose to **wickworks v0.3.x** (primitives-only, camelCase-canonical).

- README and SKILL.md drop divergence claims, add primitives-only disclaimer, fix the `indicators` spec example to the flat shape.
- SKILL.md indicator catalog rewritten with real registry names across 8 trader-meaningful categories; missing alt-MAs added.
- `docker-compose.yml.example` pin bumped `psyb0t/wickworks:v0.2.0 → v0.3.1` so a fresh install no longer ships the pre-purification image.
- Go client `RatesTAQuery` doc comment fixed (flat indicator shape; `RecentBars` marked inert).

No runtime code changes.

## [v4.2.1] — 2026-05-13

Surface the TA capability prominently in docs.

- README intro leads with built-in TA, adds a Table of Contents.
- SKILL.md gets a dedicated Technical Analysis section.
- Both docs link to `github.com/psyb0t/docker-wickworks` for the indicator catalog.

No code changes.

## [v4.2.0] — 2026-05-13

**Go client gains `GetRatesTA`** matching the wickworks TA endpoint from v4.1.0.

- New `Client.GetRatesTA(ctx, symbol, RatesTAQuery{Indicators, ...})`.
- New `RatesTAQuery` + `RatesTAResponse` types.

## [v4.1.0] — 2026-05-13

**Wickworks TA sidecar + `POST /symbols/<symbol>/rates/ta`** — one call returns OHLC bars plus indicators (RSI, MACD, Bollinger, ADX, SMC primitives, etc.) computed by the wickworks sidecar.

- New wickworks sidecar (`psyb0t/wickworks`), netns-shared with `mt5`, no published ports, VM-reachable via `20.20.20.1:8000`.
- `config.yaml` gains optional `wickworks: { url, timeout }`.
- Backwards compatible — existing endpoints unchanged.
- **Manual upgrade step:** copy the `wickworks:` service block from `docker-compose.yml.example` into your `docker-compose.yml`.

## [v4.0.1] — 2026-05-07

Post-v4.0.0 stability fixes.

- `install.bat`: skip the install loop when every broker already has its `base/terminal64.exe`. The v4.0.0 loop set `NEEDS_REBOOT=1` once per boot, triggering an infinite reboot loop.
- `start.bat`: switched API_TOKEN load from `for /f` to a tempfile read — the for/f form occasionally returned empty (python crash / pyyaml install / stdout buffering through the cmd subshell).
- `run.sh`: added `SKIP_KVM_CHECK` env escape hatch for hosts that proxy KVM differently (CI, nested virt).
- `config_helper.py` + `run.sh`: new `port_list` subcommand that prints individual ports space-separated, for per-port iteration in `run.sh`.

## [v4.0.0] — 2026-05-07 — BREAKING

**Single `config/config.yaml` replaces seven separate config files.** Migrate from `config/config.yaml.example`.

The retired files: `accounts.json`, `terminals.json`, `api_token.txt`, `ts_authkey.txt`, `ts_login_server.txt`, `reboot_interval.txt`, `requirements.txt`.

Also pins all docker images to specific versions (`dockurr/windows:5.14`, `nginx:1.30.0-alpine3.23`, `cloudflare/cloudflared:2026.3.0`, `tailscale/tailscale:v1.96.5`, `python:3.12-slim-bookworm`) in response to the Trivy/KICS supply-chain incidents on Docker Hub.

---

## [v3.2.0] — 2026-05-07

Daily log rotation sidecar with 7-day retention.

## [v3.1.2] — 2026-05-07

Tee Windows events into `full.log` alongside `windows-events.log`.

## [v3.1.1] — 2026-05-07

Windows event log tailer for OOM / crash / BSOD visibility from outside the VM.

## [v3.1.0] — 2026-05-07

**Concurrency hardening.** Per-request MT5 lock + per-call SDK timeouts + queue-depth backpressure. Fixes wedge-induced connection-refused failures that surfaced under sustained load.

## [v3.0.3] — 2026-05-07

Tailscale sidecar TUN mode (`TS_USERSPACE=false`). Accurate inbound-vs-outbound isolation docs.

## [v3.0.2] — 2026-05-07

Wire tailscale serve via CLI (FQDN-aware), drop static `serve.json` — fixes Headscale + bare-host dispatch.

## [v3.0.1] — 2026-05-07

Fix tailscale `serve.json` (`TCP[80].HTTP=true`); Cloudflare Tunnel docs.

## [v3.0.0] — 2026-05-07 — BREAKING

**nginx always-on single entry point**, `/<broker>/<account>/` URL prefix, tailscale own-netns ACL isolation.

All terminals are now routed through a single nginx instance instead of per-terminal port exposure. Callers move from `host:<port>/...` to `host:<api_port>/<broker>/<account>/...`. Tailscale (optional) runs in its own netns so it gets its own tailnet identity.

---

## [v2.2.0] — 2026-05-06

Optional Tailscale + nginx sidecars for tailnet exposure. Auto-generated from `terminals.json`, Headscale-compatible.

## [v2.1.0] — 2026-05-05

nginx-style request logs, switched to waitress WSGI server, retry-doubling on terminal init failure, `from+to` range mode for rates/ticks, pytest suite.

## [v2.0.1] — 2026-05-05

Fix `rates` signed-count direction — `copy_rates_from` goes backward, not forward.

## [v2.0.0] — 2026-05-05 — BREAKING

`rates` / `ticks`: drop `to` parameter, use signed `count` for direction. Forward queries use positive count, backward queries use negative.

---

## [v1.8.2] — 2026-05-04

Go client: `uint64` for `tick_volume` / `real_volume` / `volume` to match MQL5 `ulong`.

## [v1.8.1] — 2026-05-04

Proper full-URL logging; fix rates returned when requested date is beyond what the broker has on file.

## [v1.8.0] — 2026-05-04

**Normalize broker timestamps to real UTC** via per-terminal `utc_offset`. MT5 returns timestamps in broker wall-clock time disguised as unix UTC; this offset corrects them on the wire. Negative values allowed for west-of-UTC brokers.

## [v1.7.3] — 2026-05-03

Make `docker-compose.yml` user-owned; ship `.example` template.

## [v1.7.2] — 2026-05-03

Healthcheck: dynamic per-port probing via dnsmasq VM IP.

## [v1.7.1] — 2026-05-03

Docs: clarify 512M memory-limit caveats for heavy scraping.

## [v1.7.0] — 2026-05-02

`rates` / `ticks` time-range, tick flags, auto symbol-select, gzip response compression.

## [v1.6.0] — 2026-05-02

**Typed Go client** at `clients/go/` covering all endpoints.

## [v1.5.x] — 2026-04-08 → 2026-04-27

Startup polish + documentation iteration (`v1.5.0` shipped bearer-token auth via `--token` / `API_TOKEN` / `config/api_token.txt`, auth on all routes, cloudflared tunnel commented into docker-compose; subsequent patches were minor doc/startup tweaks).

## [v1.4.0] — 2026-02-28

**Observability + self-healing.** Structured logging, health monitor, terminal restart, boot fix.

- Centralized logging (`logger.py`) with identity prefix and cross-process file locking for shared `full.log`.
- Health monitor thread: checks login status, algo trading, auto-restarts dead terminals after 5 consecutive failures.
- Terminal restart via API (`POST /terminal/restart`) using WMI kill + PowerShell `Start-Process RunAs` for elevated launch.
- HTTP request/response logging (skipping `/ping`).
- Fixed `start.bat` goto-inside-call bug (replaced with `for /L` loop).
- Added `psutil` dependency.

## [v1.3.0] — 2026-02-23

**Multi-terminal boot overhaul, debloat fixes, settings cleanup.**

- Rename docker-compose service `metatrader5 → mt5`, volume `data/metatrader5 → data/shared`.
- Restructure shared dir: `scripts/`, `config/`, `terminals/` subdirs.
- `install.bat`: 4-stage sequential boot (schtask+UAC, debloat, python, terminals) with atomic mkdir lock + stale-lock cleanup after reboot.
- `start.bat` (renamed from `start-mt5.bat`): multi-terminal via `terminals.json`; deletes stale `settings.ini` + `common.ini` per boot so MT5 actually reads `mt5start.ini`; pip failure is now fatal.
- `debloat.bat`: removed `Ndu` from `sc stop` (kernel driver, hangs forever); added firewall disable; fixed defender-remover path.
- `mt5api/config.py`: fixed paths for `config/` subdir, simplified `terminal64.exe` lookup.

## [v1.2.0] — 2026-02-21

**Multi-terminal fixes, boot stability, login verification.**

- Fix reboot loop: separate `oem-install.bat` stub so only one `install.bat` path runs.
- Fix concurrent instances: atomic mkdir lock instead of file-based lock (race condition).
- Fix MT5 auto-updater reboots: kill `liveupdate.exe` / `mtupdate.exe` on startup.
- `ensure_initialized`: verify `account_info()` login after terminal connects, call `mt5.login()` if not logged in.
- Wrap `account_info()` in 15s timeout to prevent hangs.
- Remove legacy single-terminal fallback (`terminals.json` now required).
- Disable UAC on VM (headless, no need for it).

## [v1.1.0] — 2026-02-19

Multi-terminal support on a single machine — multiple broker/account terminals running in the same Windows VM, each on its own port.

## [v1.0.0] — 2026-02-15

Initial public release. MT5 terminal running inside a `dockurr/windows` VM, exposed via a Python HTTP API for live trading and market data.

[v4.3.1]: https://github.com/psyb0t/mt5-httpapi/releases/tag/v4.3.1
[v4.3.0]: https://github.com/psyb0t/mt5-httpapi/releases/tag/v4.3.0
[v4.2.2]: https://github.com/psyb0t/mt5-httpapi/releases/tag/v4.2.2
[v4.2.1]: https://github.com/psyb0t/mt5-httpapi/releases/tag/v4.2.1
[v4.2.0]: https://github.com/psyb0t/mt5-httpapi/releases/tag/v4.2.0
[v4.1.0]: https://github.com/psyb0t/mt5-httpapi/releases/tag/v4.1.0
[v4.0.1]: https://github.com/psyb0t/mt5-httpapi/releases/tag/v4.0.1
[v4.0.0]: https://github.com/psyb0t/mt5-httpapi/releases/tag/v4.0.0
[v3.2.0]: https://github.com/psyb0t/mt5-httpapi/releases/tag/v3.2.0
[v3.1.2]: https://github.com/psyb0t/mt5-httpapi/releases/tag/v3.1.2
[v3.1.1]: https://github.com/psyb0t/mt5-httpapi/releases/tag/v3.1.1
[v3.1.0]: https://github.com/psyb0t/mt5-httpapi/releases/tag/v3.1.0
[v3.0.3]: https://github.com/psyb0t/mt5-httpapi/releases/tag/v3.0.3
[v3.0.2]: https://github.com/psyb0t/mt5-httpapi/releases/tag/v3.0.2
[v3.0.1]: https://github.com/psyb0t/mt5-httpapi/releases/tag/v3.0.1
[v3.0.0]: https://github.com/psyb0t/mt5-httpapi/releases/tag/v3.0.0
[v2.2.0]: https://github.com/psyb0t/mt5-httpapi/releases/tag/v2.2.0
[v2.1.0]: https://github.com/psyb0t/mt5-httpapi/releases/tag/v2.1.0
[v2.0.1]: https://github.com/psyb0t/mt5-httpapi/releases/tag/v2.0.1
[v2.0.0]: https://github.com/psyb0t/mt5-httpapi/releases/tag/v2.0.0
[v1.8.2]: https://github.com/psyb0t/mt5-httpapi/releases/tag/v1.8.2
[v1.8.1]: https://github.com/psyb0t/mt5-httpapi/releases/tag/v1.8.1
[v1.8.0]: https://github.com/psyb0t/mt5-httpapi/releases/tag/v1.8.0
[v1.7.3]: https://github.com/psyb0t/mt5-httpapi/releases/tag/v1.7.3
[v1.7.2]: https://github.com/psyb0t/mt5-httpapi/releases/tag/v1.7.2
[v1.7.1]: https://github.com/psyb0t/mt5-httpapi/releases/tag/v1.7.1
[v1.7.0]: https://github.com/psyb0t/mt5-httpapi/releases/tag/v1.7.0
[v1.6.0]: https://github.com/psyb0t/mt5-httpapi/releases/tag/v1.6.0
[v1.5.x]: https://github.com/psyb0t/mt5-httpapi/releases/tag/v1.5.3
[v1.4.0]: https://github.com/psyb0t/mt5-httpapi/releases/tag/v1.4.0
[v1.3.0]: https://github.com/psyb0t/mt5-httpapi/releases/tag/v1.3.0
[v1.2.0]: https://github.com/psyb0t/mt5-httpapi/releases/tag/v1.2.0
[v1.1.0]: https://github.com/psyb0t/mt5-httpapi/releases/tag/v1.1.0
[v1.0.0]: https://github.com/psyb0t/mt5-httpapi/releases/tag/v1.0.0
