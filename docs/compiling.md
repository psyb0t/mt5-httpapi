# Compiling MQL5

`POST /compile` takes MQL5 source text and hands back a compiled `.ex5`. No
Windows machine, no MetaEditor GUI, no RDP session.

This exists because MetaEditor is the only thing that can produce an `.ex5`, and
it only runs on Windows — which this stack already has. If you are generating,
templating, or patching EA source anywhere else (CI, a code generator, a web
app, an agent), this is how you get a binary out of it without a human in the
loop.

The endpoint is source-text-only by design. It takes no path, reads nothing off
disk on your behalf, and gives you no control over MetaEditor's arguments. See
[Security](#security) for why that matters.

## Quick start

```bash
curl -sS -X POST -H "Authorization: Bearer $MT5_API_TOKEN" \
  -H 'Content-Type: application/json' \
  "$MT5_API_URL/compile" \
  -d '{
    "source": "void OnTick() {}",
    "filename": "MyEA.mq5"
  }' | jq -r 'if .ok then .ex5_base64 else .log end'
```

Decode the binary:

```bash
curl -sS -X POST -H "Authorization: Bearer $MT5_API_TOKEN" \
  -H 'Content-Type: application/json' \
  "$MT5_API_URL/compile" -d @payload.json \
  | jq -r .ex5_base64 | base64 -d > MyEA.ex5
```

## Request

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `source` | string | yes | The complete `.mq5` text. Capped at `COMPILE_MAX_SOURCE_BYTES` (default 2 MB). |
| `filename` | string | no | Reduced to a bare stem — see [Security](#security). Defaults to `ea.mq5`. A name ending in `.mq4` compiles the source as MQL4 — see [MQL4](#mql4). |
| `ea_version` | string | no | Recorded in the server log to correlate a compile with your build. Not passed to the compiler. 1–64 characters of `A-Z a-z 0-9 . _ + -`; anything else is a 400. |

## Responses

Every response is JSON, including failures. `log` is always a string, never
`null`, and is capped at the last 8 KB.

**200 — compiled.**

```json
{
  "ok": true,
  "ex5_base64": "AAEC...",
  "log": "Result: 0 errors, 1 warnings, 143 msec elapsed",
  "warnings": 1,
  "include_hash": "sha256:a8694a3b..."
}
```

`ok: true` always comes with a non-empty `ex5_base64`. If the compiler reports
success but produces no binary, that is reported as a failure, not a success.

`include_files` is present only when `COMPILE_INCLUDE_DIGESTS` names something,
and carries a digest per matching header:

```json
"include_files": {"MyLib.mqh": "sha256:17d64580..."}
```

It exists because `include_hash` cannot say *what* moved. Upgrading the stock
MQL5 library and editing your own shared header both change it, and the correct
responses are opposites — the first needs no rebuild, the second needs every
dependent artifact rebuilt. Without per-header digests a caller has to assume
the expensive one. Keys are paths relative to the include root.

A configured header that is **absent from the tree** is absent from this map —
never null. That is a distinct third case, and it is worth handling as one:

| Observed | Means | Response |
| --- | --- | --- |
| digest unchanged | that header did not move | nothing, even if `include_hash` moved |
| digest changed | that header was edited | rebuild what depends on it |
| **key gone** | the header is not in the tree the compiler read | **do not rebuild — investigate** |
| whole field absent | nothing is configured, or the tree was unreadable | unknown; treat as rebuild |

The third row is the one that bites. A missing header is a provisioning fault on
*this* host, not drift on the caller's side, and rebuilding against it cannot
succeed — every dependent source fails with `error 106: file not found`. A
caller that treats a vanished key as "rebuild" queues a run of guaranteed 422s
instead of alerting someone.

`include_hash` identifies the include tree this binary was built against —
sha256 over the relative paths and contents of everything under the `/inc:`
root. Record it with the build and "which artifacts used a library that has
since changed" becomes a comparison instead of an assumption. It is computed
from the tree the compiler actually read, so if the mirror were stale the hash
reports the stale tree rather than claiming the current one. It follows the
tree as it is at each compile: an edit made in the terminal's `MQL5` folder, or
a mirror refresh done by another API process, shows up in the next response.
Omitted if the tree cannot be read; never guessed.

**422 — the source did not compile.** This is your code being wrong, not the
server. `log` carries MetaEditor's own diagnostics.

```json
{
  "ok": false,
  "log": "ea.mq5(12,5) : error 160: expression of 'void' type is illegal\nResult: 3 errors, 0 warnings",
  "errors": 3
}
```

A missing `#include` lands here too — it is a compile error, not a server fault.

**504 — the compile exceeded its deadline**, or waited too long for the
compiler lock.

```json
{"ok": false, "log": "compile timeout after 30s"}
```

**413 — the request is too large.** Sent when the declared body exceeds the
request bound, or the decoded `source` exceeds `COMPILE_MAX_SOURCE_BYTES` —
in both cases before anything reaches disk. The log names the limit:

```json
{"ok": false, "log": "source is 3145728 bytes; this server accepts at most 2097152 (COMPILE_MAX_SOURCE_BYTES)"}
```

**429 — too many compiles waiting.** One compile runs and at most two wait
behind it; a request beyond that is answered at once, with a `Retry-After`
header, instead of holding a server thread. See [Concurrency](#concurrency).

```json
{"ok": false, "log": "compile busy: 2 requests are already waiting; retry shortly"}
```

**400 — malformed or refused request**: no `source`, a malformed `ea_version`,
or source whose `#include`, `#resource` or `#property icon` names a file
outside the allowed trees (see [What the source can reach](#what-the-source-can-reach)).
The log names the directive and the path.
**401 — bad or missing credentials.**
**500 — the host cannot compile** (MetaEditor missing, unreadable,
unlaunchable), an unexpected server error (reported as a generic
`"internal error"` — details go to the server log, not the response), or a
compiled binary over `COMPILE_MAX_EX5_BYTES`. That last one means the source
compiled but the server refuses to return an artifact that size — it is
checked on disk, before the binary would be read into memory and
base64-inflated into the response. Raise the cap if your EA legitimately
embeds resources that big.

Warnings never fail a compile. MetaEditor exits non-zero on warnings as well as
errors, so the exit code is not used to decide the outcome — the log is parsed
and the produced binary is the tiebreaker.

## Configuration

```yaml
# config/config.yaml

# Optional second credential, accepted ONLY on /compile. api_token keeps
# working everywhere including here. Leave empty if you do not need it.
compile_api_token: ""
```

Environment overrides, all optional:

| Variable | Default | Purpose |
| --- | --- | --- |
| `COMPILE_API_TOKEN` | unset | Compile-only bearer token. |
| `COMPILE_TERMINAL_DIR` | `terminals/metaquotes/base` | Terminal directory whose `MetaEditor64.exe` is used. |
| `COMPILE_MT4_TERMINAL_DIR` | `compile-mt4` | Directory holding the MT4 MetaEditor and `MQL4` tree, used for `.mq4` requests. |
| `COMPILE_INCLUDE_DIR` | `<terminal>/MQL5` | Passed to MetaEditor as `/inc:`. `#include <Foo.mqh>` resolves under `<this>/Include/`. |
| `COMPILE_WORK_DIR` | `logs/compile-work` | Parent of the per-request temp directories. |
| `COMPILE_LOCAL_CACHE` | unset | Mirror the toolchain onto local disk — see [Performance](#performance). |
| `COMPILE_INCLUDE_DIGESTS` | unset | Comma-separated globs (relative to the include root) whose per-file digests are reported as `include_files`. |
| `COMPILE_TIMEOUT` | `30s` | Per-compile deadline. A bare number is seconds (`45`), or give units (`45s`, `1m`). Hard ceiling 60s. |
| `COMPILE_MAX_SOURCE_BYTES` | `2097152` (2 MB) | Reject a larger `source` with 413, before it is written to disk. |
| `COMPILE_MAX_EX5_BYTES` | `16777216` (16 MB) | Refuse to return a larger compiled binary, before it is read or encoded. |

A zero, negative or unparseable `COMPILE_TIMEOUT`, `COMPILE_MAX_SOURCE_BYTES` or
`COMPILE_MAX_EX5_BYTES` falls back to its default with a warning in the API
log, and a timeout above 60s is lowered to 60s, also with a warning. None of
them stops the API from starting.

### Which terminal compiles

Compiles run in a dedicated terminal directory, not a broker terminal.
MetaEditor is a separate executable from `terminal64.exe` and does not contend
with a running terminal for the SDK — but it does write into the directory it
compiles under, and sharing that with a live terminal or a running Strategy
Tester buys nothing. `metaquotes/base` is the default because it ships
MetaEditor and no terminal runs out of it.

### Custom includes

Drop `.mqh` files into `<COMPILE_INCLUDE_DIR>/Include/` and `#include <Name.mqh>`
resolves. If you are generating source against a library you maintain
elsewhere, sync it into that directory as part of your deploy — a stale `.mqh`
compiles clean and then misbehaves at runtime, which is the worst failure shape
available.

Edits are picked up by a running server without a restart. With
`COMPILE_LOCAL_CACHE` set the include tree is re-validated against the source at
most once every `INCLUDE_REFRESH_SECONDS` (60s), so an updated header reaches
builds within that window rather than at the next restart. If you have just
changed a shared library and are about to rebuild everything that depends on
it, let that window pass first — otherwise the first builds of the batch can
still use the previous copy, and they will report success while doing it.

## MQL4

The same route compiles MQL4. Send `"filename": "MyEA.mq4"` and the server uses
the MT4 MetaEditor instead of MetaEditor 5; anything else is MQL5 exactly as
before. Nothing else about the request changes.

The 200 response carries the binary as `ex4_base64` instead of `ex5_base64`
(the two are never both present), and has no `include_hash` / `include_files`.
422, 504, 413, 400, 401 mean what they do for MQL5.

This is a compiler only. There is no MT4 terminal, account, or tester behind
it, and it is not visible to anything that enumerates broker terminals.

Set up by putting an MT4 installation in `COMPILE_MT4_TERMINAL_DIR` (default
`compile-mt4/` beside `terminals/`, i.e. `C:\Users\Docker\Desktop\Shared\compile-mt4`
in the guest). It needs `metaeditor64.exe` or `metaeditor.exe` and the `MQL4`
folder next to it (the installer creates both; `MQL4\Include` must exist or
`#include <stdlib.mqh>` fails with error 106). With `COMPILE_LOCAL_CACHE` set,
the MT4 toolchain is mirrored to `<cache>\mt4` the same way as MQL5's. Without
an install, an `.mq4` request answers `500 "MT4 MetaEditor is not available on
this host"`. MQL5 is unaffected.

## Performance

If your terminals live on a network or host-shared mount, the compile is not
what costs you — dragging MetaEditor across the mount is.

Measured on a docker-hosted Windows VM with the terminals on a 9p share: a
compile MetaEditor's own log timed at **4.1s** took **29s** wall-clock, on every
request. The page cache does not save you, and three concurrent requests then
queue past a reverse proxy's default 60s read timeout.

Set `COMPILE_LOCAL_CACHE` to a path on the VM's own disk:

```yaml
compile_local_cache: "C:\\mt5-compile"
```

On the first compile the server copies MetaEditor, its `Config`, and the include
tree there — about 105 MB, once per process — and compiles from local disk
afterwards. `terminal64.exe`, `metatester64.exe` and `Bases` are not copied; a
compile never reads them.

Only files that are missing or changed are copied, compared on size and
whole-second mtime, so a mirror that survived a restart costs nothing to
re-validate. This matters more than it looks: the stock MQL5 `Include` tree is
~260 files, and copying it unconditionally took **103s** on a 9p share —
directly in front of the first caller after every process start. Populating it
the first time still costs that once.

Even with the mirror warm, the first compile after a VM restart pays
MetaEditor's own cold load: measured at **30–55s** on a busy host, against ~2–3s
warm. With `COMPILE_LOCAL_CACHE` set, the server now absorbs that itself — it
compiles a throwaway EA in the background 180s after start, so a real caller
finds MetaEditor warm.

That warm-up is deliberately gated, and the gates matter more than the compile:

- **Delayed**, because the VM launches every terminal at boot and a MetaEditor
  run added to that contention slows the guest exactly when its health probe is
  most marginal.
- **Claimed once per VM per boot**, via an exclusive file in the cache
  directory. Every API process exposes `/compile` and they share that
  directory, so an ungated warm-up starts one MetaEditor per terminal — twenty
  at once on a busy host. The claim records the boot it was made in, so the
  next boot warms up again however soon it comes (with `reboot_interval: 30`,
  an age-based expiry would skip every other boot).
- **Yields to real work.** It takes the compile lock non-blocking and gives up
  if a compile is running; a caller queuing behind a warm-up would defeat it.

Failures are logged and ignored — the worst case is that the next real compile
pays the cold load, exactly as before.

If the mirror cannot be built (unwritable path, no space) the server logs a
warning and falls back to the configured paths. A slow compile beats a broken
one.

Leave it unset if your terminals are already on local disk — then there is
nothing to win.

## Concurrency

Compiles are serialized behind one lock and the request is synchronous. A queue
would add failure modes — lost jobs, status polling, restart recovery — to buy
nothing, because the work itself cannot overlap. Concurrent callers wait; a
caller that waits longer than the compile deadline plus 30s gets a 504 rather
than a hung connection.

At most two callers wait. A waiting compile holds one of the API's server
threads, which every route shares, so an unbounded wait would let a caller
holding only the compile token tie up the pool and stall orders, positions and
`/ping`. The third waiter and beyond get an immediate `429` with `Retry-After`.

The lock covers every API process using the same toolchain, not just one:
MetaEditor is single-instance per installation directory, and every process
on a VM serves `/compile`. It is an operating-system byte-range lock on
`.compile-inflight.lock` in the local mirror (or, without one, beside
`MetaEditor64.exe`), so the OS releases it the moment its holder exits or is
killed; there is no timeout after which a holder is presumed dead. Refreshing
the mirror happens under the same lock, so no process's MetaEditor reads a
header while another process rewrites it.

**Tell callers to compile one at a time.** Since the lock serializes them
anyway, concurrency buys no throughput — but it does stack waits on top of a
fixed deadline, so the callers at the back of the queue start returning 504
while the same requests sent sequentially would all have succeeded. Measured on
a loaded host: five concurrent compiles of an EA including `<Trade\Trade.mqh>`
took 92s in total, with individual waits of 24/47/72/90/92s and one 504.

Sustained parallel compiles are also enough to saturate a VM's CPU. If
something supervises that VM on a health probe with a short timeout, the probe
starts failing under compile load and the supervisor restarts a VM that was
merely busy — converting a slow batch into an outage. Give the probe enough
timeout to survive a CPU-saturated host.

If you put a reverse proxy in front of this, give `/compile` a read
timeout longer than the handler's worst case: the lock wait (deadline + 30s)
plus the compile itself, 150s at the 60s ceiling. nginx defaults to 60s,
which a wait will cross — and then the caller gets the proxy's HTML error
page instead of the JSON documented here. The nginx config this project
generates gives `/<broker>/<account>[/<instance>]/compile` 180s and leaves
every other route at nginx's default.

Each request compiles inside its own temp directory, which is removed on every
exit path including timeouts and crashes. Two callers compiling different
sources under the same `filename` cannot see each other's output.

## Security

The endpoint accepts source text and nothing else. Specifically:

- **No caller-supplied paths.** `filename` is stripped to a bare stem —
  directories, drive letters and `..` are discarded — then re-suffixed. It
  cannot escape the temp directory.
- **No caller-controlled compiler arguments.** `/compile:`, `/log:` and `/inc:`
  are all computed server-side. Sending `log` or `include` in the body does
  nothing.
- **No file reads on your behalf.** The handler only touches the request's own
  temp directory. MetaEditor itself resolves `#include`, `#resource` and
  `#property icon`, so those are checked before it runs — see
  [What the source can reach](#what-the-source-can-reach).
- **No trading surface.** The handler never touches the MT5 SDK. It cannot
  place an order, modify a position, or restart a terminal.

### What the source can reach

Three directives make MetaEditor read a file named in the source, with the
API process's file access: `#include` compiles it in, and `#resource` and
`#property icon` embed it in the `.ex5`. MetaEditor resolves `"file"` against
the source's directory (the request's temp directory), and `<file>` and a
leading `\` against the `/inc:` tree.

Measured against MetaEditor 5.00 build 5836, before the handler checked
anything:

| Directive | Absolute path | `..` walk | `\\server\share` |
| --- | --- | --- | --- |
| `#include "…"` | **read** | **read** | not found (joined to the temp directory) |
| `#include <…>` | **read** | **read** | — |
| `#resource` | refused (`error 313`) | refused (`error 313`) | refused |
| `#property icon` | not found (joined to the temp directory) | **read** | — |

Forward slashes, indentation and a comment before the `#` made no difference.
The icon reads fail on anything that is not a valid `.ico` (`error 342: invalid
icon format`), which still tells the caller the file exists, and a real icon
from anywhere on the disk is embedded in the returned `.ex5`.

A file that is read but is not MQL5 still does not compile, but the
diagnostics in `log` quote its tokens back: `#include "C:\Windows\win.ini"`
answered with `win.ini(1,3) : error 175: 'for' - expressions are not allowed
on a global scope`. An include of the API's own `config.yaml` would do the same
with the tokens in it.

So the handler refuses, with a 400 and before MetaEditor runs, any of these
directives whose path has a drive letter or `:`, starts with `\\` or `//`, has a
`..` segment (or any segment made only of dots and spaces, which Windows may
trim to one), names a
Windows device such as `CON`, or is not a literal `"path"` / `<path>`. An
`#include` path may not start with `\` or `/` either; for `#resource` and
`#property icon` a leading `\` means the `/inc:` tree and is allowed. The
check runs on the source with comments removed, and matches the directive
more loosely than MetaEditor does (any case, spaces after `#`), so a directive
MetaEditor would ignore can be refused but one it honours cannot slip past.

The check reads lines the way the preprocessor does. Every `\r\n` and lone `\r` in the source becomes `\n` before the check, and the file MetaEditor compiles is written from that same text. For the check only, `\u0085`, ` ` and ` ` also break lines, backslash-newline continuations are joined, and form feed and vertical tab count as spaces. A `#` left alone on its line once comments are removed is refused, because a comment spanning a line break between `#` and the directive name still joins them for the preprocessor.

`tests/real_compile/` runs these cases against a deployed endpoint. Against
build 5836 without this check, MetaEditor ran on all sixteen and read the
target in nine.

`#import` is not checked. It names a DLL or `.ex5` to load when the program
runs, not a file MetaEditor reads: an `#import` of a file that does not exist
compiles.

### A compile-only credential

`compile_api_token` exists so a system that only needs to compile does not have
to hold a token that can also trade.

`api_token` unlocks order placement, position management and terminal restart.
If you are handing a build pipeline, a code generator, or a third-party service
the ability to compile, giving it `api_token` gives it your account too. Set
`compile_api_token` and hand out that instead: it is accepted on `/compile` and
rejected everywhere else.

Both tokens work on `/compile`. Only `api_token` works anywhere else.

> Compiling arbitrary source is not a sandbox. MetaEditor parses attacker-
> controlled text, and MQL5 has `#import` for DLLs. Treat `/compile` as trusted
> input regardless of which token opens it, and do not expose it to the open
> internet.
