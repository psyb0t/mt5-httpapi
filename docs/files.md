---
title: "File API"
description: "List, read, upload, unzip and delete files in a terminal's install directory and the compile tree, with the paths the API refuses."
nav_order: 3
parent: "REST API"
---

# File API: read and write a terminal's files over HTTP

List, download, upload, unzip and delete files in a terminal's install directory, the folder holding `terminal64.exe`. That covers shared libraries in `MQL5/Include`, DLLs and `.ex5` imports in `MQL5/Libraries`, an expert's own data in `MQL5/Files`, and the logs, all without RDP or touching the host. A second tree reaches the MQL5 directory `POST /compile` builds against, so a whole library can be uploaded once and compiled against from then on.

## Contents

- [Enabling it](#enabling-it)
- [Endpoints](#endpoints)
- [Uploading a zip](#uploading-a-zip)
- [What the API refuses](#what-the-api-refuses)
- [The compile tree](#the-compile-tree)
- [Example: an expert that uses a library](#example-an-expert-that-uses-a-library)
- [MCP tools](#mcp-tools)
- [Errors](#errors)

## Enabling it

The file API is opt-in and off by default, because it reaches everything an expert can, including code that runs inside the terminal:

```yaml
# config/config.yaml
files:
  enabled: true
  max_extract_bytes: 209715200   # 200 MiB unpacked per ?extract (default)
  max_extract_files: 10000       # files per ?extract (default)
```

`files: true` is shorthand for `files: {enabled: true}`. A terminal entry can opt out with `files: false`. Unlike Chart Deployments, the file API also works on backtest-mode terminals. With it off, every route below answers 404.

## Endpoints

Paths are relative to the terminal's install directory and mirror it, `/`-separated:

```
http://localhost:8888/<broker>/<account>[/<instance>]/files/<path>
```

| Method | Endpoint | Description |
| --- | --- | --- |
| `GET` | `/files`, `/files/<dir>` | List a directory |
| `GET` | `/files/<file>` | Download a file (`application/octet-stream`) |
| `PUT` | `/files/<file>` | Create or replace a file, creating its directories |
| `PUT` | `/files/<dir>?extract` | Unpack a zip into a directory (see below) |
| `DELETE` | `/files/<path>` | Delete a file, or an empty directory |
| `DELETE` | `/files/<dir>?recursive` | Delete a directory and everything in it |

The same routes exist under `/compile/files` for [the compile tree](#the-compile-tree).

```bash
AUTH=(-H "Authorization: Bearer $MT5_API_TOKEN")

# list a folder
curl "${AUTH[@]}" "$MT5_API_URL/files/MQL5/Include"

# upload one file (raw body; a multipart field named "file" works too)
curl "${AUTH[@]}" -X PUT --data-binary @Signals.mqh \
  "$MT5_API_URL/files/MQL5/Include/MyLib/Signals.mqh"

# read what an expert wrote
curl "${AUTH[@]}" "$MT5_API_URL/files/MQL5/Files/mybot/state.json"

# the terminal's journal for today (UTF-16, like every MT5 log)
curl "${AUTH[@]}" "$MT5_API_URL/files/logs/$(date +%Y%m%d).log" -o journal.log

# remove a library folder
curl "${AUTH[@]}" -X DELETE "$MT5_API_URL/files/MQL5/Include/MyLib?recursive"
```

A directory listing is the JSON version of `ls -al`. The response carries `count` and `total_size`, plus one entry per file or directory, directories first:

```json
{
  "tree": "terminal",
  "path": "MQL5/Include/MyLib",
  "count": 1,
  "total_size": 5000,
  "entries": [
    {
      "name": "Signals.mqh",
      "path": "MQL5/Include/MyLib/Signals.mqh",
      "type": "file",
      "size": 5000,
      "size_human": "4.9K",
      "mode": "-rw-rw-rw-",
      "attributes": ["archive"],
      "nlink": 1,
      "modified_at": 1791580800,
      "modified": "2026-10-09T20:00:00Z",
      "created_at": 1791580800,
      "created": "2026-10-09T20:00:00Z",
      "accessed_at": 1791580800,
      "accessed": "2026-10-09T20:00:00Z",
      "is_symlink": false,
      "readable": true,
      "writable": true
    }
  ]
}
```

- `size_human` is `ls -h` style. Directories report size 0.
- `mode` is `ls`'s mode string.
- `attributes` lists the Windows file attributes (`readonly`, `hidden`, `system`, `archive`, `reparse_point`, ...). Windows has no owner or group in the `ls` sense, so the listing has none.
- Times come as epoch seconds (`*_at`) and as UTC ISO 8601.
- `readable` and `writable` say what this API allows, not what the file system does.
- A symlink or junction also carries `link_target` and `link_outside_tree`. One that leads out of the tree is listed but can be neither read nor written.

An upload answers `201` when it created the file and `200` when it replaced one, with `tree`, `path`, the stored `size` and `sha256`, and `created`.

Uploads are capped at `max_upload_body_bytes` (25 MiB, the same as nginx's limit). A request whose `Content-Length` is over the cap is refused before the handler runs, with a 413 that carries an `error` but no `code`. Every write is atomic: the bytes land in a staging file beside the target and are renamed over it, so the terminal never reads half a file.

## Uploading a zip

Add `?extract` to a `PUT` and the body is treated as a zip. The path then names the directory it unpacks into:

```bash
cd MyLib && zip -r ../MyLib.zip . && cd ..
curl "${AUTH[@]}" -X PUT --data-binary @MyLib.zip \
  "$MT5_API_URL/files/MQL5/Include/MyLib?extract"
```

- The archive's tree is merged into the directory. Files it names are replaced, and everything else already there is left alone. To get an exact copy, delete the directory first with `?recursive`.
- The zip itself is never stored. The API unpacks it into a staging directory, checks every entry, and only then moves the files into place. A bad entry or a cap exceeded anywhere fails the whole upload with nothing written. The files are then moved one at a time, so a file the terminal holds open (`FILE_LOCKED`) or a directory in the way (`CONFLICT`) at that stage leaves the files moved before it in place.
- The response is `201` with `tree`, `path`, `count` and `files`, which lists every file written with `path`, `size` and `sha256`.
- Refused: entries that would leave the target (`../`, absolute paths, drive letters), symlinks, encrypted entries, the same file named twice, and anything the single-file rules refuse. An archive that unpacks to more than `max_extract_files` files or `max_extract_bytes` bytes is refused with 413. The API counts the bytes it actually inflates, not the size the archive declares. `max_extract_bytes` is raised to at least 1 MiB and `max_extract_files` to at least 1.
- `?extract=0` (or `false`) stores the bytes as an ordinary file.

## What the API refuses

The API refuses some paths in every request, with no setting to change that:

| Path | Read | Write / delete | Why |
| --- | --- | --- | --- |
| `mt5start.ini`, `Config/accounts.dat` | no | no | The broker login and password, and the terminal's saved accounts |
| `terminal64.exe`, `MetaEditor64.exe`, `metatester64.exe` | yes | no | The running terminal's binaries |
| `MQL5/Experts/Uploaded/`, `MQL5/Files/chartctl/`, `chartctl/` | yes | no | Chart Deployments' staged experts, protocol files and registry. Use the [Chart Deployments](chart-deployments.md) endpoints for those |
| `Experts/Uploaded/`, `Files/chartctl/` in the [compile tree](#the-compile-tree) | yes | no | The same Chart Deployments directories, in case the compile tree is a live terminal's `MQL5` |
| The tree root, or a directory holding any of the above | | no | |

Every path is also checked for anything that could leave the tree on Linux or Windows: `..` segments, absolute and drive paths, `:` (alternate data streams), Windows device names (`CON`, `NUL`, `COM1`, ...), characters Windows forbids, segments ending in a dot or space, segments over 255 characters, names starting with `.mt5api-` (the API's own staging files, which listings also skip), and symlinks or junctions that resolve outside the root. Comparisons ignore case, as Windows does. A protected path answers 403 whether it exists or not.

Everything else is open, `MQL5/Libraries` included. A DLL put there runs inside the terminal the next time an expert imports it (`AllowDllImport` is on in every terminal this stack provisions). Hand out the token that can call these routes accordingly.

## The compile tree

`/compile/files` is rooted at the MQL5 directory `POST /compile` builds against (`compile_include_dir`, by default `<compile_terminal_dir>/MQL5`). Every terminal on the VM shares it, so any terminal's prefix reaches the same files. Put a library there and every compile can `#include` it:

```bash
curl "${AUTH[@]}" -X PUT --data-binary @MyLib.zip \
  "$MT5_API_URL/compile/files/Include/MyLib?extract"

# then compile against it
#   #include <MyLib/Signals.mqh>
```

With `compile_local_cache` set, compiles read a mirror of this same tree on the VM's own disk, so what you put through `/compile/files` reaches a compile with or without it. A write or delete through `/compile/files` marks the mirror stale, and the next compile in any terminal's API process re-validates it before building, so it never builds against the old copy. A change made any other way, such as directly on the host, still waits up to 60 seconds, as described in [Compiling MQL5](compiling.md#custom-includes).

The compiled `.ex5` carries the library inside it, so the terminal that runs the expert never needs the `.mqh` files. The exception is `#import` of an `.ex5` or DLL: those load at run time from the running terminal's own `MQL5/Libraries`, so upload them there through `/files`.

## Example: an expert that uses a library

A library usually comes in two parts: headers the expert `#include`s, which only the compiler needs, and code the expert `#import`s at run time (a compiled `.ex5` library or a DLL), which the terminal running the expert needs. Say the library is:

```
MyLib/Signals.mqh     the header your expert includes
MyMath.mq5            a library (#property library) it imports
```

```mql5
// MyLib/Signals.mqh
#import "MyMath.ex5"
double Scale(double x, double k);
#import

double Half(double x) { return Scale(x, 0.5); }
```

```mql5
// MyMath.mq5
#property library
double Scale(double x, double k) export { return x * k; }
```

```mql5
// MyBot.mq5
#include <MyLib/Signals.mqh>

int OnInit()
{
   Print("half of 42 is ", Half(42.0));
   return INIT_SUCCEEDED;
}

void OnTick() {}
```

1. Put the headers in the compile tree, so `#include <MyLib/...>` resolves:

   ```bash
   (cd MyLib && zip -r ../MyLib.zip .)
   curl "${AUTH[@]}" -X PUT --data-binary @MyLib.zip \
     "$MT5_API_URL/compile/files/Include/MyLib?extract"
   ```

2. Compile the library and put the `.ex5` in the terminal's `MQL5/Libraries`, where `#import "MyMath.ex5"` looks for it at run time:

   ```bash
   jq -n --rawfile src MyMath.mq5 '{source: $src, filename: "MyMath.mq5"}' \
     | curl "${AUTH[@]}" -H "Content-Type: application/json" -d @- "$MT5_API_URL/compile" \
     | jq -r .ex5_base64 | base64 -d > MyMath.ex5
   curl "${AUTH[@]}" -X PUT --data-binary @MyMath.ex5 \
     "$MT5_API_URL/files/MQL5/Libraries/MyMath.ex5"
   ```

   A DLL goes to the same place: `PUT /files/MQL5/Libraries/mylib.dll`.

3. Compile the expert. The compiler reads `MyLib/Signals.mqh` from the compile tree; the `.ex5` it returns does not need the header again:

   ```bash
   jq -n --rawfile src MyBot.mq5 '{source: $src, filename: "MyBot.mq5"}' \
     | curl "${AUTH[@]}" -H "Content-Type: application/json" -d @- "$MT5_API_URL/compile" \
     | jq -r .ex5_base64 | base64 -d > MyBot.ex5
   ```

4. Run it, for example with [Chart Deployments](chart-deployments.md#quick-start): `POST /experts` with `MyBot.ex5`, then `POST /deployments`. Its `Print` lands in the terminal's `MQL5/Logs`, which `GET /files/MQL5/Logs/<yyyymmdd>.log` downloads.

The compile tree is shared by every terminal on the VM, so step 1 is done once. `MQL5/Libraries` belongs to one terminal, so step 2 is repeated for every terminal that runs the expert.

An expert loads its libraries when it starts. To give a running expert a new version, upload it and then restart the expert, for a deployment by pausing and resuming it. If the terminal holds the old file open, as Windows does with a DLL a running expert has loaded, the upload answers 409 `FILE_LOCKED`. Pause the deployment, wait for its chart to close, upload, then resume.

## MCP tools

Both MCP endpoints have typed tools for this, so an agent never needs curl:

- `list_files(path?, tree?)`
- `get_file(path, tree?)` returns `text` for UTF-8 or UTF-16 text (MT5 logs are UTF-16), otherwise `content_base64`, plus `size` and `sha256`. Files over 16 MiB are refused; use REST for those.
- `put_file(path, content? | content_base64?, extract?, tree?)`
- `delete_file(path, recursive?, tree?)`

`tree` is `terminal` (the default) or `compile`. On the unified endpoint each tool also takes `broker`, `account` and `instance`, and `list_terminals` reports `files` per terminal. A per-terminal endpoint lists the tools only when the file API is on for it. The unified endpoint always lists them, and calling one on a terminal without the file API says so instead of returning a bare 404. The `/mcp` request is capped at 25 MiB, about 18 MiB of file after base64.

## Errors

Errors are JSON with an `error` message and a `code`:

| Status | `code` | Meaning |
| --- | --- | --- |
| 400 | `BAD_PATH` | The path is malformed or would leave the tree |
| 400 | `BAD_ARCHIVE` | The `?extract` body is not a usable zip |
| 400 | `BAD_REQUEST` | A multipart upload without a `file` field |
| 403 | `PROTECTED` | See [What the API refuses](#what-the-api-refuses) |
| 404 | `NOT_FOUND` | Nothing at that path |
| 409 | `CONFLICT` | A file where a directory is needed or the other way round, or a non-empty directory deleted without `?recursive` |
| 409 | `FILE_LOCKED` | Windows refused because another process, usually the terminal, holds the file open. Retry once it lets go |
| 413 | `TOO_LARGE`, `ARCHIVE_TOO_LARGE` | The upload or the unpacked archive is over its cap |
