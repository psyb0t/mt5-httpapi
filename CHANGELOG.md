# Changelog

All notable changes to **mt5-httpapi**. Annotated git tags carry the full message for each release (`git show <tag>` or the GitHub Releases page) — this file is the readable digest.

The project follows [Semantic Versioning](https://semver.org/): patch = bug fixes / docs, minor = backwards-compatible features, major = breaking changes.

---

## [Unreleased]

## [v4.15.0]: 2026-09-30

`POST /compile` and everything under it was contributed by @Marinski in #16.

### Added

- `POST /compile` takes MQL5 source as JSON and returns the compiled `.ex5`, base64-encoded, with MetaEditor's log. Warnings do not fail a build, since MetaEditor exits non-zero on warnings too: success means a clean log and an actual binary. The log is decoded from MetaEditor's UTF-16LE. A compile error is a `422` carrying the diagnostics, the deadline is a `504`, and every response is JSON. See [Compiling MQL5](docs/compiling.md).

  The caller sends source text only. `filename` is reduced to a bare stem inside a per-request temp directory that is removed on every exit, and `/compile:`, `/log:` and `/inc:` are computed by the server. `#include`, `#resource` and `#property icon` paths that are absolute, UNC, contain a `..` segment or name a device are refused with a `400` before MetaEditor runs: measured on MetaEditor build 5836, `#include` otherwise reads any file the API process can, and quotes its tokens back in the log. The check splits lines the way the preprocessor does (`\r`, `\r\n`, backslash-newline continuations, form feed, vertical tab, and a comment between `#` and the directive name), and MetaEditor compiles the same line-normalized text the check read. `tests/real_compile/` checks this against a live deployment. `ea_version` is only logged, and must be 1 to 64 characters of `A-Z a-z 0-9 . _ + -`.
- Compiles are serialized across processes, not just threads: MetaEditor is single-instance per installation directory and every API process on a VM serves `/compile`. The lock is an OS byte-range lock on `.compile-inflight.lock` in the local mirror, or beside `MetaEditor64.exe` without one, which the OS releases when its holder exits or is killed. The mirror is refreshed under the same lock. At most two compiles wait behind the running one; the rest get an immediate `429` with `Retry-After`, so compile traffic cannot occupy every server thread.
- `compile_api_token`, a second bearer token accepted only on `/compile`, so a build pipeline or code generator can compile without holding the token that can trade. `api_token` keeps working everywhere, and leaving the new token empty changes nothing.
- Compile settings: `compile_terminal_dir` (default `terminals/metaquotes/base`), `compile_include_dir`, `compile_work_dir`, `compile_timeout` (default `30s`, ceiling 60s; a bare number is seconds), `compile_max_source_bytes` (2 MB, `413` before anything is written), `compile_max_ex5_bytes` (16 MB, checked before the binary is read), `compile_local_cache` and `compile_include_digests`. An invalid timeout or byte cap falls back to its default with a warning in the API log.
- A successful compile reports `include_hash`, a sha256 over the include tree MetaEditor was given, recomputed whenever a file in that tree changes. `compile_include_digests` names headers whose own digests are reported as `include_files`, so a caller can tell an edit of its own header from an upgrade of the stock library.
- `compile_local_cache` mirrors MetaEditor, its `Config` and the `MQL5` tree onto local disk, for installs whose terminals sit on a network or host-shared mount. On a 9p share a compile MetaEditor timed at 4.1s took 29s wall-clock, all of it loading the 105 MB binary. The include tree is re-checked against the source at most once a minute. A mirror that cannot be built falls back to the shared copy.

### Changed

- **Every API process schedules a MetaEditor warm-up** when `compile_local_cache` is set: 180s after start, one throwaway compile, so the first real caller does not pay the 30 to 55s cold load. One process per VM per boot runs it, claimed by a file in the cache that records the boot. It skips itself if a compile is running and logs, never raises, on failure.
- **nginx gives `/<broker>/<account>[/<instance>]/compile` a 180s read and send timeout.** The handler's worst case at the 60s ceiling is 150s. Every other route keeps nginx's 60s default.

## [v4.14.0]: 2026-09-29

The symbol-suffix, symbol-import, body-cap, journal-size and log-tail work below was contributed by @Marinski in #18.

### Added

- `POST /symbols/import` fills a terminal's symbol cache from a JSON list without calling the MT5 SDK, so a `mode: backtest` terminal can be primed. Each import is merged into the existing cache. `"complete": true` says the list is the broker's full book and replaces the cache instead. A partial import keeps the cache's `updated` stamp, so only a full book restarts its trust window. The body is bounded by `symbol_import_max_body_bytes` (2 MiB, `413` before parsing, `411` without a `Content-Length`), `symbol_import_max_symbols` (20000) and `symbol_import_max_symbol_length` (64). See [Market data](docs/market-data.md).
- Every request body is capped before it is parsed: `max_request_body_bytes` (4 MiB) for JSON, `max_upload_body_bytes` (25 MiB, matching nginx's `client_max_body_size`) for multipart `POST /backtest`. Over the cap is a `413`.

  For all five byte and count settings, zero, a negative or a non-integer falls back to the default with a warning in the API log. There is no value that turns a cap off.
- `scripts/measure-broker-offsets.py` reads each terminal's latest tick and prints the `utc_offset` to set, for re-measuring after a DST change. It only works against `mode: live` terminals. See [Installation and configuration](docs/installation-and-configuration.md).
- The rotator truncates a terminal journal over `MAX_LOG_BYTES` (default 2 GiB) once it has been idle for `IDLE_MINUTES` (default 30). One high-frequency backtest can fill the disk with today's journal long before the `RETAIN_DAYS` age pass reaches it. The file is truncated in place with its mtime kept, because the terminal holds it open. See [Operations](docs/operations.md).
- `tests/integration/test_mcpunifier.py` calls the unifier's `endpoints` tool over MCP and compares the result with the API's Flask routes in both directions.

### Changed

- **`GET /symbols` returns `409` on a `mode: backtest` terminal.** It used to call `mt5.initialize()` there, which starts `terminal64.exe` and holds the tester's data-dir lock, so every later backtest on that terminal came back empty. Callers that listed symbols on a backtest terminal get a `409` now and should use `POST /symbols/import`. The refusal does not wait for the MT5 lock.
- **Every other MT5 SDK route answers `503` on a `mode: backtest` terminal.** Account, orders, positions, history, market data and terminal info all went through the same reconnect path, which started `terminal64.exe` on a backtest terminal and left later backtests there with an empty report. They now refuse before any SDK call.
- **`start.bat` launches only the terminals in its VM's group.** `config_helper.py terminals` now applies the same `config/vm-group.txt` filter that `check_health.py` and the container healthcheck already use. Before, every VM in a multi-VM install prepared and served every terminal, including another VM's live terminal on the same shared data dir. A single-VM install, or one with no group file, still gets every terminal.

### Fixed

- `symbol_suffix` is no longer appended to a symbol the broker only carries bare. Brokers often suffix part of their book: Eightcap Global has `EURUSD.i` but a bare `XAUUSD`, so every non-FX backtest there asked for a symbol that does not exist. The suffix is skipped only when a complete symbol list, written by an unfiltered `GET /symbols` on a live terminal or by an import with `"complete": true`, has the bare name and not the suffixed one. With no cache, a partial one, or one older than `symbol_cache_max_age` (default `7d`; a bare number is seconds), the suffix is appended as before.
- Log tails (`GET /backtest/<id>/tail`) read at most the last 256 KiB of the file. A multi-gigabyte Tester log used to be read whole on every poll, which held the process long enough to fail `/ping` and the healthcheck.

## [v4.13.2]: 2026-09-29

### Fixed

- Backtest tester processes are now recognised inside the Windows VM. There, `Desktop\Shared` is a link to `\\host.lan\Data`, and Windows reports a process's executable by the resolved path, so matching only the configured `C:\Users\Docker\Desktop\Shared\...` path found nothing. The self-relaunch wait never saw the replacement terminal, so those jobs failed as `Report not generated` while the run carried on, and the timeout and startup cleanups killed nothing, leaving `metatester64.exe` agents holding their ports. The resolved path now matches too, and a sibling directory still never does. Contributed by @Marinski in #22.

  After upgrading, the timeout and startup cleanups actually stop this terminal's `terminal64.exe` and `metatester64.exe` processes, which they silently skipped before.

- The resolved terminal path is cached only once the link actually resolves. A lookup made while the share is still unreachable no longer hides the resolved path until the API restarts, and a failed lookup is logged instead of ignored.

### Fixed

- **A wedged SDK call let every later request re-wedge instead of failing fast** (`mt5api/mt5client.py`). `_run_with_timeout` abandons its worker thread on a 30s timeout — Python cannot interrupt a thread blocked inside the MT5 SDK's C extension — and `session()` releases `_mt5_lock` regardless, so the zombie stays inside the single-connection, non-threadsafe SDK while the next request calls in alongside it. Traced to a real 2026-08-23 incident: every API instance on a VM logged waitress's `Task queue depth is 66` and `/ping` took 60+ minutes, because each of ~66 requests independently paid the full 30s timeout serialized behind the same wedged connection. A new single-flight guard now refuses to start another SDK call while a prior worker is still alive — new `MT5Wedged(MT5Timeout)`, mapped by `with_mt5` to `503` + `Retry-After: 30` (naming the stuck function and its age) instead of running out its own timeout. `restart_terminal`'s own post-kill reconnect is the one call exempted from the guard it may have just tripped (`allow_wedged=True`), otherwise the monitor would kill the terminal again every cycle with "reconnection failed" forever. `ensure_initialized`/`init_mt5` propagate `MT5Wedged` rather than swallowing it into a generic "not initialized" result, so the ~24 handler call sites that gate on `ensure_initialized()` actually surface the guard's 503 instead of a plain one with no `Retry-After`. `/ping` (still lock-free) now reports `sdk_threads_alive` / `sdk_oldest_alive_s`, and every WEDGED/TIMEOUT log line carries the process mode, request path and live worker count — the incident above was unrecoverable partly because none of that was captured at the time. Tests: `tests/test_mt5client.py`, new `tests/test_wedge_watchdog.py`.

- **A wedged process had no way back except a VM recreate — now it exits and relaunches itself.** A new `mt5-wedge-watchdog` daemon thread (`mt5api/wedge_watchdog.py`, started in every mode from `main.py`) exits the process (`os._exit(75)`) once the oldest live SDK worker has been stuck longer than `MT5_WEDGE_EXIT_SECONDS` (default 180s). 37 of the fleet's 38 terminals run in backtest mode, which never starts the existing health monitor, so this is the only thing that can act on a wedge there; in that mode the exit defers while a tester run holds `RUN_LOCK`, but only up to `MT5_WEDGE_DEFER_CEILING_SECONDS` (default 1800s) measured from when it first wanted to exit — queued jobs re-acquiring the lock back-to-back (this fleet runs them up to ~6h) must not extend that ceiling, or the defer is effectively permanent. `api_runner.bat`'s single-shot Python launch is wrapped in a relaunch loop with backoff, giving up after 5 exits in 10 minutes — applied as a deploy-time hook (`backend/scripts/patch-api-runner-bat.py`, run by `deploy-mt5-scripts.sh`) rather than committed to the submodule, since `api_runner.bat` stays upstream. New settings: `MT5_WEDGE_EXIT_SECONDS`, `MT5_WEDGE_CHECK_INTERVAL_SECONDS`, `MT5_WEDGE_DEFER_CEILING_SECONDS`. See `docs/spec/mt5-httpapi-sdk-call-thread-leak.md` for the full design and open questions.

## [v4.13.1]: 2026-09-10

### Changed

- Updated the Wickworks sidecar to v0.7.0. Technical-analysis requests now
  use its canonical OHLCV `volume` field. The existing `recentBars` request
  field remains accepted for compatibility and is not forwarded to Wickworks.

### Fixed

- The log rotator now prunes dated MT5 terminal, Tester, and Tester Agent
  journals under `data/shared/terminals/`, using the same configurable
  `RETAIN_DAYS` window as shared API logs. It leaves expert logs, MetaEditor
  logs, reports, and backtest jobs alone.

## [v4.13.0]: 2026-09-10

### Added

- A Compose-managed `vm-watchdog` sidecar now watches this project's Windows
  VM containers and recreates a VM only after it has remained unhealthy for the
  configured streak. Recovery uses the existing `recreate-vm.sh` path, so the
  VM and its shared-network sidecars are rebuilt together. The watchdog has
  scoped image and Compose-project filters, exponential backoff, a bounded
  attempt budget, dry-run mode, and documented configuration defaults.

- Shared-network sidecars with their own healthchecks are supervised after
  their VM is continuously healthy. This repairs a sidecar stranded in an
  obsolete network namespace without unnecessarily recreating the VM. Set
  `WATCHDOG_WATCH_SIDECARS=0` to retain VM-only recovery.

### Changed

- VM port checks now run concurrently. The healthcheck has a bounded wall-clock
  time independent of terminal count, deduplicates configured ports, and
  distinguishes a responsive terminal, a temporarily busy one, and a
  persistently hung one.

- `run.sh` persists `MT5_PROJECT_DIR` for later Compose commands, and recovery
  explicitly stops VMs with the configured grace period before recreating them.

### Fixed

- `make lint` no longer reports a false clean result when PSScriptAnalyzer is
  unavailable. The lint image treats module installation and import failures as
  fatal and validates the exact analyzer version before scanning scripts.

## [v4.12.2] — 2026-08-20

### Fixed

- **`make verify-binaries` now catches MetaTrader `.ex5` artifacts.** The
  gate recognizes the EX5 header and any `.ex5` filename, so a compiled MQL5
  program cannot bypass the manifest merely because it is not a PE, ELF, or
  Mach-O file.

- **The host-side per-VM health filter never ran.** `check_health.py` imports
  `_vm_group_filter`/`_in_group` from `scripts/config_helper.py`, but neither
  name was ever implemented there, so the import always hit its permissive
  fallback and the `start.bat` status loop probed every port in `config.yaml` —
  the same permanently-unhealthy multi-VM behaviour the container healthcheck
  (the awk filter in `healthcheck.sh`) already scopes. Both hooks are now
  implemented, mirroring the awk contract: they read the same per-VM group
  file (`config/vm-group.txt`) docker-compose bind-mounts, treat a missing or
  empty file as "no filter" so single-VM installs are unchanged, and match
  broker/account/instance the same way the `vm_group` writer emits them. The
  strict `xfail` that recorded the gap is replaced with behavioral tests.

- **Backtest cleanup now stays inside its terminal instance.** Startup no
  longer fails sibling jobs that share the host job directory, stale tester
  processes no longer hold agent ports after a timeout, and failure reports
  use the latest terminal log rather than an old MetaEditor log.

- **Wickworks follows the VM lifecycle.** The sidecar now detects an orphaned
  network namespace and Compose recreates it with the VM instead of leaving
  technical-analysis requests pointed at a dead gateway.

## [v4.12.1] — 2026-08-08

Documentation. No code changed.

- The README documented the make targets but never mentioned
  `make verify-binaries`, even though `make test` runs it first, so the
  binary-manifest gate was invisible to anyone reading the docs to find out what
  CI enforces. Added to the target list and given a short section explaining
  `assets/binaries.lock.json` — what it records, and what to do when a new
  executable has to be vendored.

## [v4.12.0] — 2026-08-06

Most of what this stack does is now tested in CI, and every vendored binary has
to prove what it is.

### Added

- **`make verify-binaries` — a gate for vendored executables.** Every tracked
  executable must be declared in `assets/binaries.lock.json` with its SHA-256,
  size, upstream URL and observed code-signature state. An undeclared binary
  fails the build, a changed one fails the build, and a signature that
  degrades fails the build. It runs as the first step of `make test`, so CI
  enforces it on every pull request. `scripts/verify_binaries.py` parses the PE
  certificate directory and recomputes the Authenticode digest rather than
  trusting that a file is what its filename says.

  A repo that vendors executables has one hard problem: nobody can read them. A
  reviewer skims thousands of lines of source and waves through the binary
  beside it because there is nothing to skim. This makes that impossible to do
  silently.

- **`BACKTEST_MAX_TIMEOUT`** (default `48h`) bounds the `timeout` form field on
  backtest submission. The terminal's run lock is held for the whole timeout,
  so an unbounded value could park a terminal indefinitely.

- **Roughly 130 new tests**, taking the unit suite from 244 to 379:
  - `tests/test_live_api_contract.py` drives every flow that previously existed
    only in `tests/real/` (account, symbols, rates, market and limit orders,
    position management, history, ping, TA) through the real Flask app against
    a scripted SDK — so the HTTP contract is exercised in CI without a
    terminal, including asserting the request dictionaries actually sent to the
    SDK.
  - `tests/test_mt5client.py` and `tests/test_monitor.py` cover two modules
    that had no direct tests.
  - Five `clients/go/*_test.go` files cover every exported method on the Go
    client — field decoding, error mapping and request encoding.
  - `tests/test_healthcheck_behavior.py` runs the real `healthcheck.sh` awk
    program against fixture configs, replacing assertions that only checked
    whether certain substrings appeared in the script's source.

- **Line-coverage floor.** `make test-unit` now reports coverage for `mt5api/`
  and fails below 62% (currently 75%).

### Fixed

- **`parse_duration_to_seconds` raised `OverflowError` on non-finite input.**
  `float("inf")`, `"1e400"` and friends passed the bare-number fast path and
  then overflowed `int()`. Callers catch `ValueError`, so a `timeout=inf`
  submission surfaced as a 500 instead of a 400. All three paths — the numeric
  branch, the bare-number branch and the unit branch (`"9"*400 + "h"`
  overflows too) — now reject non-finite values as `ValueError`.

- **`prune_old_jobs` removed a directory named by data read back from disk.**
  The per-job staging directory was resolved from the `jobId` inside the job's
  own state file, then removed with `shutil.rmtree`. Job IDs are always
  `uuid.uuid4().hex`, but the delete now requires that shape before it touches
  anything, and logs a warning otherwise.

- **`scripts/check_health.py` degraded silently.** Its per-VM filter import sits
  behind `except ImportError` with permissive no-op fallbacks, so a failure
  there quietly restored the probe-everything behaviour the filter exists to
  prevent. It now says so on stderr.

### Known

- `scripts/defender-remover/PowerRun.exe` is vendored from the defender-remover
  toolkit rather than from Sordum directly. Its certificate directory is not a
  well-formed `WIN_CERTIFICATE`, so its signature cannot be validated, and its
  hash matches no upstream Sordum release. It is not known to be malicious —
  repacking is normal for that toolkit — but it is unverifiable, and
  `assets/binaries.lock.json` records exactly that. `make verify-binaries`
  warns about it on every run and fails if it ever changes.

- `scripts/check_health.py` imports `_vm_group_filter` and `_in_group` from
  `scripts/config_helper.py`, which does not define them, so that import always
  falls back and the Python-side per-VM filter never actually runs. The
  container-side filter in `scripts/healthcheck.sh` is unaffected and is
  covered by the new behavioural tests. Recorded as a strict `xfail` in
  `tests/test_terminal_instances.py` so implementing the functions turns into a
  visible failure until the marker is removed.

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

- **`make test-mcpunifier`** — an end-to-end test for the MCP unifier, which had no automated coverage: `scripts/lint.sh` only reaches `.ps1` and `.sh` files, so nothing verified `mcpunifier/`. `scripts/test-mcpunifier.sh` builds the unifier image, stands it up beside a stub terminal on a scratch network, and asserts seven behaviours — health, the 25-tool surface, `list_terminals` reporting every configured terminal, a live terminal routing to its own port, a configured-but-down terminal failing only the calls that name it, a broker/account pair that is not configured being refused rather than routed, and the service staying healthy after both failures.
- Every resource the harness creates carries one name prefix and is removed by an `EXIT` trap, so a pass, a failure and an interrupt all leave nothing behind. It also sweeps that prefix on entry, so a run killed outright — where the trap never fires — is cleaned up by the next invocation. On a readiness timeout it dumps each container's state and last log lines *before* tearing down, since the teardown would otherwise destroy the only evidence of why the run failed.
- Fixtures are written under the gitignored `.data/` rather than `/tmp`: a bind-mount source is resolved on the docker daemon's filesystem, so a `mktemp -d` directory that exists only in the caller's namespace would be bound as an empty dir and the service would start with no configuration.

## [v4.9.1] — 2026-07-28

### Fixed

- **A missing `mcpunifier` container no longer stops nginx from starting and takes every other route down with it.** v4.9.0 generated `location /mcp/` with a literal `proxy_pass http://mcpunifier:6600/`. nginx resolves a literal upstream hostname while *parsing* the config, so on a deployment without that container nginx aborts with `host not found in upstream "mcpunifier"` — and every per-terminal route plus the whole REST API returns 502 behind it. Because `docker-compose.yml` is gitignored, pulling v4.9.0 delivered the new `scripts/config_helper.py` without the service it referenced, so the next restart broke the stack.
- The upstream now routes through a variable (`set $mcp_upstream …;` then `proxy_pass $mcp_upstream;`) with an explicit `resolver`, which defers the lookup to request time. nginx starts whether or not the container exists, every terminal route serves normally, and only `/mcp/` returns 502 until the unifier is running — which makes the service genuinely optional, as it needs to be.

## [v4.9.0] — 2026-07-28

### Added

- **Unified MCP endpoint at `/mcp`, spanning every configured terminal.** Each terminal already had its own MCP server at `/<broker>/<account>/mcp`, and a session was permanently bound to whichever one it connected to — an MCP session has a fixed tool catalog, so there was no per-call slot to name a terminal. The new endpoint exposes the same 24 tools, each taking `broker` and `account` (plus optional `instance`), so one session can drive every terminal.
- **`list_terminals`**, reporting each configured terminal's broker, account, instance and whether it is a live or demo account. Every other tool refuses a broker/account pair that is not configured and answers with the valid list, rather than routing to something plausible but wrong.
- **`mcpunifier` service** (`mcpunifier/`, `Dockerfile.mcpunifier`) — a Linux container running beside the Windows VM. It reads the same `config/config.yaml` that generates the nginx routing, so it cannot route somewhere nginx does not, and reaches each terminal directly on that terminal's own port. `nginx` proxies `/mcp/` to it.

### Notes

- **Nothing existing changes.** The per-terminal `/<broker>/<account>/mcp` endpoints and the whole REST surface are untouched. Which endpoint a client reaches depends only on the URL it is pointed at: a root URL gets the unified tools, a `/<broker>/<account>` URL gets that terminal's existing tools.
- **No startup coupling and no shared failure.** The unifier never waits on a terminal — the routing table is static and is not re-probed. A terminal that is down fails only the calls naming it and leaves the rest usable; successful responses carry the `terminal` key that answered. `/health` reports whether the unifier can route, never a terminal's state.
- The unified endpoint is gated by the same bearer token as the REST API. The service runs as a non-root user with a read-only root filesystem and all Linux capabilities dropped, and mounts `config/config.yaml` read-only.
- `scripts/config_helper.py` now refuses to generate nginx config for a broker literally named `mcp`, which would otherwise shadow the unified route.

### Changed

- **Docs and plugin manifests now describe both MCP endpoints.** The README's MCP section, the Claude Code manifest's `api_url` prompt, and the OpenClaw bridge's `MT5_API_URL` all previously described the base URL as terminal-scoped only, which was the entire truth before this release and is now half of it. Each states that the server root reaches every terminal while a `/<broker>/<account>` path pins one, so the value a user is prompted for no longer steers them into single-terminal mode without mentioning the alternative.

## [v4.8.4] — 2026-07-27

### Fixed

- The README's Codex subsection under `## Agent integrations` stopped after `codex plugin marketplace add psyb0t/agents` and never told the reader how to actually install the plugin. Added the missing command, `codex plugin add mt5-httpapi@psyb0t`.
- Clarified that skill invocation differs by install path: a marketplace-installed skill invokes as `$mt5-httpapi:mt5-httpapi`, while a skill Codex picks up automatically from a repo's own `.agents/skills/` invokes as plain `$mt5-httpapi`.

## [v4.8.3] — 2026-07-27

### Added

- **Codex plugin manifest** (`.agents/.codex-plugin/plugin.json`) — points `skills` at `.agents/skills/`, so mt5-httpapi installs as a distribution channel via `codex plugin marketplace add psyb0t/agents`. The skill itself already worked in Codex with zero files (native `.agents/skills/` scanning); this only adds discovery.
- **`## Agent integrations` README section** with copy-pasteable install commands for Claude Code, Codex, and the OpenClaw skill + MCP-bridge plugin, linked from the Table of Contents.

### Fixed

- The README's prior Claude Code install snippet pointed `claude plugin marketplace add` at this repo directly, which has no `marketplace.json` and would fail. It now points at the shared catalog, `psyb0t/agents`.

### Removed

- Deleted the stray `.claude-plugin/marketplace.json` from this repo — marketplaces register by name and collide across repos; the catalog now lives solely in `psyb0t/agents`.

## [v4.8.2] — 2026-07-27

### Added

- Added a GitHub Actions CI status badge to the README.

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
