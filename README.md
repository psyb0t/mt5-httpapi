# mt5-httpapi

[![CI](https://github.com/psyb0t/mt5-httpapi/actions/workflows/pipeline.yml/badge.svg?branch=master)](https://github.com/psyb0t/mt5-httpapi/actions/workflows/pipeline.yml)
[![version](https://raw.githubusercontent.com/psyb0t/mt5-httpapi/badges/version.svg)](https://github.com/psyb0t/mt5-httpapi/releases)
[![license](https://raw.githubusercontent.com/psyb0t/mt5-httpapi/badges/license.svg)](LICENSE)

Run the real MetaTrader 5 terminal inside a Windows VM on Docker + QEMU/KVM and control multiple brokers and accounts through one REST or MCP endpoint.

> [!WARNING]
> This automates real trades. Start with demo accounts, test your strategy, and treat every endpoint that changes an order or position as production access to your money.

## What it ships

- A real Windows MT5 environment in portable mode — no Wine.
- Multiple brokers, accounts, and terminal instances behind one nginx port.
- REST endpoints for terminal state, market data, orders, positions, history, and Strategy Tester jobs.
- Server-side technical analysis through the isolated [wickworks](https://github.com/psyb0t/docker-wickworks) sidecar.
- Per-terminal and unified MCP endpoints, plus Claude Code, Codex, and OpenClaw integrations.
- Optional multi-VM placement, Tailscale access, and Cloudflare Tunnel access.

## Requirements

- Linux with KVM available at `/dev/kvm`
- Docker with Docker Compose
- About 20 GB of disk for the ISO, VM, and MT5 installations
- Enough RAM for the Windows workload; the default low-memory setup relies heavily on host swap

Heavy history scraping can push each terminal into multiple gigabytes of cached chart data. Read the [resource guidance](docs/installation-and-configuration.md#requirements) before sizing a busy deployment.

## Quick start

```bash
git clone https://github.com/psyb0t/mt5-httpapi.git
cd mt5-httpapi

cp config/config.yaml.example config/config.yaml
# Set api_token, accounts, and terminals in config/config.yaml.

cp ~/Downloads/mt5setup.exe mt5installers/mt5setup-mybroker.exe
make up
```

The first run downloads tiny11, installs Windows, Python, and MT5, and takes roughly ten minutes. Later boots normally take about a minute. Open noVNC at <http://localhost:8006> to watch setup, then use the API at <http://localhost:8888>.

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

`config/config.yaml` is the source of truth for accounts, terminals, ports, authentication, broker UTC offsets, and process mode. A real `vms.yaml` opts into multi-VM generation; without it, the project uses the single-VM compose example.

## Documentation

| Area | Guide |
| --- | --- |
| Host sizing, installation, accounts, terminals, and configuration | [Installation and configuration](docs/installation-and-configuration.md) |
| Routing, authentication, health, terminal, account, and broker time | [REST API overview](docs/rest-api.md) |
| Symbols, ticks, rates, and server-side technical analysis | [Market data API](docs/market-data.md) |
| Positions, orders, broker results, and history | [Trading and history API](docs/trading-and-history.md) |
| Strategy Tester jobs and artifacts | [Backtesting](docs/backtesting.md) |
| Large parameter sweeps and result handling | [Backtest optimization](docs/backtest-optimization.md) |
| Unified/per-terminal MCP and agent installation | [MCP and agent integrations](docs/mcp-and-agents.md) |
| curl examples, Go client, and technical analysis | [Clients and examples](docs/clients-and-examples.md) |
| Make targets, ports, remote access, concurrency, and logs | [Operations](docs/operations.md) |
| NUMA placement and multiple Windows VMs | [Multi-VM setup](docs/multi-vm-setup.md) |

## API at a glance

Each configured terminal is available at:

```text
http://localhost:8888/<broker>/<account>/...
http://localhost:8888/<broker>/<account>/<instance>/...
```

Set `api_token` in `config/config.yaml`, then send it as a bearer token:

```bash
export MT5_API_TOKEN="your-token-here"
export MT5_API_URL="http://localhost:8888/mybroker/main"

curl -H "Authorization: Bearer $MT5_API_TOKEN" "$MT5_API_URL/ping"
curl -H "Authorization: Bearer $MT5_API_TOKEN" "$MT5_API_URL/account"
curl -H "Authorization: Bearer $MT5_API_TOKEN" \
  "$MT5_API_URL/symbols/EURUSD/rates?timeframe=H1&count=100"
```

See the [REST API reference](docs/rest-api.md) for the complete request and response model.

## Development

```bash
make status            # inspect a running deployment
make test              # unit, integration, and Go race tests
make lint              # validate PowerShell and shell scripts
make format            # apply shell formatting
make logs              # follow service logs
make down              # stop this project's stack
```

Run `make help` for every supported target. The [operations guide](docs/operations.md#make-targets) explains the test split and live status probe.

## Project layout

```text
config/          deployment configuration and templates
mt5api/          per-terminal Python REST and MCP server
mcpunifier/      unified MCP server for every configured terminal
clients/go/      typed public Go client
scripts/         Windows bootstrap plus host-side helpers
tests/           unit, contract, and container-backed integration tests
docs/            feature guides and operational runbooks
```

## Recommended brokers

- [RoboForex](https://my.roboforex.com/en/?a=zswg)
- [TeleTrade](https://my.teletrade-dj.com/agent_pp.html?agent_pp=26834897)

## License

[WTFPL](LICENSE). See [CHANGELOG.md](CHANGELOG.md) for release notes.
