# Changelog

All notable changes to **mt5-httpapi**. Annotated git tags carry the full message for each release (`git show <tag>` or the GitHub Releases page) — this file is the readable digest.

The project follows [Semantic Versioning](https://semver.org/): patch = bug fixes / docs, minor = backwards-compatible features, major = breaking changes.

---

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
