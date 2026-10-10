# Changelog

All notable changes to **mt5-httpapi**. Annotated git tags carry the full message for each release (`git show <tag>` or the GitHub Releases page) — this file is the readable digest.

The project follows [Semantic Versioning](https://semver.org/): patch = bug fixes / docs, minor = backwards-compatible features, major = breaking changes.

---

## [Unreleased]

### Fixed

- **Terminals no longer abort at launch with exit code 10053 on a VM running many of them.** Every window, console and terminal in the VM's session takes memory from its desktop heap, which Windows caps at 20 MB. With 20 or more terminals running it ran out (`Win32k` event 243, `A desktop heap allocation failed`), and every terminal launched after that exited about a second after starting, before it signed in or started the tester, so backtests failed with `terminal64.exe exited with code 10053` in bursts. `start.bat` now raises the interactive desktop heap to 64 MB at boot through `scripts/desktop_heap.py` and reboots once for it to apply. See [docs/operations.md](docs/operations.md#many-terminals-per-vm-the-desktop-heap).

## [v4.28.1]: 2026-10-10

### Changed

- Documentation only. The agent skill section names [peen](https://github.com/psyb0t/peen) as an example of a compatible agent. See [docs/mcp-and-agents.md](docs/mcp-and-agents.md).

## [v4.28.0]: 2026-10-10

Based on #29 by @Marinski, extended from backtests to live terminals.

### Added

- **`disable_terminal_mcp` turns off MetaTrader's own MCP servers.** Terminal build 6090 and later starts MCP servers of its own (the transport behind its built-in AI assistant; not this API's `/mcp`) from `Config/assistant.ini`, and each tries to listen on `127.0.0.1:22346`. With several terminals in a VM all but the first log `MCP bind error on 127.0.0.1:22346 (10048)` on every launch, and backtest terminals have aborted with exit code 10053 after the same subsystem failed to sign in to MQL5.community. With `disable_terminal_mcp: true` in `config.yaml`, `Enable=0` goes into `[MCP.MetaTrader]` and `[MCP.MetaEditor]` before every launch: at boot, on a restart the API makes, and before each backtest. MT5 rewrites the file on exit, so it happens every time; endpoints, API keys and other sections are kept. A terminal entry's `disable_terminal_mcp: false` keeps its own servers on. Off by default. See [docs/installation-and-configuration.md](docs/installation-and-configuration.md).

## [v4.27.0]: 2026-10-09

### Added

- **The scheduled VM reboot waits for running work.** Every `reboot_interval` minutes the VM rebooted no matter what, cutting off a running backtest or an order, stop loss change, deployment or file write halfway. The reboot now first asks every API on the VM what it is doing and waits while any of them runs a backtest or a request that changes something. Reads never hold it. Each wait goes to `full.log` with the terminal and the reason (`reboot postponed (reason=busy ...): teletrade/demo/default: backtest ... running`), and so does the reboot (`reason=idle`, or `reason=max_postpone` with what was still busy). Once everything is idle, the APIs refuse new writes for the last seconds with 503 `REBOOT_PENDING` and `Retry-After: 120`, so nothing starts just before the reboot. See [docs/operations.md](docs/operations.md#scheduled-reboots).
- **`reboot_max_postpone`** in `config.yaml` caps that wait in minutes (default `360`, the default backtest timeout). `0` reboots on schedule without waiting.
- **`GET /busy`** reports whether a reboot now would break work in that terminal's API: `busy`, `reasons`, the queued and running `backtests`, the `writes_in_flight` with method, path and age, and `draining`. See [docs/rest-api.md](docs/rest-api.md#health).

### Changed

- The agent sign-off rule in `.agents/rules/chat.md` now also covers pull request, issue and discussion comments, other messages, and commit message bodies (CHAT3, CHAT4). Code, docs, the CHANGELOG and tag messages stay without it.

### Fixed

- **Downloading a file deleted a moment earlier answered 500.** Windows keeps a deleted entry visible briefly while the delete is pending, so `GET /files/<path>` could find it and then fail to open it. It now answers 404 `NOT_FOUND`.

## [v4.26.2]: 2026-10-09

### Fixed

- **The CI Docker Hub login failed the `test` job when Docker Hub's token endpoint timed out.** v4.26.1 logged in with a single attempt, and three runs in a row timed out on it, so the job stopped before running a test and nothing was published. `scripts/ci-dockerhub-login.sh` now does the login: it retries five times with a growing pause and, if every attempt fails, lets the job carry on with anonymous pulls and a warning on the run. This release is the first since v4.24.0 to publish the skill and the OpenClaw plugin to ClawHub.

## [v4.26.1]: 2026-10-09

### Fixed

- **CI failed before running any test.** The `test` and `lint` jobs pull their base images from Docker Hub without logging in, and Docker Hub refused with 429 because GitHub's shared runner IPs had used up the anonymous pull limit. Both jobs now log in to Docker Hub first, except on pull requests from forks, which get no secrets. The v4.25.0 and v4.26.0 tag pipelines failed this way, so neither published the skill or the OpenClaw plugin to ClawHub; this release publishes both.

## [v4.26.0]: 2026-10-09

### Added

- **`DELETE /sets/<name>` removes a staged `.set` file.** Sets could be uploaded but not removed, and the file API cannot remove them either, since it only reads the terminal's `chartctl/` folder. The route answers 409 `IN_USE` while any deployment names the set, paused ones included, 403 `HOST_ASSET` for a host-managed set, and 404 when nothing is staged under that name. Both MCP endpoints get a `delete_set` tool, and `make test-live` now removes the `livetest-` sets it stages. See [docs/chart-deployments.md](docs/chart-deployments.md).
- **Example: an expert that uses a library.** [docs/files.md](docs/files.md#example-an-expert-that-uses-a-library) walks through uploading a library's headers to the compile tree, compiling an `#import`ed `.ex5` library (or bringing a DLL) and putting it in the terminal's `MQL5/Libraries`, compiling the expert against it and deploying it, and replacing a library a running expert has loaded.

## [v4.25.0]: 2026-10-09

### Added

- `make test-live` has an opt-in terminal restart test (`MT5_LIVE_RESTART=1`). A probe expert holds a file open and calls `WebRequest()`. The test checks that the file API reports the file locked, restarts the terminal through the API, and checks that the deployment comes back on exactly one chart and that the restarted probe's `WebRequest()` still works, which happens only when the API re-applied the allowlist. See [operations](docs/operations.md).

### Changed

- **A missing or wrong bearer token answers JSON.** REST routes and a terminal's `/mcp` answered 401 with an HTML page. They now answer `{"error": "unauthorized", "code": "UNAUTHORIZED"}` like every other error.
- **Every Python dependency is pinned to an exact version.** `requirements-api.txt`, the VM's `start.bat` and `install.bat`, `requirements-mcpunifier.txt`, `requirements-test.txt` and `Dockerfile.test` name exact versions, and a unit test fails when one of them loses its pin or drifts from the API's versions. The API keeps the versions the VM already ran.

### Fixed

- **A hard terminal stop still left a deployment on two charts.** MT5 writes a chart's expert into the saved profile only when it exits cleanly. `POST /terminal/restart`, the health monitor's recovery and a VM restart stop the terminal hard, so MT5 restored the deployment's chart without its expert, and loader 1.0.3 opened a second chart beside the bare one. Loader 1.0.4 re-applies the deployment's template to that restored chart instead, once, when its 30 second startup grace ends, and only for deployments it owned a chart for before the restart. It also re-arms a chart it owns whose expert is gone, where it used to report that chart as running. `make up` compiles and installs the new loader. See [docs/chart-control-protocol.md](docs/chart-control-protocol.md#rules).
- **Downloading a file an expert holds open broke the response.** `GET /files/<path>` opened the file only while sending it, so a file opened without sharing (an expert's `FileOpen`, the terminal's own files) failed mid-response. It now answers 409 `FILE_LOCKED`, as `PUT` and `DELETE` already did.
- **nginx cut `POST /terminal/restart` off after 60 seconds.** The call waits for the terminal to come back, which can take longer. The route now gets 300 seconds, like the file API and the WebRequest routes.
- **The VM's `.bat` scripts are plain ASCII.** Some carried non-ASCII characters in comments and messages. `make lint` now checks `.bat` and `.cmd` files as well as `.ps1`.

### Deprecated

- **The camelCase JSON keys go away in v5.0.0.** The README, [backtesting](docs/backtesting.md) and the skill now name the release.

## [v4.24.0]: 2026-10-09

### Added

- **`AGENTS.md` and `.agents/rules/`.** `AGENTS.md` is the guide for coding agents working on this repository: what each component does, how code reaches the VM, the layout, the Make targets and test layers, the conventions, and how releases work. `.agents/rules/` holds the project rules, one file per topic (architecture, Python code, API design, MCP, testing, the Windows VM, security, docs and releases, chat), each rule with a stable ID such as `API1` or `TST3`.

### Changed

- **CI validates the ClawHub skill and plugin on every push.** The pipeline's ClawHub job names the `mt5-httpapi` skill and plugin explicitly and runs the reusable workflow's validation on every push and same-repository pull request, not only on tags. Publishing is still tag-only and still waits for the test and lint jobs. Pull requests from forks skip it, since they get no ClawHub token.

- **JSON keys are snake_case everywhere.** The backtest routes (`POST /backtest`, `GET /backtest/<job_id>`, `GET /backtest/<job_id>/tail`, including the `summary` and `optimization_cache` objects) and the TA route's wickworks error answered in camelCase while everything else, including the MT5 fields the API passes through, used snake_case. They now answer in snake_case (`job_id`, `started_at`, `poll_after_seconds`, `net_profit`, `wickworks_status`, ...). `POST /backtest/build-ini` accepts its fields in snake_case (`from_date`, `last_days`, `latency_ms`, ...) and `POST /backtest` accepts `top_passes`. Optimization result rows keep the report's column names and the EA's input names as they are.

### Deprecated

- **The camelCase keys.** Responses still carry every old camelCase key next to its snake_case twin, and request bodies still accept camelCase, so existing clients keep working. Both will be removed in a later release; move clients to the snake_case names now. Sending a field under both names with different values is refused with 400.

## [v4.23.0]: 2026-10-09

### Added

- **File listings carry what `ls -al` shows.** Each entry now has `size_human`, `mode`, the Windows `attributes` (`readonly`, `hidden`, `system`, `archive`, ...), `nlink`, created, modified and accessed times as epoch seconds and as UTC ISO 8601, and `is_symlink`, with `link_target` and `link_outside_tree` for a symlink or junction. The listing itself adds `count` and `total_size`. See [docs/files.md](docs/files.md#endpoints).
- `make test-live` now covers the file API's REST routes through nginx against the terminal's real file system: binary, multipart and urlencoded uploads, a 20 MiB upload, the 25 MiB cap, overwrites, case-insensitive paths, zip extraction into the terminal tree, bad and zip-slip archives, protected paths, Windows device names, recursive deletes and the listing fields.

### Fixed

- A symlink inside a listed directory that pointed out of the tree made the whole listing fail with 400. It is now listed, flagged `link_outside_tree`, and neither readable nor writable.

## [v4.22.1]: 2026-10-09

### Fixed

- **`GET /<broker>/<account>/files` answered nginx's 301.** v4.22.0 gave the file routes a longer timeout through an nginx location ending in `files/`, and nginx redirects a bare `/files` to `/files/` for such a location, which the API does not serve, so the root listing was unreachable through nginx. The locations no longer end in a slash.

## [v4.22.0]: 2026-10-09

### Added

- **File API.** `GET`, `PUT` and `DELETE` on `/files/<path>` list, download, upload and delete files in a terminal's install directory, the folder holding `terminal64.exe`: libraries in `MQL5/Include`, DLLs and `.ex5` imports in `MQL5/Libraries`, an expert's data in `MQL5/Files`, the logs. `PUT /files/<dir>?extract` unpacks a zip into that directory, merging with what is there, and never stores the zip. `/compile/files/...` does the same on the MQL5 tree `POST /compile` builds against, so a library uploaded there is what the next compile includes, in every API process at once. Both MCP endpoints get `list_files`, `get_file`, `put_file` and `delete_file`, and `list_terminals` reports `files` per terminal. Opt-in with `files.enabled: true` (`files: false` on a terminal opts it out). The broker credentials (`mt5start.ini`, `Config/accounts.dat`) are never served, the terminal's executables and Chart Deployments' own files are read-only, and every path is checked against `..`, absolute and drive paths, Windows device names and symlinks out of the tree. Archives are checked entry by entry before anything is written, and capped by `files.max_extract_bytes` (200 MiB unpacked) and `files.max_extract_files` (10000). See [docs/files.md](docs/files.md).

### Fixed

- **The WebRequest allowlist is re-applied after every terminal restart the API makes.** It used to be re-applied only when the API process started, so after the health monitor's recovery restart or `POST /terminal/restart` an expert's `WebRequest()` was refused until someone called `POST /webrequest/apply`.
- **Resuming a paused deployment could put two deployments on one chart.** `PATCH /deployments/<id>` with `"enabled": true` skipped the duplicate check `POST /deployments` makes, so a deployment paused while another took its symbol and timeframe could be resumed beside it. It is now refused with 409 `DUPLICATE_CHART`.
- **nginx cut long calls off after 60 seconds.** `PUT /webrequest` and `/webrequest/apply` can wait minutes for the GUI lock, and a terminal's own `/mcp` proxies them, so callers got nginx's HTML 504 while the work carried on. Those routes and the file API now get 300 seconds, like the unified `/mcp/`.
- **`chartctl: true` crashed the API and the boot provisioning.** A bare `true` (or `false`) is now shorthand for `{enabled: true}`, in config.yaml's `chartctl` and `files` blocks alike. Any other non-mapping value still stops the MCP unifier with a config error, and the API treats it as off.

## [v4.21.1]: 2026-10-09

### Fixed

- The live TA tests (`tests/live/test_rates_ta.py`) sent indicators in an old `{"type", "params": {"period"}}` shape that wickworks rejects, and reported every 502 as "wickworks unavailable", so they skipped instead of failing. They now send the flat spec from [market data](docs/market-data.md), check that each requested key comes back, and skip only when wickworks cannot be reached.

## [v4.21.0]: 2026-10-09

### Changed

- **One live suite.** The live-trading tests (`tests/real/`) and the `/compile` sandbox tests (`tests/real_compile/`) moved into `tests/live/`, and `make test-live` runs all of it against the one `MT5_LIVE_*` target, real orders included. Name modules in `LIVE_TESTS` to run a subset, for example to leave the order tests out. `tests/real/run.sh` and its variables are gone: `MT5_API_URL` and `MT5_API_TOKEN` become `MT5_LIVE_URL`, `MT5_LIVE_BROKER`, `MT5_LIVE_ACCOUNT` and `MT5_LIVE_TOKEN`; `MT5_TEST_SYMBOL`, `MT5_TEST_VOLUME` and `MT5_TEST_MAGIC` become `MT5_LIVE_SYMBOL`, `MT5_LIVE_VOLUME` and `MT5_LIVE_MAGIC`; `MT5_COMPILE_URL` and `MT5_COMPILE_TOKEN` follow the same target, and the depth overrides become `MT5_LIVE_COMPILE_WORK_DEPTH` and `MT5_LIVE_COMPILE_INCLUDE_DEPTH`. The order volume now defaults to the symbol's minimum, and the order tests skip on a non-demo account unless `MT5_LIVE_ALLOW_REAL=1`, the same as the deployment tests. `MT5_LIVE_TRADING` is gone.

## [v4.20.0]: 2026-10-09

### Added

- **`make test-live`** tests a running stack. Set `MT5_LIVE_URL`, `MT5_LIVE_TOKEN`, `MT5_LIVE_BROKER` and `MT5_LIVE_ACCOUNT` (or fill `tests/live/.env`) and it checks the REST API, both MCP endpoints and the route catalog against that terminal. If the terminal has Chart Deployments on, it also compiles a probe expert that never trades, deploys it, and checks the chart in screenshots. Steps that change state run only on a demo account unless `MT5_LIVE_ALLOW_REAL=1`. `MT5_LIVE_TRADING=1` adds the real-order suite in `tests/real`. See [operations](docs/operations.md#testing-a-running-stack).

### Fixed

- **A newly uploaded expert could not be deployed until the terminal restarted.** MT5 only loads an `.ex5` it saw on disk at startup or after a Navigator refresh, so a template naming a freshly uploaded expert opened an empty chart and the deployment failed with `EXPERT_NOT_ATTACHED` on every retry. Every successful `POST /experts` now refreshes the terminal's Navigator inside the Windows VM through the bundled AutoIt (`assets/autoit/refresh_navigator.au3`), and so does the first deployment of a host-managed expert. The response's `navigator_refresh` is `ok`, `failed` (upload the same file again to retry) or `unavailable` (no GUI automation, as on bare metal, where the terminal still needs a restart first).
- **The loader lost track of charts whose expert calls `Comment()`.** Loader 1.0.2 marked a deployment's chart by writing `chartctl:<id>` into the chart comment, which any expert calling `Comment()` overwrites, and wrote over the expert's own comment in turn. Loader 1.0.3 keeps the chart-to-deployment map in `MQL5\Files\chartctl\owned.json` instead and never touches the chart comment. Charts the map does not know, such as the ones MT5 restores under new ids after a terminal restart, are adopted by expert, symbol and timeframe as before.
- **A terminal restart could leave a deployment running on two charts.** The loader starts before the experts on MT5's restored charts have loaded, so it saw no chart to adopt and opened a second one, and the restored copy kept running its own expert unmanaged. Loader 1.0.3 only adopts during its first 30 seconds and opens no new charts. It also forgets a chart only once `ChartClose` succeeds, so a failed close no longer looks like a closed chart.
- `run.sh` (`make up`) now builds images as it starts the stack. It used to start whatever image was built first, so an update never reached the MCP unifier.
- `POST /webrequest/apply?script=` with an unknown script name answers 400 instead of an HTML 500.

## [v4.19.0]: 2026-10-09

### Added

- **Chart Deployments over MCP.** Both MCP endpoints have typed tools for the chartctl and WebRequest routes: `upload_expert`, `list_experts`, `delete_expert`, `upload_set`, `list_sets`, `get_set`, `create_deployment`, `list_deployments`, `get_deployment`, `update_deployment`, `delete_deployment`, `reconcile_deployments`, `list_charts`, `get_loader`, `screenshot_chart`, `close_chart`, `get_webrequest`, `set_webrequest` and `apply_webrequest`. Before this an agent could reach those routes only through the generic `request` tool, which sends and reads JSON, so it could not upload an `.ex5` or `.set` file or get a screenshot back. The upload tools take the file as base64 (`upload_set` also takes plain text, and wrapped base64 is accepted) and send the multipart form the REST API expects. `screenshot_chart` returns the PNG as MCP image content. Name and id arguments are percent-encoded into the URL, and a value that would reach a different route, such as `../deployments/<id>`, is refused before any request.
- On a per-terminal `/mcp` endpoint these tools are registered only when chartctl is enabled for that terminal. The unified `/mcp` endpoint always has them. Its `list_terminals` now reports `chartctl` per terminal, using the same rule as the API, and a chartctl tool called on a terminal without the routes says so, and why, instead of returning a bare 404. The unified endpoint gives the two WebRequest tools 290 seconds instead of the usual 120, since an apply can wait minutes for the GUI lock or a terminal restart.

### Fixed

- A terminal entry with `chartctl: false` still served every chartctl and WebRequest route when `chartctl.enabled` was on globally, because the API dropped that key when it read its own terminal entry. The loader was correctly left off that terminal, so the routes answered with nothing behind them. The override now turns the routes off too. The API also matches its own terminal entry when `config.yaml` gives the account as an unquoted number; before, it fell back to the first terminal's entry and its settings.
- The unified MCP `endpoints` catalog listed none of the chartctl and WebRequest routes, so an agent on that endpoint could not discover them. They are listed now, each marked `"requires": "chartctl"`.
- `docs/chart-deployments.md` and the skill had broken examples: the upload `curl` calls had no `Authorization` header, so they failed with 401 wherever a token is set, and the `PATCH /deployments/<id>` calls sent no `Content-Type: application/json`, so the API ignored the body and answered 400. The route was also listed as `/experts/<hash>`; it is `/experts/<name>`. The skill said Chart Deployments set themselves up, when they are off until `chartctl.enabled: true`, and linked the OpenClaw plugin on a branch that does not exist.
- The docs said `PATCH /deployments/<id>` changes the set file in place. It updates the deployment, but the loader leaves a chart it already runs alone, so the new inputs apply only the next time it opens the chart. The docs and tool descriptions now say so and give the way to apply them right away: pause, wait until `GET /charts` no longer lists the chart, then resume.
- The v4.18.0 notes said the WebRequest allowlist is re-applied after every terminal restart. It is re-applied once when the API process starts, which covers the periodic VM reboot but not a terminal restart inside a running API process, such as the health monitor's recovery restart. The docs now say so and point at `POST /webrequest/apply` for that case.

## [v4.18.0]: 2026-10-09

Remote EA deployment: attach Expert Advisors to charts with set files over the HTTP API, with no RDP and no terminal restart. Chart Deployments and the WebRequest allowlist automation were contributed by @Marinski in #10.

### Added

- **Chart Deployments** (`mt5api/chartctl/`, `mt5api/handlers/chartctl.py`). Stage `.ex5` and `.set` artifacts and declare deployments (expert, set, symbol, timeframe) as desired state. A resident loader EA reconciles the terminal's charts to it. New endpoints: `POST/GET/DELETE /experts`, `POST/GET /sets` and `GET /sets/<name>`, `POST/GET /deployments` and `GET/PATCH/DELETE /deployments/<id>`, `POST /deployments/reconcile`, `GET /charts`, `GET /loader`, `POST /charts/<chart_id>/screenshot`, `POST /charts/<chart_id>/close`.
- **Chart Control Protocol v1**, a file-based contract in `MQL5\Files\chartctl\` (`desired.json`, `observed.json` and a command channel), documented in `docs/chart-control-protocol.md`. A deployment reports `running` only after the loader confirms the expert is live on a chart. Drift and failures show up in `observed.json`.
- **Reference loader EA** `assets/experts/MT5ChartLoader.mq5` and the portable include `assets/experts/include/ChartControl.mqh`. An existing resident EA, such as an account tracker, can adopt the protocol with three calls instead of running a second EA. A terminal GlobalVariable works as a single-loader mutex, so two loaders on one terminal do not fight.
- The loader adopts its own charts after a terminal restart. MT5's profile save and restore drops the `chartctl:<id>` chart comment, so before opening a chart, reconcile first adopts an unowned chart that already runs the deployment's exact expert, symbol and timeframe. Without this, every restart, including the periodic auto-reboot, opened a duplicate chart. A failed attach closes the chart it opened and backs off for 60 s, and errors are tracked per deployment.
- The `close_chart` loader command closes any chart by id, including charts the loader cannot attribute to a deployment. The loader refuses to close its own chart.
- Config block `chartctl:` in `config.yaml` (enable flag, reconcile hint, staleness window, command timeout, upload cap). Live-mode terminals only, with a per-terminal `chartctl: false` override.
- **Loader bootstrap on boot.** `scripts/compile-chartctl-loader.bat` compiles the loader in every broker base and copies the `.ex5` into existing terminal instances. `config_helper.py write_ini` adds a `[StartUp] Expert=Advisors\MT5ChartLoader` section (honoring `symbol_suffix`) to live chartctl-enabled terminals, so the loader attaches itself when the terminal launches. Deploying needs no RDP and no manual attach. A duplicate loader from a re-fired `[StartUp]` line closes itself through the mutex.
- **WebRequest allowlist provisioning** (`GET`/`PUT /webrequest`, `POST /webrequest/apply`; `mt5api/chartctl/webrequest.py`, `mt5api/chartctl/autoit_webrequest.py`, `mt5api/handlers/webrequest.py`). Set the terminal's `WebRequest()` allowed-URL list over the API instead of the Options dialog. It is its own call, not a deployment field, because it is rarely needed. The API picks one of two apply paths at runtime:
  - **Windows VM (the default here):** on this terminal build the allowlist lives in the machine-bound `MQL5\experts.dat`, not in `common.ini`, and MT5 drops it on every restart. So the API sets it the way a user would. A bundled portable AutoIt interpreter (`assets/autoit/AutoIt3_x64.exe` with `set_webrequest.au3`) opens Tools > Options > Expert Advisors and types the URLs in. The list takes effect in the running session. Because MT5 forgets it on restart, `main.py` re-applies the stored list about 25 s after each terminal start or restart, including the periodic auto-reboot.
  - **Bare metal:** where `common.ini` does hold the list, the API encodes it into `common.ini` (`scripts/webrequest_allowlist_codec.py`, format reverse-engineered from `terminal64.exe` and checked byte-identical against 18 real broker blobs) and restarts the terminal.

  The desired list is stored per terminal in `Config/webrequest.json`. The first use imports the terminal's existing list, so manually configured URLs survive. The AutoIt scripts find the right terminal by the `terminal64.exe` PID that the API resolves from the executable path, because cloned terminals of one account have identical window titles. A named Windows kernel mutex (`Global\mt5_httpapi_webrequest_autoit`, crash-safe through `WAIT_ABANDONED`) serializes GUI applies across the host, since all terminals share desktop focus, and each terminal's boot re-apply is staggered by its port. `inspect_options.au3` and `selftest.au3` ship as GUI-automation diagnostics (`POST /webrequest/apply?script=`). Live-mode chartctl terminals only.
- Tests: `tests/test_chartctl_units.py`, `tests/test_chartctl_endpoints.py`, `tests/test_webrequest.py`, and `tests/chartctl_fake_loader.py`, a Python stand-in for the EA side of the protocol, so the endpoint suite runs on Linux without MT5.

### Notes

- All chartctl handlers are lock-free. They only do file I/O against the terminal data dir, so they never wait behind the process-wide MT5 SDK lock.
- Additive and opt-in: `chartctl.enabled` defaults to `false`. Without the block the API behaves exactly as before and writes no `[StartUp]` section. Turning it on attaches the loader EA to every live terminal, which is a fleet-wide change, so it never turns on through an upgrade alone.

## [v4.17.1]: 2026-10-08

### Fixed

- **The health monitor could no longer see a terminal once its queue was full** (issue #25, reported by @Marinski). Its check went through the same `MT5_MAX_QUEUE_DEPTH` gate as client requests, and a full queue or an MT5 lock held past 60s made it log a warning and skip the round without counting it. So the backpressure the monitor exists to recover from also stopped it from acting. The monitor now skips the queue-depth cap and still waits for the lock. A lock it can't get within 60s counts as a dead check. After five in a row it kills the terminal without an SDK call, which releases whatever was stuck holding the lock, and the next check restarts the terminal. Client requests still get `503` for a full queue or a held lock, as before.

## [v4.17.0]: 2026-10-07

The stuck-call guard, the `/ping` counters and the account-field fix were contributed by @Marinski in #26.

### Fixed

- **One stuck SDK call no longer drags every later request down with it.** An `mt5.*` call that runs past its 30s timeout leaves its thread inside the SDK, because Python can't kill it. Each later request then made a second call on the same stuck connection and waited out its own 30s, so a terminal's queue could fill and `/ping` could take an hour. While a stuck call's thread is alive, later SDK calls on that terminal now fail at once with `503 mt5 call is wedged` and `Retry-After: 30`. When the live health monitor restarts the terminal, it kills it without calling `mt5.shutdown`, reconnects past the stuck call, and once the reconnect succeeds it stops tracking that call, so requests work again.
- An account in `config.yaml` with keys other than `login`, `password` and `server`, such as `symbol_map`, no longer breaks terminal init and every SDK route with it. The API now passes only those three keys to `mt5.initialize`.

### Changed

- `/ping` also reports `sdk_threads_alive` and `sdk_oldest_alive_s`.
- A timed-out or refused SDK call logs one error line with `TIMEOUT` or `WEDGED (<stuck call> stuck <N>s)`, the process mode, the request path and the number of SDK threads still alive.

## [v4.16.0]: 2026-10-06

Rotating every VM's log directory and gzipping the archives was contributed by @Marinski in #27.

### Fixed

- **Every VM's log directory is rotated.** The generated compose mounted only `/data/mt5-shared/logs` into `log-rotator`, so a VM with its own `log_dir` in `vms.yaml` never had its logs rotated or pruned. The rotator now takes `LOG_DIRS`, a colon-separated list, and the generated compose mounts and lists one directory per distinct `log_dir`. A VM that sets no `log_dir` writes to `/data/mt5-shared/logs`, so that directory is rotated whenever any VM omits `log_dir`. A listed directory that is not mounted is logged as `skipping <dir>: not mounted` and the others are still rotated. `LOG_DIR`, the single-directory form `docker-compose.yml.example` passes, still works.
- **Rotated archives no longer fill the disk with zeros.** Truncating a live log on the host does not reset the Windows guest's write offset, so the file comes back sparse: a large hole with the real text at the end. On one install a live `full.log` reported 2.3 GB with 90 MB on disk. The old rotator copied it with `cp`, which wrote the whole hole out, so every daily archive was the full apparent size in real blocks. Archives are now gzipped, which shrinks the hole to almost nothing and compresses the text as well.

### Changed

- **Rotated logs are named `*.log.YYYYMMDD.gz`** instead of `*.log.YYYYMMDD`. On its first pass after the upgrade the rotator gzips the plain archives an older version left behind, and retention prunes both names. Expired archives are deleted before that pass, so they are never compressed only to be removed. Nothing to migrate; anything that reads the archives directly needs to read gzip.


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
