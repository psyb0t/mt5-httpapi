# Changelog

All notable changes to **mt5-httpapi**. Annotated git tags carry the full message for each release (`git show <tag>` or the GitHub Releases page) — this file is the readable digest.

The project follows [Semantic Versioning](https://semver.org/): patch = bug fixes / docs, minor = backwards-compatible features, major = breaking changes.

---

## [v4.8.1] — 2026-07-27

### Added

- Added self-hosted version and license badges; wired a badges job into pipeline.yml.

## [v4.8.0] — 2026-07-26

MCP interface reworked from a single generic passthrough to dedicated, typed tools.

### Changed

- **`/mcp` now exposes ~24 dedicated typed tools** grouped by family (market data, account, positions, orders, history, terminal, backtest) instead of the lone generic `request` passthrough. Each tool has typed params + a description the agent reads — e.g. `create_order(symbol, type, volume, price?, sl?, tp?)`, `get_rates(symbol, timeframe, count?)`, `close_position(ticket, volume?)` — so the tool schema IS the documentation. Order/position mutation tools carry an explicit irreversible-live-account note. A generic `request` + `endpoints` catalog remain as a fallback for routes without a dedicated tool. Every tool still runs the same handler + auth + MT5 locking as a real HTTP call (in-process). README + skill + plugin docs updated.
- Submitting a backtest (`POST /backtest`) is **not** exposed as a tool — that route takes a multipart file upload; `get_backtest` polls status/report/log/tail, and new runs are submitted via the REST API.

## [v4.7.0] — 2026-07-26

New MCP interface — the API is now also driveable over the Model Context Protocol.

### Added

- **MCP server mounted at `/mcp`** (streamable-HTTP), in the same process as the REST API on every terminal. Three tools mirror the whole REST surface: `ping` (lock-free liveness), `endpoints` (the route catalog), and `request(method, path, query, body)` — call any REST endpoint, running the exact same handler + auth + MT5 locking as a real HTTP request. Same bearer auth as REST (empty `api_token` = auth off; a configured token requires `Authorization: Bearer <token>` on `/mcp` too). See `mt5api/mcp_server.py`.
- **`@psyb0t/mt5-httpapi` ClawHub plugin** (`.agents/plugins/mt5-httpapi/`) — a stdio↔HTTP MCP bridge (`mcp-remote`) so an OpenClaw/MCP agent can drive a running terminal. Point `MT5_API_URL` at the terminal's base (+ `MT5_API_TOKEN` if auth is on); the reachable endpoint is `$MT5_API_URL/mcp/`. CI publishes it to ClawHub alongside the skill.
- README and the `mt5-httpapi` skill gain an **MCP interface** section.

### Note

- mt5api is a Flask/WSGI app; the ASGI MCP app is bridged in via `a2wsgi` behind `/mcp` (there's a `TODO` to migrate mt5api to FastAPI and drop the bridge). New runtime deps `mcp` + `a2wsgi` are installed by `scripts/start.bat` on boot and tracked in `requirements-api.txt`. No REST endpoint or trading-path change.

## [v4.6.0] — 2026-07-26

Hotfix for a boot-blocking regression introduced in v4.5.0, plus a `make lint` / `make format` gate so that class of bug cannot reach the VM again.

### Fixed

- **v4.5.0's `scripts/acquire_lock.ps1` deadlocked every boot.** The file contained em-dashes in comments *and in string literals*, with no UTF-8 BOM. Windows PowerShell 5.1 reads `.ps1` as ANSI, so those bytes were mangled, the string literals terminated early, and the script died with `Unexpected token` / `The hash literal was incomplete`. The script is now pure ASCII, which needs no BOM to stay stable.
- **A failing lock helper was indistinguishable from a held lock.** `acquire_lock.ps1` used exit 1 for "another instance holds the lock" — the same code PowerShell returns for a parse error. So the syntax error above made `start.bat` conclude the lock was taken and exit, on every boot, which is the exact deadlock the lock rewrite was meant to remove. Exit codes are now distinct: `0` acquired, `10` held, anything else means the helper itself failed. On that third case `scripts/start.bat` and `scripts/install.bat` log a warning and fall back to a plain `mkdir` lock, so a broken helper can degrade single-instance safety but can never block boot.
- `scripts/event-log-tailer.ps1`: em-dashes in comments replaced with ASCII (same mojibake hazard); `Append-Full` renamed to `Add-FullLogLine` (`Add` is an approved PowerShell verb); its `catch {}` no longer swallows silently — a failed `full.log` append is now reported into `windows-events.log`, which is not the contended file that just failed.

### Added

- **`make lint`** — lints every tracked-or-new script in a throwaway Docker image (built, run, `docker rmi`'d, repo mounted read-only), mirroring how `make test` works. Six checks: a self-test of its own non-ASCII detector, the `.ps1` ASCII gate, a `.ps1` parse check, PSScriptAnalyzer, shellcheck (warning and above), and shfmt. `Dockerfile.lint` + `scripts/lint.sh`.
  - The detector self-test exists because a checker that silently stops detecting is worse than no checker — the same failure mode as the healthcheck fixed in v4.5.0. It verifies the pattern still flags a real em-dash and still passes pure ASCII, and fails the whole run if it cannot tell them apart.
  - Files are selected with `git ls-files --cached --others --exclude-standard`, so brand-new scripts are covered while gitignored local scratch is not. The vendored `scripts/defender-remover/` tree is excluded.
- **`make format`** — applies shfmt in place. Delegates to `scripts/lint.sh --format` so it shares file selection with `make lint`; when the two had separate lists, `format` skipped untracked files that `lint` still flagged and the gate could never go green.

### Changed

- Applied shfmt formatting to `run.sh`, `test.sh`, `scripts/rotate-logs.sh`, and `tests/real/run.sh`. Whitespace and layout only — no behavior change. In `test.sh` this expands single-line function bodies (`pass() { echo …; PASS=…; }`) onto separate lines, which is most of the diff.
- README's Make Targets list now includes `lint`, `format`, and `test` (`test` had been missing).

## [v4.5.0] — 2026-07-26

Boot-lock and reboot hardening for the Windows VM, a critical healthcheck false-positive fix, and a `make test` build fix. Also adds third-party license notices for the vendored Windows Defender removal tool.

### Fixed

- **Healthcheck reported dead terminals as healthy.** `scripts/healthcheck.sh` probed each terminal with `curl … -w '%{http_code}' … || echo 000`. curl already prints `000` on a failed connection, so the `|| echo 000` fallback appended a second one — yielding `000000`, which compared unequal to `000` and marked the port UP. A full outage could sit behind a green Docker healthcheck indefinitely. The probe now whitelists a valid HTTP status shape (`[1-5][0-9][0-9]`) and fails closed; any real status, including 4xx/5xx, proves the process is listening.
- **Reboot-orphaned boot locks deadlocked the stack.** `%SHARED%\start.running` (and install.bat's lock) live on the host-mounted volume and survive a VM reboot. The auto-reboot task fires `shutdown /r /t 0 /f` with no grace period and can land mid-run, stranding the lock so every later boot bailed on the orphan forever. New `scripts/acquire_lock.ps1` stamps each lock with the OS boot time, so a lock from a previous boot is provably ownerless and is cleared automatically; a live same-boot instance still blocks. `scripts/start.bat` and `scripts/install.bat` acquire through it and release via a single `release_lock`.
- **`make test` was dead on a clean checkout.** `Dockerfile.test` COPYed `config/requirements.txt`, which had been retired and is gitignored, so the build failed with `"/config/requirements.txt": not found`. Tests now install from the tracked `requirements-api.txt`, additionally COPY `scripts/config_helper.py` (loaded by `tests/test_terminal_instances.py`), and skip the live-deployment `tests/real/` suite in the default offline run.

### Added

- `scripts/reboot.bat` — the single reboot path for the VM. Writes `rebooting.flag` and releases both lock dirs in one place, replacing three separate inline flag+shutdown+rmdir sequences that had to be kept in sync.
- `requirements-api.txt` — tracked source of truth for the mt5api HTTP server's Python dependencies (replacing the retired, gitignored `config/requirements.txt`). `scripts/start.bat` installs the same set inline on every boot. Retains the documented `numpy<2` pin — the MetaTrader5 `5.0.5735` wheel is built against numpy 1.x, and under numpy 2.x `order_send` fails with `(-2, 'Unnamed arguments not allowed')`.

### Changed

- Renamed the boot entrypoint `start-mt5.bat` → `start.bat` and its log `start-mt5.log` → `start.log`; README file-tree and log references updated to match.

### Licensing

- Added `THIRD_PARTY.md` and `scripts/defender-remover/LICENSE` (GPL-3.0). `scripts/defender-remover/` is a verbatim vendored copy of the third-party windows-defender-remover tool, which is GPL-3.0-licensed; the rest of mt5-httpapi stays WTFPL. Documents the licensing of what the repo actually distributes.

## [v4.4.3] — 2026-07-26

Docs: hardened the `mt5-httpapi` agent skill with explicit destructive-operation guardrails and an auth/exfil-style warning. Renamed the safety section to `## Security & safety`, spelled out that trade/order/position mutations are irreversible with no client-side auto-retry, and made the "empty `api_token` = unauthenticated" warning more explicit. No behavior, endpoint, or API change.

## [v4.4.2] — 2026-07-25

CI: switch the ClawHub skill publish to `clawhub-publish.yml` directly — the `clawhub-skills-publish-workflow.yml` shim was removed upstream. No trading-path or API change.

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
