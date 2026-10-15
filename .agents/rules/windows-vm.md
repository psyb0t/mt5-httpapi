# Windows VM rules

Prefix: WIN.

Everything under `scripts/*.bat`, `scripts/*.ps1`, `mt5api/`, `assets/` and the copies of `config_helper.py`, `check_health.py` and `webrequest_allowlist_codec.py` runs inside the Windows guest. Bugs there show up only when the VM boots, often as a stack that never comes up. Background: [docs/operations.md](../../docs/operations.md) and [docs/installation-and-configuration.md](../../docs/installation-and-configuration.md).

## Encoding

- **WIN1:** `.ps1` files are pure ASCII. Windows PowerShell 5.1 reads a `.ps1` without a BOM as ANSI, so an em dash in a string in `acquire_lock.ps1` turned into mojibake, the script failed to parse, `start.bat` read the exit code as "lock held", and every boot deadlocked. Enforced by `check_windows_ascii` in `scripts/lint.sh` (`make lint`), which also self-tests its detector.
- **WIN2:** `.bat` and `.cmd` files are pure ASCII too, comments included: `cmd.exe` reads them in the console code page. The same `check_windows_ascii` gate covers them.
- **WIN3:** Python that runs inside the VM opens text files with an explicit `encoding=` (`config_helper.py write_ini` writes UTF-8). MetaEditor writes its log as UTF-16LE with a BOM; decode it as such (`tests/test_compile.py::test_metaeditor_log_is_decoded_as_utf16`).

## Boot flow

- **WIN4:** The order is fixed: `oem-install.bat` (first boot) puts a Startup entry that calls `start.bat`. `start.bat` takes the boot lock, calls `install.bat`, installs the pip packages (rebooting when they changed), starts the event log tailer, kills lingering terminals, parses the terminal list, ensures the auto-reboot task, compiles the chartctl loader when enabled, launches each terminal (copying the broker's base install per account and instance and writing its INI through `write_ini`), starts one `api_runner.bat` per terminal, releases the lock, and then loops on `check_health.py` every 60 seconds. Keep each step idempotent: `start.bat` runs on every logon and `install.bat` marks finished work with `*.done` flags in the shared folder.
- **WIN5:** Every reboot goes through `scripts/reboot.bat`, which writes `rebooting.flag` before restarting so `install.bat` can clear its own stale lock on the next boot. Do not call `shutdown` from anywhere else. A `scheduled` reboot first runs `scripts/reboot_guard.py`, which waits (up to `reboot_max_postpone` minutes) while any API's `GET /busy` reports a backtest or a write request in flight; a new state-changing route is held by it automatically, since every method other than `GET`, `HEAD` and `OPTIONS` counts.
- **WIN6:** `start.bat` deletes `Config/common.ini` on every boot and `config_helper.py write_ini` re-seeds the WebRequest allowlist from the per-terminal desired file. Persistent terminal state that must survive a boot lives in a file the API owns, not in `common.ini`.

## Shared folder

- **WIN7:** The guest sees the code only through `data/shared/`, which `run.sh` rewrites on every `make up` (`mt5api/` is deleted and copied whole). Never edit files under `data/shared/` as the fix; change the source in the repo.
- **WIN8:** A new script the VM runs must be added to the copy list in `run.sh` (ARC14). `assets/` is mounted read-only; nothing in the guest may write there.
- **WIN9:** `config_helper.py` runs both on the host and with the VM's Python. It may use only the standard library and PyYAML (it installs PyYAML if missing; Jinja2 is needed only for `generate_compose`, which runs on the host). It cannot import `mt5api` at module level.

## Locks

- **WIN10:** `start.bat` holds a boot-scoped lock through `acquire_lock.ps1`, which stamps it with the OS boot time so a lock from a previous boot is cleared. The script exits 0 when acquired and 10 when a live instance from this boot holds it. Any other exit code is a failure of the script itself, and `start.bat` falls back to a plain `mkdir` lock instead of treating it as held. Keep that contract in both files.
- **WIN11:** Lock directories in the shared folder (`start.running`, `install.running`) survive VM reboots because the folder lives on the host. `run.sh` clears them on every start. Any new lock in the shared folder needs the same story for a VM killed mid-run.
- **WIN12:** `logs/full.log` is shared by every API process and batch script; the API writes to it under a mkdir lock (`_LockedFileHandler` in `mt5api/logger.py`). Batch scripts append whole lines only.
- **WIN13:** `/compile` serializes MetaEditor runs across API processes with a cross-process lock and bounds the waiting queue (`tests/test_compile.py` lock tests). Keep any new shared MetaEditor use behind the same lock.
- **WIN14:** GUI automation (the WebRequest allowlist in the Options dialog and the Navigator refresh) sends window messages to one terminal's own controls (`mt5api/chartctl/winoptions.py`, `winnavigator.py`), never keystrokes to whatever has focus, so terminals need no host-wide lock. It finds the terminal by its exact executable path (`terminal_pid.py`), never by window title, and refuses to run while a backtest holds `RUN_LOCK`. The API and the terminals both run elevated, which UIPI requires. A bare-metal fallback restart can still take minutes, so these routes keep the long nginx and unifier timeouts (ARC16).

## Binaries

- **WIN15:** The guest runs vendored executables (`assets/autoit/AutoIt3_x64.exe`, `scripts/defender-remover/PowerRun.exe`). Adding or replacing one follows SEC12.
