# mt5-httpapi setup: boot the whole fucking contraption

This is an operator reference for deploying and managing the stack. The skill itself only talks to a server the user already runs, so use these steps only when the user asks to set up or manage the stack. They run on the user's host: `git`, `make`, `docker compose`, `openssl`, and for tunnels `cloudflared`. `make clean` and `make distclean` delete VM data (see [Management](#management)).

## Requirements

- Linux host with KVM enabled (`/dev/kvm`)
- Docker + Docker Compose
- `python3` with `pip`, `aria2c` (installed for you only on Debian or Ubuntu) and `openssl`
- ~20 GB disk (Windows ISO + VM storage + MT5 installs); `DISK_SIZE` lets the VM disk grow to 32 GB
- 5 GB RAM (runs mostly on swap; tiny11 + debloat idles at ~1.4 GB)

## Quick install

```bash
git clone https://github.com/psyb0t/mt5-httpapi
cd mt5-httpapi
cp config/config.yaml.example config/config.yaml
# Edit config.yaml with your broker credentials, api_token, terminals
```

Dump your broker's installer in `mt5installers/` as `mt5setup-<broker>.exe`, then fire it up:

```bash
make up
```

The first run downloads tiny11, installs and debloats Windows, reboots, installs Python and every MT5 terminal, reboots again, and finally starts the stack. Allow 10 to 15 minutes. Later boots are usually around a minute.

## Configuration

### `config/config.yaml`

One file controls the whole mess: bearer token, broker credentials, terminals, and optional Tailscale and wickworks sidecars. Copy the example and fill in your shit. It holds broker credentials and the API token, so keep it out of git and do not print it.

```yaml
# Bearer token for API auth. Empty string = no auth.
api_token: "your-token-here"   # or: $(openssl rand -hex 32)

# Broker credentials: accounts.<broker>.<account_name>
accounts:
  roboforex:
    main:
      login: 12345678
      server: "RoboForex-Pro"
      password: "your_password"

# Terminal instances: one API process per entry.
# port is container-internal (only nginx and the mt5 container talk to it).
# utc_offset normalizes broker wall-clock timestamps to real UTC on the wire.
terminals:
  - broker: roboforex
    account: main
    port: 6542
    utc_offset: "3h"

# Optional: wickworks TA sidecar (used by POST /symbols/<symbol>/rates/ta)
# Default URL is the dockurr gateway IP; leave as-is unless you change
# the docker-compose networking.
wickworks:
  url: "http://20.20.20.1:8000/"
  timeout: "30s"
```

Other top-level settings:

- `reboot`: scheduled VM reboots. `enabled: false` turns them off;
  `interval` is the minutes between them (default `30`, `0` also off);
  `at: "HH:MM"` with an optional IANA `timezone` (default UTC) reboots once a
  day at that wall-clock time instead; `max_postpone` (minutes, default `360`)
  caps how long a reboot waits for a running backtest or write. The old
  top-level `reboot_interval` and `reboot_max_postpone` still work without the
  block but are deprecated (removed in v5).
- `backtest_timeout`: default Strategy Tester deadline (`"6h"`, `"30m"`,
  `"3h30m"`, or a bare hour count); the `POST /backtest` `timeout` field can
  override it per job.
- `requirements`: extra Python packages installed inside the VM on boot.
- `tailscale.auth_key` / `tailscale.login_server`: optional tailnet exposure.
  The sidecar runs only when its block in `docker-compose.yml` is uncommented;
  an empty auth key skips the Serve setup but does not stop an uncommented
  sidecar from starting unauthenticated.

`broker` matches both the `mt5setup-<broker>.exe` installer name and the key in `accounts`. Each terminal installs to `<broker>/base/` and gets copied to `<broker>/<account>/` at startup so multiple accounts of the same broker don't conflict.

Optional per-terminal fields:

- `instance`: clone name for running multiple terminals of the same `broker`/`account`. Route to a specific clone via `/<broker>/<account>/<instance>/...`. Missing/empty = `default`, which also keeps the legacy `/<broker>/<account>/...` alias.
- `symbol_suffix`: Strategy Tester symbol remap. If set, mt5-httpapi appends it when `[Tester].Symbol` doesn't already end with it (e.g. `EURUSD` -> `EURUSDp`). Examples: `"p"`, `".p"`, `"-mini"`. Use `""` for no suffix.
- `mode`: `live` (default) keeps MT5 running for SDK calls; `backtest` leaves
  the portable data directory free for Strategy Tester jobs. A live terminal
  cannot run a backtest against the same data directory.
- `vm`: VM name from `vms.yaml`. Without `vms.yaml` every terminal runs on
  the single `mt5` VM; once `vms.yaml` exists, give every terminal a `vm`, or
  it is never launched.

### Multi-VM topology

No `vms.yaml` means the committed single-VM compose example is used. To opt in,
copy `vms.yaml.example` to `vms.yaml`, edit the services/resources/storage
paths, then regenerate `docker-compose.yml`:

```bash
cp vms.yaml.example vms.yaml
python3 scripts/config_helper.py generate_compose
```

See [`docs/multi-vm-setup.md`](https://github.com/psyb0t/mt5-httpapi/blob/master/docs/multi-vm-setup.md) for field
definitions, NUMA pinning, storage mounts, and per-VM terminal assignment.

> **`api_token` must be set to a strong random value before this server is reachable by anything other than localhost.** An empty `api_token` disables auth entirely: any process that can reach the listening socket can read account state, place orders, modify positions, and close trades. Generate one with `openssl rand -hex 32` (or equivalent) and keep it out of git. Do not skip this when planning to bind to a non-loopback interface, expose via a tunnel (see below), or run on a shared host.

## Ports

| Port | Service |
| ---- | ------- |
| 8006 | noVNC (VM desktop), override with `NOVNC_PORT`; published on every host interface with no password, so it shows the logged-in terminals to anyone on the network |
| 8888 | HTTP API entry (nginx, all terminals), override with `API_HOST_PORT`, bound to `127.0.0.1` |

`MCP_LOG_LEVEL` controls the unified MCP service's log level. `NOVNC_PORT` and
`API_HOST_PORT` are set on `make up` (`API_HOST_PORT=9999 make up`), and `run.sh`
keeps them in `.env`, also across a later plain `make up`, so compose publishes the same ports; per-terminal
ports are not published on the host.

Per-terminal ports from `config.yaml`'s `terminals:` list stay container-internal. nginx routes `/<broker>/<account>/...` to the right terminal via docker DNS, and the mt5 container's iptables DNAT forwards into the Windows VM. URL scheme: `http://localhost:8888/<broker>/<account>/...`. noVNC is mainly useful for watching the install progress. Anyone on the network can reach it. In a single-VM `docker-compose.yml`, change its mapping to `"127.0.0.1:${NOVNC_PORT:-8006}:8006"` or firewall the port; see the Ports section of [`docs/operations.md`](https://github.com/psyb0t/mt5-httpapi/blob/master/docs/operations.md#ports).

### Backtest assets (optional)

The `/backtest` endpoint can pull experts and parameter files from a
host-managed pool instead of every request having to upload them:

```
assets/
  experts/   # *.ex5 files
  sets/      # *.set files
```

`run.sh` creates these directories on first start and `docker-compose.yml`
mounts the tree into the VM as `/shared/assets:ro`. Reference them by
filename (basename only; path traversal is rejected) using the `expert_name`
and `set_name` multipart fields. Inline uploads via `expert` / `set` always
take precedence.

## Management

```bash
make up          # start
make down        # stop
make logs        # tail logs
make status      # check status
make clean       # delete the single-VM disk and state (keeps the ISO; multi-VM storage paths stay)
make distclean   # make clean, plus the Windows ISO
```

`make clean` runs `sudo rm -rf data/storage data/shared data/metatrader5 data/oem run.log`, which removes the Windows disk, the terminals' installs and the copy of `config.yaml` in `data/shared`, and `make distclean` also removes `data/win.iso`. Run them only when the user asked for that exact cleanup on this checkout, and only after `make down`; the next `make up` reinstalls everything.

## Logs

Inside the VM shared directory (`data/shared/logs/`):

- `install.log`: MT5 installation progress
- `start.log`: boot-time setup output
- `pip.log`: Python package install
- `api-<broker>-<account>.log`: per-terminal API logs
- `full.log`: combined log of everything

## Public access via Cloudflare Tunnel: lock this shit down

A tunnel makes the API reachable from the internet, so everything in it is an operator decision. Do it only when the user asks. The full step-by-step guide is in the [Cloudflare Tunnel section of `docs/operations.md`](https://github.com/psyb0t/mt5-httpapi/blob/master/docs/operations.md#cloudflare-tunnel-optional). It installs `cloudflared` on the host with `sudo`, creates a tunnel and a DNS record in the user's Cloudflare account, copies the tunnel credentials file into `.data/cloudflared/` (a secret that stays out of git) and enables the `cloudflared` service in `docker-compose.yml`.

Prerequisites before exposing the API:

1. Set a strong `api_token` in `config/config.yaml` (`openssl rand -hex 32` or equivalent). An empty `api_token` plus a public tunnel lets anyone on the internet read account state, place trades and close positions on the real broker account.
2. Restrict access at Cloudflare as well: use Cloudflare Access (Zero Trust) to require email, SSO or service-token auth on the public hostname, so the tunnel is not a wide-open path even if the token leaks.
3. Use a demo broker account first, until the auth chain has been verified end to end from a separate network.
4. Rotate the token after testing and treat it as a high-value secret: losing it is equivalent to losing the brokerage account password.

Clients then use `https://<your-hostname>/<broker>/<account>/...` as `MT5_API_URL`. Cloudflare terminates TLS at the edge, and cloudflared reaches the always-on nginx sidecar (`http://nginx:80`) over the Docker bridge on the host.
