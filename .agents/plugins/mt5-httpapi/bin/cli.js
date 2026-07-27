#!/usr/bin/env node
// mt5-httpapi MCP bridge. A thin stdio<->HTTP proxy: forwards MCP over stdio to a
// running mt5-httpapi server's Streamable-HTTP endpoint (`$MT5_API_URL/mcp/`),
// authenticating with `$MT5_API_TOKEN` when the server requires it.
//
// stdout IS the MCP protocol channel, so diagnostics go to stderr only — the
// sole output here is a fatal pre-launch console.error (user-facing CLI
// output). The token is passed to the proxy as an argv header, never logged.
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);

const MCP_PATH = "/mcp/";

const base = process.env.MT5_API_URL;

if (!base) {
  console.error(
    `[mt5-httpapi-mcp] Missing MT5_API_URL.

Point this bridge at your running mt5-httpapi terminal, e.g.:
  export MT5_API_URL=http://localhost:8888/<broker>/<account>

mt5-httpapi is self-hosted — see https://github.com/psyb0t/mt5-httpapi`,
  );
  process.exit(1);
}

const url = `${base.replace(/\/+$/, "")}${MCP_PATH}`;
const token = process.env.MT5_API_TOKEN;
const proxyEntry = require.resolve("mcp-remote/dist/proxy.js");

const args = [proxyEntry, url, "--transport", "http-only"];
if (token) {
  args.push("--header", `Authorization: Bearer ${token}`);
}
args.push(...process.argv.slice(2));

const result = spawnSync(process.execPath, args, { stdio: "inherit" });
process.exit(result.status ?? 1);
