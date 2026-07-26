<#
Acquire a boot-scoped lock directory.

Exit 0 = lock acquired (caller may proceed).
Exit 1 = lock is held by a live instance (caller must bail).

WHY THIS EXISTS
---------------
The previous lock was a bare `mkdir "%SHARED%\start.running"` with no
staleness check. Three facts combined into a permanent outage:

  1. %SHARED% is the host-mounted volume (./data/shared), so the lock
     directory survives BOTH a VM reboot and a container restart.
  2. The MT5AutoReboot scheduled task fires `shutdown /r /t 0 /f` — zero
     grace period — and can land at any point inside start.bat's run.
     start.bat legitimately holds the lock for anywhere from seconds to
     over an hour (pip install, then up to 10 minutes per terminal
     waiting for "started for" to appear in the MT5 journal).
  3. Nothing ever cleared an orphaned lock.

So the reboot killed start.bat mid-run, the lock outlived it, and every
subsequent boot saw the orphan and exited immediately. Total outage with
no self-heal path — recovery required a human running run.sh.

Staleness is decided by OS boot time. A lock stamped with a different
boot than the current one CANNOT have a live owner, because the process
that took it died when that boot ended. That makes the common case
(killed by reboot) recover instantly on the very next boot.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$LockDir,

    # Fallback for a lock directory that exists but carries no boot stamp.
    # A live owner from THIS boot always stamps within milliseconds of the
    # mkdir, so an unstamped lock older than this is a corpse — either from
    # a pre-stamp version of this script, or killed in that tiny window.
    # Deliberately short: this is the recovery path, and a false positive
    # here can only clear a lock whose owner already died.
    [int]$StaleAfterMinutes = 30
)

$ErrorActionPreference = 'Stop'

Set-Variable -Name STAMP_FILE_NAME -Value 'boot.id' -Option Constant
Set-Variable -Name BOOT_ID_FORMAT -Value 'yyyyMMddHHmmss' -Option Constant

$stampPath = Join-Path $LockDir $STAMP_FILE_NAME

# A failed boot-time lookup must NOT hard-fail: bailing here would
# reintroduce exactly the deadlock this script exists to remove. Degrade to
# the TTL-only decision instead, which still self-heals (just slower).
$bootId = $null
try {
    $bootId = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().ToString($BOOT_ID_FORMAT)
} catch {
    Write-Output "WARN could not read boot time ($($_.Exception.Message)); falling back to age-only staleness"
}

function Get-LockAgeMinutes {
    param([string]$Path)

    $item = Get-Item -LiteralPath $Path
    # Take the NEWEST of the two timestamps. Creation time is unreliable
    # across the 9p/virtio-fs host mount, and over-estimating the age would
    # clear a possibly-live lock. Erring toward "not stale" is the safe
    # direction — worst case one boot cycle is skipped.
    $newest = $item.LastWriteTime
    if ($item.CreationTime -gt $newest) {
        $newest = $item.CreationTime
    }

    return ((Get-Date) - $newest).TotalMinutes
}

function Test-LockStale {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $stampPath)) {
        $ageMinutes = Get-LockAgeMinutes -Path $Path
        if ($ageMinutes -ge $StaleAfterMinutes) {
            return @{ Stale = $true; Reason = "no boot stamp and age $([int]$ageMinutes)m >= ${StaleAfterMinutes}m" }
        }

        return @{ Stale = $false; Reason = "no boot stamp but only $([int]$ageMinutes)m old — assuming live owner" }
    }

    # No stamp comparison possible without a boot id — fall back to age.
    if ($null -eq $bootId) {
        $ageMinutes = Get-LockAgeMinutes -Path $Path
        if ($ageMinutes -ge $StaleAfterMinutes) {
            return @{ Stale = $true; Reason = "boot id unavailable, age $([int]$ageMinutes)m >= ${StaleAfterMinutes}m" }
        }

        return @{ Stale = $false; Reason = "boot id unavailable, age $([int]$ageMinutes)m below TTL" }
    }

    $ownerBoot = (Get-Content -LiteralPath $stampPath -Raw).Trim()
    if ($ownerBoot -ne $bootId) {
        return @{ Stale = $true; Reason = "stamped boot $ownerBoot != current boot $bootId" }
    }

    return @{ Stale = $false; Reason = "stamped with current boot $bootId — owner is live" }
}

if (Test-Path -LiteralPath $LockDir) {
    $verdict = Test-LockStale -Path $LockDir

    if (-not $verdict.Stale) {
        Write-Output "HELD $($verdict.Reason)"
        exit 1
    }

    Write-Output "STALE clearing lock: $($verdict.Reason)"
    Remove-Item -LiteralPath $LockDir -Recurse -Force -ErrorAction SilentlyContinue
}

try {
    $null = New-Item -ItemType Directory -Path $LockDir -ErrorAction Stop
} catch {
    # Lost a same-boot race between the staleness check and the mkdir, or the
    # mount rejected the write. Either way the caller must not proceed.
    Write-Output "HELD could not create lock: $($_.Exception.Message)"
    exit 1
}

# Stamp IMMEDIATELY so the unstamped-lock window stays in the millisecond
# range. If this write fails the exception propagates and the caller sees a
# non-zero exit, so no instance ever proceeds holding an unstamped lock.
if ($null -ne $bootId) {
    Set-Content -LiteralPath $stampPath -Value $bootId -Encoding ASCII -NoNewline
}

Write-Output "ACQUIRED boot=$(if ($null -ne $bootId) { $bootId } else { 'unknown' })"
exit 0
