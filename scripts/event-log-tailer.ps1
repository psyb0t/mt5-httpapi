# Tail Windows System + Application event logs (Warning/Error/Critical) to
# the shared logs dir so OOM kills, process crashes, BSODs, etc. show up
# alongside the API logs and survive a VM reboot.
#
# Single-instance: if another copy is already running, exit. start.bat
# launches us at every boot, so stale tailers from a previous boot stay
# orphaned only until the next start.bat run, at which point this guard
# stops a second copy from piling on.

$ErrorActionPreference = 'Continue'
$shared = 'C:\Users\Docker\Desktop\Shared'
$logDir = Join-Path $shared 'logs'
$logFile = Join-Path $logDir 'windows-events.log'
$fullLog = Join-Path $logDir 'full.log'
$lockFile = Join-Path $logDir 'event-log-tailer.lock'

if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

# Single-instance check — if lock exists and that PID is alive, bail.
if (Test-Path $lockFile) {
    $oldPid = Get-Content $lockFile -ErrorAction SilentlyContinue
    if ($oldPid) {
        $proc = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
        if ($proc -and $proc.ProcessName -eq 'powershell') {
            exit 0
        }
    }
}
Set-Content -Path $lockFile -Value $PID -Force

# Catch up on the last 5 minutes so we don't miss events that happened
# between previous-boot crash and this startup. Subsequent passes use
# wall-clock since the previous poll.
$since = (Get-Date).AddMinutes(-5)

# full.log is written concurrently by start.bat, install.bat, and
# api_runner.bat via cmd's `>>`. Wrap our appends in try/silent-continue
# so a transient lock contention from one of those writers doesn't kill
# the tailer loop — the canonical copy is in windows-events.log anyway.
function Append-Full($line) {
    try { Add-Content -Path $fullLog -Value $line -ErrorAction Stop } catch {}
}

$startMsg = "[" + (Get-Date).ToString('s') + "] [tailer] starting (pid=$PID, since=$since)"
Add-Content -Path $logFile -Value $startMsg
Append-Full ("[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] [winevt] tailer starting (pid=$PID)")

while ($true) {
    $now = Get-Date
    try {
        Get-WinEvent -FilterHashtable @{
            LogName   = 'System', 'Application'
            StartTime = $since
            Level     = 1, 2, 3   # Critical, Error, Warning
        } -ErrorAction SilentlyContinue |
            Sort-Object TimeCreated |
            ForEach-Object {
                $ts = $_.TimeCreated.ToString('s')
                $lvl = $_.LevelDisplayName
                $log = $_.LogName
                $prov = $_.ProviderName
                $id = $_.Id
                # Collapse multi-line messages onto one line for grep-ability.
                $msg = ($_.Message -replace '\r?\n', ' ' -replace '\s+', ' ').Trim()
                $detailed = "[$ts] [$log/$lvl] [$prov] $id $msg"
                $condensed = "[$ts] [winevt] [$log/$lvl] [$prov] $id $msg"
                Add-Content -Path $logFile -Value $detailed
                Append-Full $condensed
            }
    } catch {
        $errMsg = "[" + (Get-Date).ToString('s') + "] [tailer] poll error: $_"
        Add-Content -Path $logFile -Value $errMsg
        Append-Full ("[" + (Get-Date).ToString('s') + "] [winevt] tailer poll error: $_")
    }
    $since = $now
    Start-Sleep -Seconds 15
}
