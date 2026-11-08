# mt5-httpapi

[![CI](https://github.com/psyb0t/mt5-httpapi/actions/workflows/pipeline.yml/badge.svg?branch=master)](https://github.com/psyb0t/mt5-httpapi/actions/workflows/pipeline.yml)
[![version](https://raw.githubusercontent.com/psyb0t/mt5-httpapi/badges/version.svg)](https://github.com/psyb0t/mt5-httpapi/releases)
[![license](https://raw.githubusercontent.com/psyb0t/mt5-httpapi/badges/license.svg)](LICENSE)

MetaTrader 5 running inside a real Windows VM (Docker + QEMU/KVM) with REST and MCP APIs slapped on top. No Wine bullshit, no janky compatibility hacks — the real Windows MT5 terminal in portable mode, controllable without clicking through that fucking GUI all day.

Run multiple brokers, accounts, and cloned terminal instances at the same time. Each one gets its own Python API process inside the VM, while nginx puts the whole mess behind one port at `http://localhost:8888/<broker>/<account>/...`.

## Contents

- [What this fucker does](#what-this-fucker-does)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Architecture](#architecture)
- [Where the fuck is everything documented?](#where-the-fuck-is-everything-documented)
- [API at a glance](#api-at-a-glance)
- [Development](#development)
- [Project layout](#project-layout)
- [Recommended brokers and prop firms](#recommended-brokers-and-prop-firms)
- [Sponsor](#sponsor)
- [License](#license)

> [!WARNING]
> This thing can trade real money. If your algo buys the top and detonates your account, that shit is on you. Start with demo accounts and test the fucker properly.

## What this fucker does

- **Real MT5 on real Windows** — no Wine, no pretending, no compatibility-layer roulette.
- **As many brokers, accounts, and terminal clones as your machine can stomach** — all behind one nginx port.
- **The useful MT5 shit over HTTP** — terminal state, market data, orders, positions, history, and Strategy Tester jobs.
- **Built-in technical analysis** — [wickworks](https://github.com/psyb0t/docker-wickworks) calculates indicators and SMC primitives server-side without exposing its own port.
- **MCP for the robot overlords** — one endpoint per terminal or one unified endpoint for the whole pile, plus Claude Code, Codex, and OpenClaw integrations.
- **Optional Telegram signal copier**: follow signal channels or a websocket feed and place their trades on as many accounts as you want, each with its own sizing and open-trade limits. Off until you turn it on. See [Signal copier](https://psyb0t.github.io/mt5-httpapi/signals/).
- **Optional multi-VM fuckery** — spread terminals across VMs or NUMA nodes, then reach them through Tailscale or Cloudflare Tunnel if you need to.

## Requirements

- Linux with KVM available at `/dev/kvm`
- Docker with Docker Compose
- About 20 GB of disk for the ISO, VM, and MT5 installations
- Enough RAM for however hard you plan to abuse the Windows VM; the default low-memory setup leans heavily on host swap

MT5 hoards loaded chart history like a greedy little bastard and does not give the memory back. Deep history scraping can blow each terminal up to several gigabytes, so read the [actual sizing details](https://psyb0t.github.io/mt5-httpapi/installation-and-configuration/#requirements) before hammering it.

## Quick start

```bash
git clone https://github.com/psyb0t/mt5-httpapi.git
cd mt5-httpapi

cp config/config.yaml.example config/config.yaml
# Generate a bearer token, then edit config/config.yaml with it, your broker
# login, server, password, and ONE terminal you actually intend to run.
openssl rand -hex 32

# This filename is not decoration: "mybroker" MUST exactly match the
# broker: field of the terminal you configured below.
cp ~/Downloads/mt5setup.exe mt5installers/mt5setup-mybroker.exe

# Start the stack. This starts the VM; it does not mean MT5 is ready yet.
make up

# Wait until this passes before hitting the API.
make status
```

Before `make up`, replace the template's sample `accounts:` and `terminals:`
blocks — they contain placeholder FTMO and RoboForex entries and **will not
magically start your broker**. Keep the other template settings, but the three
names below must line up exactly, or Windows installs one terminal and the API
tries to launch another fucking thing:

```yaml
# config/config.yaml
accounts:
  mybroker:                 # 1. broker key
    main:                   # 2. account key
      login: 12345678       # your real account login
      server: "Broker-MT5"  # your broker's exact MT5 server name
      password: "your-password"

terminals:
  - broker: mybroker        # 1. same broker key
    account: main           # 2. same account key
    port: 5001              # unique per terminal; stays inside Docker
    utc_offset: "0"         # set this to the broker server's UTC offset
```

The installer must therefore be named `mt5installers/mt5setup-mybroker.exe`
(`mybroker` is 3. the same broker key). Get that executable from the broker;
this repo cannot download it for you. Remove the unused example accounts and
terminals, or configure an installer for every one of them.

The first run downloads tiny11 and installs Windows, Python, and MT5, so give
the fucker roughly ten minutes. `make up` returning only means Docker and the
VM booted; `make status` passing means the configured terminal API is actually
alive. Watch the installation through noVNC at <http://localhost:8006>; the
API lands at `http://localhost:8888/mybroker/main/` for the config above.

## Architecture

```text
client
  ├─ REST: /<broker>/<account>[/<instance>]/...
  └─ MCP:  /mcp or /<broker>/<account>/mcp
          │
      nginx :8888
          │
  Windows VM(s) ── one MT5 terminal + Python API process per configured terminal
          │
  wickworks sidecar for requested technical-analysis calculations
```

`config/config.yaml` controls the accounts, terminals, ports, auth, broker UTC offsets, and whether a terminal is for live trading or backtests. Add a real `vms.yaml` when one Windows VM is not enough; leave it out and the normal single-VM setup keeps working.

## Where the fuck is everything documented?

The old README became a massive wall of API shit, so the details now live on the [documentation site](https://psyb0t.github.io/mt5-httpapi/), with search, where you can actually find them:

| You need | Read this |
| --- | --- |
| Get Windows + MT5 running, size the host, and configure accounts | [Installation and configuration](https://psyb0t.github.io/mt5-httpapi/installation-and-configuration/) |
| Figure out routing, auth, health, terminal state, accounts, and broker time | [REST API overview](https://psyb0t.github.io/mt5-httpapi/rest-api/) |
| Pull symbols, ticks, bars, and server-side TA | [Market data API](https://psyb0t.github.io/mt5-httpapi/market-data/) |
| Place/close shit and inspect orders, positions, and history | [Trading and history API](https://psyb0t.github.io/mt5-httpapi/trading-and-history/) |
| Run Strategy Tester jobs and get the artifacts back | [Backtesting](https://psyb0t.github.io/mt5-httpapi/backtesting/) |
| Throw giant parameter sweeps at the Strategy Tester | [Backtest optimization](https://psyb0t.github.io/mt5-httpapi/backtest-optimization/) |
| Turn MQL5 source into an `.ex5` without touching MetaEditor | [Compiling MQL5](https://psyb0t.github.io/mt5-httpapi/compiling/) |
| Wire the MCP endpoints into your agent of choice | [MCP and agent integrations](https://psyb0t.github.io/mt5-httpapi/mcp-and-agents/) |
| Copy working curl and Go examples instead of guessing | [Clients and examples](https://psyb0t.github.io/mt5-httpapi/clients-and-examples/) |
| Operate the bastard: Make targets, ports, remote access, concurrency, and logs | [Operations](https://psyb0t.github.io/mt5-httpapi/operations/) |
| Split terminals across several Windows VMs or NUMA nodes | [Multi-VM setup](https://psyb0t.github.io/mt5-httpapi/multi-vm-setup/) |
| Deploy EAs onto charts over HTTP instead of clicking through the Navigator | [Chart Deployments](https://psyb0t.github.io/mt5-httpapi/chart-deployments/) |
| Push libraries, DLLs and data into a terminal, zipped or not, and read its files and logs back | [File API](https://psyb0t.github.io/mt5-httpapi/files/) |
| Copy trade signals from Telegram channels or a websocket feed onto your accounts, sized per account | [Signal copier](https://psyb0t.github.io/mt5-httpapi/signals/) |

## API at a glance

Every configured terminal gets one of these routes:

```text
http://localhost:8888/<broker>/<account>/...
http://localhost:8888/<broker>/<account>/<instance>/...
```

Set `api_token` in `config/config.yaml`, shove it into a bearer header, and start poking the thing:

```bash
export MT5_API_TOKEN="your-token-here"
export MT5_API_URL="http://localhost:8888/mybroker/main"

curl -H "Authorization: Bearer $MT5_API_TOKEN" "$MT5_API_URL/ping"
curl -H "Authorization: Bearer $MT5_API_TOKEN" "$MT5_API_URL/account"
curl -H "Authorization: Bearer $MT5_API_TOKEN" \
  "$MT5_API_URL/symbols/EURUSD/rates?timeframe=H1&count=100"
```

That is just the hello-world shit. The [REST API docs](https://psyb0t.github.io/mt5-httpapi/rest-api/) link to every endpoint and response shape.

JSON keys are snake_case everywhere, the same as the MT5 fields the API passes through (`time_msc`, `volume_real`). The backtest and TA routes used to answer in camelCase (`jobId`, `startedAt`, `wickworksStatus`), so for now they still send those keys next to the snake_case ones and still accept camelCase in request bodies. **The camelCase keys are deprecated and go away in v5.0.0.** Move your clients to the snake_case names.

## Development

```bash
make status            # see whether the whole contraption is alive
make test              # run all the tests, including integration + Go race
make test-live         # test a running stack, set by MT5_LIVE_* (see docs/operations.md)
make lint              # lint the PowerShell and shell shit
make format            # format the shell scripts
make verify-binaries   # check every vendored executable against the manifest
make logs              # watch the logs scream
make down              # stop only this project's stack
```

Run `make help` when you forget the rest. The [operations guide](https://psyb0t.github.io/mt5-httpapi/operations/#make-targets) explains what the targets actually do.

### Vendored binaries

Every executable tracked in this repo is declared in
[`assets/binaries.lock.json`](assets/binaries.lock.json) with its SHA-256, size,
upstream URL and the state of its code signature. `make verify-binaries` — which
`make test` runs first, so CI enforces it on every pull request — fails the
build on an undeclared binary, on one whose bytes changed, or on a signature
that degrades.

Nobody can read a binary. A reviewer skims thousands of lines of source and
waves through the `.exe` beside it because there is nothing to skim, and that is
the shape every serious supply-chain compromise has taken. If you need to add
one: verify the download yourself against the vendor's published hash, then
record it in the manifest with where it came from. If it cannot be verified, say
so in its `note` — the gate will keep warning about it on every run, which is
the point.

## Project layout

```text
config/          config and templates for the whole contraption
mt5api/          Python REST + MCP server running beside each terminal
mcpunifier/      one MCP endpoint that fronts every configured terminal
signals/         optional signal copier: Telegram and websocket signals in, orders out
clients/go/      typed Go client so you do not have to hand-roll requests
scripts/         Windows bootstrap and host-side glue
assets/          loader EAs, vendored binaries and their lock file, mounted read-only into the VM
mt5installers/   your broker installers, named mt5setup-<broker>.exe (gitignored)
examples/        Python and shell examples for the API and Chart Deployments
tests/           unit, contract, integration, and race-test shit
docs/            all the details that no longer clog up this README
```

## Recommended brokers and prop firms

- [RoboForex](https://my.roboforex.com/en/?a=zswg)
- [TeleTrade](https://my.teletrade-dj.com/agent_pp.html?agent_pp=26834897)
- [FTMO](https://join.ftmo.com/uYaZPRmKowRZIKtuXdkE): prop firm with MT4 and MT5 support. Trading accounts use simulated capital.

## Sponsor

<a href="https://www.buymeacoffee.com/psyb0t"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-blue.png" alt="Buy Me a Coffee" height="60" width="217"></a>

## License

[WTFPL](LICENSE). See [CHANGELOG.md](CHANGELOG.md) for release notes.
