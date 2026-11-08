---
title: "Home"
description: "mt5-httpapi runs real Windows MetaTrader 5 terminals in a Docker VM and puts a REST API and MCP servers in front of them."
nav_order: 1
permalink: /
seo:
  type: WebSite
---

# mt5-httpapi

mt5-httpapi runs the real Windows MetaTrader 5 terminal, in portable mode, inside a Windows VM in Docker (QEMU/KVM through `dockurr/windows`), and puts an HTTP REST API and MCP servers in front of it. No Wine. Each configured terminal gets its own Python API process inside the VM, and nginx puts all of them behind one port at `http://localhost:8888/<broker>/<account>/...`.

```text
client
  ├─ REST: /<broker>/<account>[/<instance>]/...
  └─ MCP:  /mcp/ or /<broker>/<account>[/<instance>]/mcp
          │
      nginx :8888
          │
  Windows VM(s) ── one MT5 terminal + Python API process per configured terminal
          │
  wickworks sidecar for requested technical-analysis calculations
```

It runs as many brokers, accounts and cloned terminal instances as the host can hold. Over HTTP you get terminal state, symbols, ticks, bars with server-side indicators, orders, positions, history, Strategy Tester jobs, MQL5 compiling, file access and EA deployment onto charts. The same surface is available as MCP tools, per terminal or through one unified endpoint.

{: .warning }
This can trade real money. Start with a demo account.

## Install

You need Linux with KVM at `/dev/kvm`, Docker with Compose, and about 20 GB of disk. The short version:

```bash
git clone https://github.com/psyb0t/mt5-httpapi.git
cd mt5-httpapi
cp config/config.yaml.example config/config.yaml   # set the token, accounts and terminals
cp ~/Downloads/mt5setup.exe mt5installers/mt5setup-mybroker.exe
make up
make status
```

The first run installs Windows, Python and MT5, which takes about ten minutes. [Installation and configuration](installation-and-configuration.md) covers sizing, every `config.yaml` setting and how the installer name must match the broker key.

## Where to go next

| You want to | Read |
| --- | --- |
| Size the host and configure accounts, terminals and sidecars | [Installation and configuration](installation-and-configuration.md) |
| Run Make targets, open ports, reach the API remotely, read logs | [Operations](operations.md) |
| Split terminals across several VMs or NUMA nodes | [Multi-VM setup](multi-vm-setup.md) |
| Learn the routing, auth, health and terminal endpoints | [REST API](rest-api.md) |
| Pull symbols, ticks, bars and server-side TA | [Market data](market-data.md) |
| Place and close trades, read orders, positions and history | [Trading and history](trading-and-history.md) |
| Read and write files in a terminal's MQL5 tree | [File API](files.md) |
| Compile MQL5 source into an `.ex5` | [Compiling MQL5](compiling.md) |
| Run Strategy Tester jobs and fetch the reports | [Backtesting](backtesting.md) |
| Run optimizations over parameter ranges | [Backtest optimization](backtest-optimization.md) |
| Attach Expert Advisors to live charts over HTTP | [Chart Deployments](chart-deployments.md) |
| Copy trade signals from Telegram or a websocket feed | [Signal copier](signals.md) |
| Connect Claude Code, Codex or OpenClaw over MCP | [MCP and agents](mcp-and-agents.md) |
| Copy working curl and Go client examples | [Clients and examples](clients-and-examples.md) |
