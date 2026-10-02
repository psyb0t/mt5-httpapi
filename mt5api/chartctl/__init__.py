"""Chart Deployments (chartctl) — EA deployment primitives.

Generic, orchestrator-agnostic capability: stage .ex5/.set artifacts,
declare desired deployments (expert + set + symbol + timeframe), and let a
resident loader EA reconcile the terminal's charts to that desired state
over a file protocol inside the MQL5 sandbox.

Protocol contract: docs/chart-control-protocol.md
Reference loader:  assets/experts/MT5ChartLoader.mq5 (+ ChartControl.mqh)

Nothing in this package touches the MT5 SDK — every operation is plain
file I/O against TERMINAL_DIR, so no handler here ever queues behind the
process-wide MT5 lock.
"""
