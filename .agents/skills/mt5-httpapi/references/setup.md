# mt5-httpapi setup — boot the whole fucking contraption

## Requirements

- Linux host with KVM enabled (`/dev/kvm`)
- Docker + Docker Compose
- ~20 GB disk (Windows ISO + VM storage + MT5 installs)
- 5 GB RAM (runs mostly on swap — tiny11 + debloat idles at ~1.4 GB)

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

The first run downloads tiny11, installs and debloats Windows, reboots, installs Python and every MT5 terminal, reboots again, and finally starts the pile. Give the bastard about ten minutes. Later boots are usually around a minute.

## Configuration

### `config/config.yaml`

One file controls the whole mess: bearer token, broker credentials, terminals, and optional Tailscale/wickworks sidecars. Copy the example and fill in your shit.

```yaml
# Bearer token for API auth. Empty string = no auth.
api_token: "your-token-here"   # or: $(openssl rand -hex 32)

# Broker credentials — accounts.<broker>.<account_name>
accounts:
  roboforex:
    main:
      login: 12345678
      server: "RoboForex-Pro"
      password: "your_password"

# Terminal instances — one API process per entry.
# port is container-internal (only nginx and the mt5 container talk to it).
# utc_offset normalizes broker wall-clock timestamps to real UTC on the wire.
terminals:
  - broker: roboforex
    account: main
    port: 6542
    utc_offset: "3h"

# Optional: wickworks TA sidecar (used by POST /symbols/<symbol>/rates/ta)
# Default URL is the dockurr gateway IP — leave as-is unless you change
# the docker-compose networking.
wickworks:
  url: "http://20.20.20.1:8000/"
  timeout: "30s"
```

Other top-level settings:

- `reboot_interval` — scheduled VM reboot interval in minutes; `0` disables it
  (default `30`).
- `backtest_timeout` — default Strategy Tester deadline (`"6h"`, `"30m"`,
  `"3h30m"`, or a bare hour count); the `POST /backtest` `timeout` field can
  override it per job.
- `requirements` — extra Python packages installed inside the VM on boot.
- `tailscale.auth_key` / `tailscale.login_server` — optional tailnet exposure;
  an empty auth key disables the sidecar.

`broker` matches both the `mt5setup-<broker>.exe` installer name and the key in `accounts`. Each terminal installs to `<broker>/base/` and gets copied to `<broker>/<account>/` at startup so multiple accounts of the same broker don't conflict.

Optional per-terminal fields:

- `instance` — clone name for running multiple terminals of the same `broker`/`account`. Route to a specific clone via `/<broker>/<account>/<instance>/...`. Missing/empty = `default`, which also keeps the legacy `/<broker>/<account>/...` alias.
- `symbol_suffix` — Strategy Tester symbol remap. If set, mt5-httpapi appends it when `[Tester].Symbol` doesn't already end with it (e.g. `EURUSD` -> `EURUSDp`). Examples: `"p"`, `".p"`, `"-mini"`. Use `""` for no suffix.
- `mode` — `live` (default) keeps MT5 running for SDK calls; `backtest` leaves
  the portable data directory free for Strategy Tester jobs. A live terminal
  cannot run a backtest against the same data directory.
- `vm` — VM name from `vms.yaml`; omitted terminals route to the default `mt5`
  container.

### Multi-VM topology

No `vms.yaml` means the committed single-VM compose example is used. To opt in,
copy `vms.yaml.example` to `vms.yaml`, edit the services/resources/storage
paths, then regenerate `docker-compose.yml`:

```bash
cp vms.yaml.example vms.yaml
python3 scripts/config_helper.py generate_compose
```

See [`docs/multi-vm-setup.md`](../../../../docs/multi-vm-setup.md) for field
definitions, NUMA pinning, storage mounts, and per-VM terminal assignment.

> **`api_token` must be set to a strong random value before this server is reachable by anything other than localhost.** An empty `api_token` disables auth entirely — any process that can reach the listening socket can read account state, place orders, modify positions, and close trades. Generate one with `openssl rand -hex 32` (or equivalent) and keep it out of git. Do not skip this when planning to bind to a non-loopback interface, expose via a tunnel (see below), or run on a shared host.

## Ports

| Port | Service |
| ---- | ------- |
| 8006 | noVNC (VM desktop) — override with `NOVNC_PORT` |
| 8888 | HTTP API entry (nginx, all terminals) — override with `API_HOST_PORT`, bound to `127.0.0.1` |

`MCP_LOG_LEVEL` controls the unified MCP service's log level. `NOVNC_PORT` and
`API_HOST_PORT` are compose-time environment overrides; per-terminal ports are
not published on the host.

Per-terminal ports from `config.yaml`'s `terminals:` list stay container-internal. nginx routes `/<broker>/<account>/...` to the right terminal via docker DNS, and the mt5 container's iptables DNAT forwards into the Windows VM. URL scheme: `http://localhost:8888/<broker>/<account>/...`. noVNC is mainly useful for watching the install progress.

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
filename (basename only — path traversal is rejected) using the `expert_name`
and `set_name` multipart fields. Inline uploads via `expert` / `set` always
take precedence.

## Management

```bash
make up          # start
make down        # stop
make logs        # tail logs
make status      # check status
make clean       # nuke VM disk (keeps ISO)
make distclean   # nuke everything including ISO
```

## Logs

Inside the VM shared folder (`data/shared/logs/`):

- `install.log` — MT5 installation progress
- `start.log` — boot-time setup output
- `pip.log` — Python package install
- `api-<broker>-<account>.log` — per-terminal API logs
- `full.log` — combined log of everything

## Public access via Cloudflare Tunnel — lock this shit down

> **Prerequisites — non-negotiable before exposing publicly:**
>
> 1. **Set a strong `api_token`** in `config/config.yaml` (`openssl rand -hex 32` or equivalent). An empty `api_token` plus a public tunnel means anyone on the internet can read your account state, place trades, modify positions, and close positions on your real broker account. This combination is catastrophic.
> 2. **Restrict access at Cloudflare** as well — use Cloudflare Access (Zero Trust) to require email/SSO/service-token auth on the public hostname so the tunnel is not a wide-open path even if the token leaks.
> 3. **Use a demo broker account first** until you have verified the auth chain end-to-end from a separate network.
> 4. **Rotate the token** after testing and treat it as a high-value secret — losing it is equivalent to losing the brokerage account password.

To expose the API publicly without opening firewall ports:

```bash
# Install cloudflared
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o /tmp/cloudflared
sudo install /tmp/cloudflared /usr/local/bin/cloudflared

# Authenticate and create tunnel
cloudflared tunnel login
cloudflared tunnel create mt5-httpapi

# Register one subdomain — nginx routes per-terminal paths internally
cloudflared tunnel route dns mt5-httpapi mt5-api.yourdomain.com

# Put creds in .data/cloudflared/
mkdir -p .data/cloudflared
cp ~/.cloudflared/<tunnel-id>.json .data/cloudflared/creds.json
```

Create `.data/cloudflared/config.yml`:

```yaml
tunnel: <tunnel-id>
credentials-file: /etc/cloudflared/creds.json

ingress:
  - hostname: mt5-api.yourdomain.com
    service: http://nginx:80
  - service: http_status:404
```

cloudflared points at the always-on nginx sidecar (`http://nginx:80`) — a single backend covers every terminal. Hit `https://mt5-api.yourdomain.com/<broker>/<account>/...` from the public side. Then uncomment the `cloudflared` service in `docker-compose.yml` and run `make up`.

Note: Cloudflare's free Universal SSL covers `*.yourdomain.com` but not deeper subdomains like `*.mt5.yourdomain.com`. Use subdomains directly under your root domain.
