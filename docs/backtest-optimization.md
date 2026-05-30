# Backtest Optimization Guide

This guide documents how mt5-httpapi runs MetaTrader 5 Strategy Tester optimizations over HTTP, what each MT5 optimization mode actually does, and what artifacts you should expect back from the API.

## Requirements

Optimization requests only run correctly against a terminal configured with `mode: backtest` in `config/config.yaml`.

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

If you submit an optimization against a `live` terminal, MT5 can silently exit without producing a usable Strategy Tester report because the portable directory is already owned by the running terminal process.

## Overview

The optimization flow is always the same:

1. Build or author a tester INI.
2. Provide an `.ex5` expert.
3. Provide a `.set` file containing MT5 optimization ranges.
4. Submit `POST /backtest`.
5. Poll `GET /backtest/<jobId>` until `completed` or `failed`.
6. Inspect `optimizationResults`, `optimizationCache`, `/report`, and `/log`.

## Optimization Modes

| Mode | MT5 meaning | Scope | Primary parsed source | Report artifact |
| ---- | ----------- | ----- | --------------------- | --------------- |
| `1` | Slow complete algorithm | Single `[Tester].Symbol` | XML report | `<report>.xml` |
| `2` | Fast genetic algorithm | Single `[Tester].Symbol` | XML report | `<report>.xml` |
| `3` | All symbols selected in Market Watch | Market Watch symbol set | Tester cache `.opt` | `<report>.symbols.xml` |

### Mode 1: Slow Complete

Mode `1` enumerates the full search space for the active symbol. Use it when you need exhaustive coverage and the parameter grid is small enough to be practical.

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
    "lastYears":1,
    "modelling":"open-prices",
    "expertParameters":"ea studio gbpcad m15 1615044595.take-profit-opt-80-92-step4.set",
    "optimization":1,
    "optimizationCriterion":0,
    "reportName":"gbpcad-m15-complete-search"
  }'
```

Example submit request:

```bash
curl -sS -X POST "$URL/backtest" \
  -H "Authorization: Bearer $TOK" \
  -F "ini=@tester.ini" \
  -F "expert_name=EA Studio GBPCAD M15 1615044595.ex5" \
  -F "set_name=ea studio gbpcad m15 1615044595.take-profit-opt-80-92-step4.set" \
  -F "topPasses=20"
```

Typical completed payload shape:

```json
{
  "status": "completed",
  "optimizationType": 1,
  "reportName": "gbpcad-m15-complete-search.xml",
  "optimizationCache": null,
  "optimizationResults": [
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

Mode `2` uses MT5's genetic optimizer for a single symbol. Use it when the search space is too large for a full exhaustive run and you want faster convergence on promising regions.

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
    "lastYears":3,
    "modelling":"open-prices",
    "expertParameters":"myea-optimizer.set",
    "optimization":2,
    "optimizationCriterion":5,
    "reportName":"gbpusd-m15-sharpe-search"
  }'
```

Example completed payload shape:

```json
{
  "status": "completed",
  "optimizationType": 2,
  "reportName": "gbpusd-m15-sharpe-search.xml",
  "optimizationCache": null,
  "optimizationResults": [
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

Mode `3` is different from modes `1` and `2` in both scope and artifact handling.

MT5 runs the optimization across the symbols currently selected in Market Watch instead of only the `[Tester].Symbol` value. That symbol still matters because it participates in cache naming and INI generation, but the pass rows themselves are keyed to the Market Watch symbol set.

Most importantly, MT5 does not put the real optimization rows into the normal report XML for mode `3`.

What MT5 writes instead:

- `<report>.symbols.xml` in the terminal `Reports` directory
- one or more `Tester/cache/*.opt` files containing the actual optimization rows
- agent logs under `Tester/Agent-*/logs/` that identify which symbol belongs to each pass

mt5-httpapi handles this by:

- falling back from `<report>.xml` to `<report>.symbols.xml`
- discovering the matching `.opt` cache file from the normalized INI
- parsing the cache rows and sorting them by `Result`
- recovering pass-to-symbol mappings from agent logs
- exposing the matched cache artifact through `optimizationCache`

Example `build-ini` request:

```bash
curl -sS -X POST "$URL/backtest/build-ini" \
  -H "Authorization: Bearer $TOK" \
  -H "Content-Type: application/json" \
  -d '{
    "symbol":"GBPCAD",
    "timeframe":"M15",
    "expert":"EA Studio GBPCAD M15 1615044595.ex5",
    "lastYears":5,
    "modelling":"open-prices",
    "expertParameters":"ea studio gbpcad m15 1615044595.take-profit-opt-80-92-step4.set",
    "optimization":3,
    "optimizationCriterion":0,
    "reportName":"mode3-gbpcad-m15-last5y-rerun5"
  }'
```

Example submit request:

```bash
curl -sS -X POST "$URL/backtest" \
  -H "Authorization: Bearer $TOK" \
  -F "ini=@tester.ini;filename=tester.ini" \
  -F "expert_name=EA Studio GBPCAD M15 1615044595.ex5" \
  -F "set_name=ea studio gbpcad m15 1615044595.take-profit-opt-80-92-step4.set" \
  -F "topPasses=50"
```

Example completed payload shape from a real replay:

```json
{
  "jobId": "b05643c6d51c4a4cb0cad8a2b6c5573b",
  "status": "completed",
  "reportName": "mode3-gbpcad-m15-last5y-rerun5.symbols.xml",
  "optimizationType": 3,
  "optimizationResults": [
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
  "optimizationCache": {
    "name": "EA Studio GBPCAD M15 1615044595.all_symbols.M15.20210525.20260525.22.788ECDD113BA3097A58EF888EBEFF9CA.opt",
    "path": "C:\\Users\\Docker\\Desktop\\Shared\\terminals\\darwinex\\live\\a\\Tester\\cache\\EA Studio GBPCAD M15 1615044595.all_symbols.M15.20210525.20260525.22.788ECDD113BA3097A58EF888EBEFF9CA.opt",
    "pattern": "EA Studio GBPCAD M15 1615044595.all_symbols.M15.20210525.20260525.*.opt",
    "build": "22",
    "cacheHash": "788ECDD113BA3097A58EF888EBEFF9CA",
    "rowCount": 28,
    "sizeBytes": 20013,
    "symbolComponent": "all_symbols",
    "period": "M15",
    "fromDate": "20210525",
    "toDate": "20260525",
    "expert": "EA Studio GBPCAD M15 1615044595",
    "modifiedAt": "2026-05-25T08:19:59.371833"
  }
}
```

## `.set` Files for Optimization

Optimization only makes sense when the `.set` file contains MT5 optimization ranges.

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

- `status`: `queued`, `running`, `completed`, or `failed`
- `optimizationType`: submitted MT5 mode
- `optimizationResults`: parsed top `N` rows exposed by the API
- `optimizationCache`: metadata for the matched `.opt` file when cache parsing is used
- `reportName`: final report artifact name; mode `3` usually ends in `.symbols.xml`
- `reportUrl`: fetch raw MT5 report artifact
- `logUrl`: fetch terminal log for the job

## Raw Artifacts and Debugging

Use these rules of thumb:

- For modes `1` and `2`, start with `/report` because the XML spreadsheet is the main source of optimization rows.
- For mode `3`, start with `optimizationCache` and `optimizationResults`; `/report` exists, but it is the `.symbols.xml` header export rather than the real pass table.
- If mode `3` symbols look wrong or missing, inspect the agent logs under `Tester/Agent-*/logs/` because pass numbers are recovered from those logs.
- If `optimizationResults` is empty for mode `1` or `2`, inspect the raw XML report and terminal log first.

## End-to-End Example Script

This pattern works for any optimization mode. Change the `optimization` field in the `build-ini` payload and point to the correct `.set` file.

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
    "lastYears":1,
    "modelling":"open-prices",
    "expertParameters":"ea studio gbpcad m15 1615044595.take-profit-opt-80-92-step4.set",
    "optimization":2,
    "optimizationCriterion":0,
    "reportName":"gbpcad-m15-opt"
  }' > "$tmp_ini"

curl -sS -X POST "$URL/backtest" \
  -H "Authorization: Bearer $TOK" \
  -F "ini=@$tmp_ini;filename=tester.ini" \
  -F "expert_name=EA Studio GBPCAD M15 1615044595.ex5" \
  -F "set_name=ea studio gbpcad m15 1615044595.take-profit-opt-80-92-step4.set" \
  -F "topPasses=20" > "$job_json"

JOB=$(jq -r '.jobId' "$job_json")
echo "Submitted job: $JOB"

while :; do
  STATUS_JSON=$(curl -sS -H "Authorization: Bearer $TOK" "$URL/backtest/$JOB")
  STATUS=$(printf '%s' "$STATUS_JSON" | jq -r '.status')
  echo "Status: $STATUS"
  [[ "$STATUS" == completed || "$STATUS" == failed ]] && break
  sleep 10
done

printf '%s\n' "$STATUS_JSON" | jq '{jobId,status,reportName,optimizationType,optimizationCache,optimizationResults}'
```
