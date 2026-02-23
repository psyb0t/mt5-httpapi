# Boot Sequence Trace (fresh install)

## Pre-boot: run.sh (Linux host)

1. Creates dirs: `data/storage`, `data/shared/scripts`, `data/shared/config`, `data/shared/terminals`, `data/oem`
2. Checks `/dev/kvm` exists
3. Downloads `win.iso` if missing
4. Copies `scripts/oem-install.bat` → `data/oem/install.bat`
5. Copies `scripts/install.bat` → `data/shared/scripts/install.bat`
6. Copies `scripts/start.bat` → `data/shared/scripts/start.bat`
7. Copies `scripts/debloat.bat` → `data/shared/scripts/debloat.bat`
8. Copies `scripts/defender-remover/` → `data/shared/scripts/defender-remover/`
9. Copies `config/*` → `data/shared/config/`
10. Copies `mt5installers/mt5setup-*.exe` → `data/shared/terminals/`
11. Copies `mt5api/` → `data/shared/mt5api/`
12. Clears stale lock dirs: `install.running`, `start.running`
13. If `data/storage/data.img` missing (fresh VM) → clears all `*.done` flags
14. Generates `.env` with `API_PORT_RANGE` from `config/terminals.json`
15. Starts docker-compose (`dockurr/windows` image)
16. Waits for VM IP, sets up iptables port forwarding

---

## Boot 0: Windows OOBE (dockurr image)

- dockurr/windows runs its own setup, mounts `/oem` and runs `data/oem/install.bat`
- Our `oem-install.bat` creates a startup folder entry:
  - Path: `C:\ProgramData\Microsoft\Windows\Start Menu\Programs\StartUp\start.bat`
  - Content: `@echo off` + `call "C:\Users\Docker\Desktop\Shared\scripts\start.bat"`
- Windows finishes OOBE, reboots into desktop

**NOTE**: We use the startup folder here (not schtasks) because Task Scheduler
may not be running during OOBE. install.bat will later replace this with a
schtask and delete the startup entry.

---

## Boot 1: First logon (Docker user)

### start.bat runs (via startup folder entry)

1. Sets variables: SHARED, SCRIPTS, CONFIG, BROKERS (=terminals), LOGDIR, logs
2. Sets `PYDIR=C:\Program Files\Python312`, prepends to PATH
3. Creates `%SHARED%\start.running` lock dir (atomic mkdir)
   - If lock exists → "Another start.bat is already running" → exit 0
4. Logs `====== Boot ======` to both start.log and install.log
5. Logs "Running install.bat..." to start.log
6. **Calls install.bat**

### install.bat runs — ALL 9 steps sequentially

1. Creates `%SHARED%\install.running` lock
2. Logs header

3. **[1/9] Admin + UAC**
   - `net localgroup Administrators Docker /add` (ensure Docker is admin)
   - Sets ConsentPromptBehaviorAdmin=0, PromptOnSecureDesktop=0 (immediate)
   - Checks registry: EnableLUA != 0x0 → sets EnableLUA=0, NEEDS_REBOOT=1
   - **No flag file** — checks actual registry value each time

4. **[2/9] App Execution Aliases**
   - Deletes fake python registry entries + WindowsApps executables

5. **[3/9] Custom hosts + Windows Update**
   - Appends `config/hosts` to Windows hosts file (if marker missing)
   - `sc config wuauserv start= disabled` + `sc stop wuauserv`

6. **[4/9] Schtask + startup cleanup**
   - Creates MT5Start schtask if missing (onlogon, HIGHEST privilege)
   - **No reboot** — schtask works immediately
   - Deletes all startup folder entries (OEM + legacy)

7. **[5/9] Python**
   - Checks `if exist "%PYDIR%\python.exe"` (NOT `python --version`)
   - If missing: downloads python-3.12.7-amd64.exe, installs, verifies
   - **No reboot needed** — continues to next step

8. **[6/9] MetaTrader5 pip package**
   - `python -m pip install --upgrade pip`
   - `python -m pip install MetaTrader5`
   - Strict error checking on both

9. **[7/9] Debloat**
   - If `debloat.done` missing: runs debloat.bat, creates flag, NEEDS_REBOOT=1
   - If `debloat.done` exists: skip
   - Only flag file in the system (debloat is destructive, shouldn't re-run)

10. **[8/9] MT5 terminals**
    - Migrates old flat-layout dirs to `base\` if needed
    - For each `mt5setup-*.exe`: installs if `<broker>/base/terminal64.exe` missing
    - Runs EVERY boot — picks up new installers automatically
    - Verifies at least one terminal is installed, EXIT /B 1 if none
    - Each new install sets NEEDS_REBOOT=1

11. **[9/9] Firewall**
    - Deletes old rule, creates new with dynamic ports from terminals.json

12. **Reboot decision**
    - NEEDS_REBOOT=1 (UAC + debloat + MT5 all set it) → ONE reboot, exit /b 3
    - NEEDS_REBOOT=0 → "Setup complete!", exit /b 0

### Back in start.bat

7. errorlevel = 3 → "Reboot scheduled by install.bat, stopping."
8. Cleans start.running lock, exit /b 0

**VM reboots — ONE time.**

---

## Boot 2: Everything is installed — normal operation

**BOTH startup folder AND schtask may fire** (if startup entry wasn't deleted
on boot 1 due to the reboot). start.bat's mkdir lock ensures only one runs.

### start.bat → install.bat

- All 9 steps run but find everything already done:
  - [1/9] UAC: EnableLUA already 0x0 → skip
  - [2/9] Aliases: already deleted (idempotent)
  - [3/9] Hosts: marker already in file → skip
  - [4/9] Schtask: already exists → skip. Startup entries: already deleted (idempotent)
  - [5/9] Python: `python.exe` exists → skip
  - [6/9] pip: already installed (quick, idempotent)
  - [7/9] Debloat: `debloat.done` exists → skip
  - [8/9] MT5: `terminal64.exe` exists for each broker → skip
  - [9/9] Firewall: re-creates rule (idempotent)
- NEEDS_REBOOT=0 → "Setup complete!" → exit /b 0

### Back in start.bat

7. errorlevel = 0 → "install.bat done."
8. **Pip install**: `python -m pip install --quiet -r "%CONFIG%\requirements.txt"`
   → Installs packages from requirements.txt (flask, etc.)
   → Output goes to pip.log
9. **Kill lingering MT5 terminals**: taskkill terminal64.exe if running
10. **Verify terminals.json exists**: error + exit if missing
11. **Parse terminals.json**: python one-liner reads broker/account/port
    → Output goes to temp file `%TEMP%\mt5_terminals.txt`
    → Parse errors captured to `%TEMP%\mt5_parse_err.txt`
12. **Launch MT5 terminals**: for each entry:
    → Check `%BROKERS%\<broker>\base\terminal64.exe` exists (error + abort if not)
    → If `%BROKERS%\<broker>\<account>\terminal64.exe` missing → xcopy from base
    → Write `mt5start.ini` (login/server/password from accounts.json)
    → Start terminal64.exe with /portable /config flags
13. **Wait 10s** for terminals to initialize
14. **Launch API processes**: for each entry:
    → Background (`start "" cmd /c ...`) for all but last
    → Foreground (blocking) for last one
    → Each runs: `python -m mt5api --broker X --account Y --port Z`
    → Logs go to `api-<broker>-<account>.log`
    → Cleans lock before starting foreground API

**System is now running.** Foreground API keeps start.bat alive.
If the foreground API crashes, start.bat exits. Schtask will re-run on next logon.

---

## Summary of reboots on fresh install

1. Boot 0 → OEM creates startup entry
2. Boot 1 → install.bat does EVERYTHING (UAC + schtask + Python + debloat + MT5) → ONE reboot
3. Boot 2 → **operational**

Total: 1 reboot before operational (down from 4).

---

## Design decisions

### UAC: registry check, no flag file
UAC state is checked via `reg query ... /v EnableLUA` every boot. If already
0x0, skip. No flag file needed — survives VM reinstalls correctly because the
registry resets with the new Windows install.

### Schtask: no reboot needed
`schtasks /create` takes effect immediately. The schtask will fire on the NEXT
logon. No reboot needed just to create it.

### One reboot at the end
UAC (EnableLUA=0), debloat, and MT5 installs all need a reboot. Instead of
rebooting after each one, we set NEEDS_REBOOT=1 and defer to the very end.
One reboot applies all changes.

### debloat.done: the only flag file
Debloat is destructive and shouldn't re-run. Everything else checks actual
artifacts: python.exe exists, terminal64.exe exists, schtask exists, hosts
marker exists, EnableLUA registry value.

### run.sh clears done flags on fresh VM
If `data/storage/data.img` doesn't exist, `run.sh` clears all `*.done` flags.
This ensures debloat re-runs on a fresh Windows install even if the shared
mount was preserved.

### MT5 installer loop runs every boot
New `mt5setup-*.exe` files dropped into `terminals/` get installed automatically
on the next boot. Already-installed brokers (where `base/terminal64.exe` exists)
are skipped.

### Strict error handling
Every step checks errorlevel. Any failure → log error → clean lock → exit /b 1.
start.bat checks install.bat return code. Terminal launch failures abort startup.
