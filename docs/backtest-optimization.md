# Backtest optimization without clicking the fucking GUI

MT5 has three optimization modes, several incompatible output formats, and a habit of hiding the useful shit in cache files. This guide shows how mt5-httpapi drives that mess over HTTP and where the actual results end up.

## Contents

- [Requirements](#requirements)
- [Overview](#overview)
- [Optimization modes](#optimization-modes)
- [Set files for optimization](#set-files-for-optimization)
- [Polling and interpreting results](#polling-and-interpreting-results)
- [Raw artifacts and debugging](#raw-artifacts-and-debugging)
- [End-to-end example script](#end-to-end-example-script)

## Requirements

Optimization requests need a terminal configured with `mode: backtest` in `config/config.yaml`. Do not point this shit at a live terminal and hope for the best.

Example:

```yaml
terminals:
  - broker: darwinex
    account: tester
    port: 6542
    utc_offset: "0"
    mode: backtest
    symbol_suffix: ""
```

If you submit against a `live` terminal, MT5 can exit successfully while producing fuck-all because the running terminal already owns the portable directory.

## Overview

No matter which mode you pick, the dance is the same:

1. Build or author a tester INI.
2. Provide an `.ex5` expert.
3. Provide a `.set` file containing MT5 optimization ranges.
4. Submit `POST /backtest`.
5. Poll `GET /backtest/<job_id>` until `completed`, `failed` or `cancelled`.
6. Inspect `optimization_results`, `optimization_cache`, `/report`, and `/log`.

## Optimization Modes

| Mode | MT5 meaning | Scope | Primary parsed source | Report artifact |
| ---- | ----------- | ----- | --------------------- | --------------- |
| `1` | Slow complete algorithm | Single `[Tester].Symbol` | XML report | `<report>.xml` |
| `2` | Fast genetic algorithm | Single `[Tester].Symbol` | XML report | `<report>.xml` |
| `3` | All symbols selected in Market Watch | Market Watch symbol set | Tester cache `.opt` | `<report>.symbols.xml` |

### Mode 1: Slow Complete

Mode `1` brute-forces the full search space for one symbol. Use it when the grid is small enough that exhaustive search will finish before the heat death of the universe.

Typical characteristics:

- deterministic pass coverage
- can become very slow as the cartesian product expands
- best when you want confidence that every range combination was evaluated

Example `build-ini` request:

```bash
curl -sS -X POST "$URL/backtest/build-ini" \
  -H "Authorization: Bearer $TOK" \
  -H "Content-Type: application/json" \
  -d '{
    "symbol":"GBPCAD",
    "timeframe":"M15",
    "expert":"EA Studio GBPCAD M15 1615044595.ex5",
    "last_years":1,
    "modelling":"open-prices",
    "expert_parameters":"ea studio gbpcad m15 1615044595.take-profit-opt-80-92-step4.set",
    "optimization":1,
    "optimization_criterion":0,
    "report_name":"gbpcad-m15-complete-search"
  }'
```

Example submit request:

```bash
curl -sS -X POST "$URL/backtest" \
  -H "Authorization: Bearer $TOK" \
  -F "ini=@tester.ini" \
  -F "expert_name=EA Studio GBPCAD M15 1615044595.ex5" \
  -F "set_name=ea studio gbpcad m15 1615044595.take-profit-opt-80-92-step4.set" \
  -F "top_passes=20"
```

Typical completed payload shape:

```json
{
  "status": "completed",
  "optimization_type": 1,
  "report_name": "gbpcad-m15-complete-search.xml",
  "optimization_cache": null,
  "optimization_results": [
    {
      "Pass": 12,
      "Result": 1450.22,
      "Profit": 450.22,
      "Profit Factor": 1.62,
      "Expected Payoff": 2.41
    }
  ]
}
```

## Mode 2: Fast Genetic

Mode `2` lets MT5's genetic optimizer hunt the promising parts of a single-symbol search space. Use it when mode `1` would take forever and you can live without evaluating every combination.

Typical characteristics:

- much faster than mode `1` for large grids
- does not guarantee evaluation of every combination
- still returns a normal optimization XML report

Example `build-ini` request:

```bash
curl -sS -X POST "$URL/backtest/build-ini" \
  -H "Authorization: Bearer $TOK" \
  -H "Content-Type: application/json" \
  -d '{
    "symbol":"GBPUSD",
    "timeframe":"M15",
    "expert":"MyEA.ex5",
    "last_years":3,
    "modelling":"open-prices",
    "expert_parameters":"myea-optimizer.set",
    "optimization":2,
    "optimization_criterion":5,
    "report_name":"gbpusd-m15-sharpe-search"
  }'
```

Example completed payload shape:

```json
{
  "status": "completed",
  "optimization_type": 2,
  "report_name": "gbpusd-m15-sharpe-search.xml",
  "optimization_cache": null,
  "optimization_results": [
    {
      "Pass": 184,
      "Result": 2.41,
      "Profit": 1263.5,
      "Profit Factor": 1.48,
      "Expected Payoff": 13.02,
      "Recovery Factor": 3.11,
      "Sharpe Ratio": 2.41,
      "FastPeriod": 12,
      "SlowPeriod": 34
    }
  ]
}
```

## Mode 3: Market Watch Symbols

Mode `3` is the weird bastard. It changes both what MT5 searches and where MT5 hides the results.

MT5 runs the optimization across the symbols currently selected in Market Watch instead of only the `[Tester].Symbol` value. That symbol still matters because it participates in cache naming and INI generation, but the pass rows themselves are keyed to the Market Watch symbol set.

Most importantly, MT5 does **not** put the real pass rows in the normal report XML for mode `3`, because apparently that would be too convenient.

What MT5 writes instead:

- `<report>.symbols.xml` in the terminal `Reports` directory
- one or more `Tester/cache/*.opt` files containing the actual optimization rows
- agent logs under `Tester/Agent-*/logs/` that identify which symbol belongs to each pass

mt5-httpapi handles this by:

- falling back from `<report>.xml` to `<report>.symbols.xml`
- discovering the matching `.opt` cache file from the normalized INI
- parsing the cache rows and sorting them by `Result`
- recovering pass-to-symbol mappings from agent logs
- exposing the matched cache artifact through `optimization_cache`

Example `build-ini` request:

```bash
curl -sS -X POST "$URL/backtest/build-ini" \
  -H "Authorization: Bearer $TOK" \
  -H "Content-Type: application/json" \
  -d '{
    "symbol":"GBPCAD",
    "timeframe":"M15",
    "expert":"EA Studio GBPCAD M15 1615044595.ex5",
    "last_years":5,
    "modelling":"open-prices",
    "expert_parameters":"ea studio gbpcad m15 1615044595.take-profit-opt-80-92-step4.set",
    "optimization":3,
    "optimization_criterion":0,
    "report_name":"mode3-gbpcad-m15-last5y-rerun5"
  }'
```

Example submit request:

```bash
curl -sS -X POST "$URL/backtest" \
  -H "Authorization: Bearer $TOK" \
  -F "ini=@tester.ini;filename=tester.ini" \
  -F "expert_name=EA Studio GBPCAD M15 1615044595.ex5" \
  -F "set_name=ea studio gbpcad m15 1615044595.take-profit-opt-80-92-step4.set" \
  -F "top_passes=50"
```

Example completed payload shape from a real replay:

```json
{
  "job_id": "b05643c6d51c4a4cb0cad8a2b6c5573b",
  "status": "completed",
  "report_name": "mode3-gbpcad-m15-last5y-rerun5.symbols.xml",
  "optimization_type": 3,
  "optimization_results": [
    {
      "Pass": 21,
      "Symbol": "GBPJPY",
      "Result": 1657.54,
      "Profit": 657.54,
      "Profit Factor": 1.9,
      "Expected Payoff": 2.57,
      "Recovery Factor": 3.89,
      "Sharpe Ratio": 0.75,
      "Equity DD %": 11.22,
      "Trades": 256,
      "Custom": ""
    }
  ],
  "optimization_cache": {
    "name": "EA Studio GBPCAD M15 1615044595.all_symbols.M15.20210525.20260525.22.788ECDD113BA3097A58EF888EBEFF9CA.opt",
    "path": "C:\\Users\\Docker\\Desktop\\Shared\\terminals\\darwinex\\live\\a\\Tester\\cache\\EA Studio GBPCAD M15 1615044595.all_symbols.M15.20210525.20260525.22.788ECDD113BA3097A58EF888EBEFF9CA.opt",
    "pattern": "EA Studio GBPCAD M15 1615044595.all_symbols.M15.20210525.20260525.*.opt",
    "build": "22",
    "cache_hash": "788ECDD113BA3097A58EF888EBEFF9CA",
    "row_count": 28,
    "size_bytes": 20013,
    "symbol_component": "all_symbols",
    "period": "M15",
    "from_date": "20210525",
    "to_date": "20260525",
    "expert": "EA Studio GBPCAD M15 1615044595",
    "modified_at": "2026-05-25T08:19:59.371833"
  }
}
```

## `.set` Files for Optimization

An optimization without ranges is just an expensive way to run the same shit repeatedly. The `.set` file needs MT5 optimization ranges.

MT5 uses this wire format for optimizable fields:

```ini
Parameter=CurrentValue||Start||Step||Stop||Y
```

Examples:

```ini
Take_Profit=92||80||4||92||Y
Stop_Loss=0||0||1||10||N
```

Interpretation:

- `Y` means MT5 should optimize this parameter.
- `N` means keep it fixed.
- `CurrentValue` is the default/base value.
- `Start`, `Step`, and `Stop` define the search grid.

You can either:

- save the `.set` directly from MT5 Strategy Tester Inputs
- generate it from `POST /backtest/build-set`

Example JSON to generate a `.set`:

```json
{
  "parameters": [
    {
      "name": "Take_Profit",
      "value": 92,
      "start": 80,
      "step": 4,
      "stop": 92,
      "optimize": true
    },
    {
      "name": "Stop_Loss",
      "value": 0,
      "start": 0,
      "step": 1,
      "stop": 10,
      "optimize": false
    }
  ]
}
```

## Polling and Interpreting Results

All modes use the same status endpoint:

```bash
curl -sS -H "Authorization: Bearer $TOK" "$URL/backtest/$JOB"
```

Important fields:

- `status`: `queued`, `running`, `completed`, `failed`, or `cancelled`
- `optimization_type`: submitted MT5 mode
- `optimization_results`: parsed top `N` rows exposed by the API
- `optimization_cache`: metadata for the matched `.opt` file when cache parsing is used
- `report_name`: final report artifact name; mode `3` usually ends in `.symbols.xml`
- `report_url`: fetch raw MT5 report artifact
- `log_url`: fetch terminal log for the job

## Raw Artifacts and Debugging

When MT5 produces confusing garbage, start here:

- For modes `1` and `2`, start with `/report` because the XML spreadsheet is the main source of optimization rows.
- For mode `3`, start with `optimization_cache` and `optimization_results`; `/report` exists, but it is the `.symbols.xml` header export rather than the real pass table.
- If mode `3` symbols look wrong or missing, inspect the agent logs under `Tester/Agent-*/logs/` because pass numbers are recovered from those logs.
- If `optimization_results` is empty for mode `1` or `2`, inspect the raw XML report and terminal log first.

## End-to-End Example Script

This script works for all three modes. Change `optimization`, point it at the right `.set`, and let the bastard run.

```bash
export URL=http://127.0.0.1:8888/darwinex/tester
export TOK=changeme-mt5-httpapi-token

tmp_ini=$(mktemp)
job_json=$(mktemp)
trap 'rm -f "$tmp_ini" "$job_json"' EXIT

curl -sS -X POST "$URL/backtest/build-ini" \
  -H "Authorization: Bearer $TOK" \
  -H "Content-Type: application/json" \
  -d '{
    "symbol":"GBPCAD",
    "timeframe":"M15",
    "expert":"EA Studio GBPCAD M15 1615044595.ex5",
    "last_years":1,
    "modelling":"open-prices",
    "expert_parameters":"ea studio gbpcad m15 1615044595.take-profit-opt-80-92-step4.set",
    "optimization":2,
    "optimization_criterion":0,
    "report_name":"gbpcad-m15-opt"
  }' > "$tmp_ini"

curl -sS -X POST "$URL/backtest" \
  -H "Authorization: Bearer $TOK" \
  -F "ini=@$tmp_ini;filename=tester.ini" \
  -F "expert_name=EA Studio GBPCAD M15 1615044595.ex5" \
  -F "set_name=ea studio gbpcad m15 1615044595.take-profit-opt-80-92-step4.set" \
  -F "top_passes=20" > "$job_json"

JOB=$(jq -r '.job_id' "$job_json")
echo "Submitted job: $JOB"

while :; do
  STATUS_JSON=$(curl -sS -H "Authorization: Bearer $TOK" "$URL/backtest/$JOB")
  STATUS=$(printf '%s' "$STATUS_JSON" | jq -r '.status')
  echo "Status: $STATUS"
  [[ "$STATUS" == completed || "$STATUS" == failed || "$STATUS" == cancelled ]] && break
  sleep 10
done

printf '%s\n' "$STATUS_JSON" | jq '{job_id,status,report_name,optimization_type,optimization_cache,optimization_results}'
```
