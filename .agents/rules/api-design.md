# API design rules

Prefix: API.

## JSON keys

- **API1:** Response and request keys are snake_case, matching the MT5 SDK fields the API passes through via `to_dict` (`time_msc`, `volume_real`).
- **API2:** The backtest routes and the TA route's wickworks error also send each former camelCase key beside its snake_case twin, through `with_legacy_keys` in `mt5api/jsonkeys.py`. These are deprecated. Never add a camelCase key to a new or existing response, and do not extend the legacy twins to routes that never had them.
- **API3:** Request bodies that used to take camelCase accept both forms through `accept_snake_keys`; the same field under both names with different values is refused with 400 (`ConflictingKeys`). Covered by `tests/test_jsonkeys.py`.
- **API4:** Key conversion touches only the API's own field names. Data maps keep their keys as given: an EA's input names, optimization result columns, a wickworks indicator spec. `with_legacy_keys` is not recursive on purpose (`test_legacy_conversion_does_not_reach_into_nested_data`).
- **API5:** Removing the camelCase twins is a breaking change and needs a major version (see [docs-and-releases.md](docs-and-releases.md)).

## Errors and status codes

- **API6:** An error response is JSON with an `error` string that tells the caller what was wrong and, where possible, what to send instead. Routes added since Chart Deployments also carry a stable upper-case `code` (`_err` in `mt5api/handlers/chartctl.py`, `_error` in `mt5api/handlers/files.py`, for example `DUPLICATE_CHART`, `FILE_LOCKED`, `ARCHIVE_TOO_LARGE`). New routes include `code`.
- **API7:** `POST /compile` is the exception: it answers `{ok, log}` on success and failure, including its 401, because its clients parse that shape. Keep it.
- **API8:** Use the status codes the existing handlers use: 400 for a bad body or argument, 404 for a missing ticket, symbol or path, 409 for a state conflict, 411 when a required length is missing, 413 for a body over a cap, 422 for a compile error, 429 when too many compiles are already waiting, 503 when MT5 is not initialized or the queue is full (wedged calls add `Retry-After`), 504 for an SDK or compile timeout. `POST /orders` answers 201 only when the retcode is `TRADE_RETCODE_DONE`, otherwise 200 with the broker result.
- **API9:** A missing or wrong bearer token answers 401 with `{"error": "unauthorized", "code": "UNAUTHORIZED"}` (`UNAUTHORIZED_BODY` in `mt5api/server.py`), on every REST route except `/compile` (see API7) and on the per-terminal `/mcp` gate in `mt5api/main.py`. Both use the same constant; `tests/test_auth.py` checks both.

## Gating and routing

- **API10:** A gated feature's routes exist only when the feature is on; when off they answer 404 (`test_the_routes_are_absent_unless_enabled` in `tests/test_files_api.py`; the per-terminal MCP tool gates in `tests/test_mcp_chartctl_tools.py` and `tests/test_mcp_files_tools.py`). Do not register a route that returns "disabled"; the unifier's `ChartctlDisabled` and `FilesDisabled` explain the 404 to agents instead.
- **API11:** A new route is added to the Flask route table, to the unifier catalog in `mcpunifier/mcp_server.py` (see [mcp.md](mcp.md)), to the endpoint docs, and, if it can run past 60 seconds, to nginx's long-running list (ARC16).
- **API12:** Path segments taken from callers are validated before use. File API paths go through `Tree.resolve` in `mt5api/fileapi/tree.py`; MCP tools percent-encode name and id arguments and refuse `.`, `..` and separators before any request.

## Body size

- **API13:** `_refuse_oversized_body` in `mt5api/server.py` caps every request body before parsing, from `Content-Length`: `MAX_REQUEST_BODY_BYTES` (4 MiB) for JSON, `MAX_UPLOAD_BODY_BYTES` (25 MiB, matching nginx `client_max_body_size 25m`) for multipart and file API `PUT`s. Do not add per-route caps as the only bound; an endpoint may add a tighter cap inside the handler, which fires first. Covered by `tests/test_request_body_cap.py`.
- **API14:** The cap runs after auth so an unauthenticated caller cannot probe limits, and answers JSON 413.
- **API15:** `/compile` bounds its own body from `COMPILE_MAX_SOURCE_BYTES` (411 or 413 before parsing) and skips the generic cap. Keep the two in their own places.

## Time and units

- **API16:** Broker timestamps are converted with the terminal's current offset, read through `broker_clock.offset_seconds()` (static, or measured when `utc_offset: auto`), never a module constant; see [docs/rest-api.md](../../docs/rest-api.md) before changing any time field. `tests/test_broker_time.py` covers it.
