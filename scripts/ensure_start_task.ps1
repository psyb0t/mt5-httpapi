<#
Make sure the MT5Start scheduled task runs start.bat at every logon.

EXIT CODES:
  0 = the task is correct (already was, or was just re-registered).
  * = the task is missing or wrong and could not be fixed.

ASCII ONLY, like every .ps1 here: Windows PowerShell 5.1 reads a .ps1 without
a BOM as ANSI, and a multi-byte character breaks the parse (see
acquire_lock.ps1). `make lint` enforces it.

WHY THIS SCRIPT EXISTS
install.bat used to create the task with
  schtasks /create /sc onlogon /ru Docker /rl HIGHEST
which leaves three defaults in the task, and each one can stop the stack:

  1. The logon trigger gets a StartBoundary of "now", in the guest's local
     time. A logon trigger never fires before its StartBoundary. The guest
     clock can be hours fast during the first install and correct itself
     later; from then on every reboot logs on, the trigger is still "in the
     future", start.bat never runs, the VM stays unhealthy, and vm-watchdog
     recreates it into the same state. Observed: a boundary 8 hours ahead
     kept every boot dead until that hour came.
  2. DisallowStartIfOnBatteries and StopIfGoingOnBatteries are true.
  3. ExecutionTimeLimit is 72 hours. start.bat never returns (it loops on
     the health check), so Task Scheduler would kill it after three days.

This script registers the task without a StartBoundary, with both battery
settings off and with no time limit. It only re-registers when the current
definition is wrong, because start.bat itself runs from this task and calls
this script on every boot.
#>
param(
    [Parameter(Mandatory = $true)][string]$StartBat
)

$ErrorActionPreference = 'Stop'
$TaskName = 'MT5Start'
$UserName = 'Docker'

function Get-TaskProblem {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        return 'missing'
    }
    $xml = [xml](Export-ScheduledTask -TaskName $TaskName)
    # Look the elements up by name: PowerShell's XML adapter turns an empty
    # <LogonTrigger /> into an empty string, which reads as missing.
    $logon = @($xml.Task.Triggers.GetElementsByTagName('LogonTrigger'))
    if ($logon.Count -eq 0) {
        return 'no logon trigger'
    }
    $boundary = @($logon[0].GetElementsByTagName('StartBoundary'))
    if ($boundary.Count -gt 0) {
        return 'logon trigger has StartBoundary ' + $boundary[0].InnerText
    }
    $settings = $xml.Task.Settings
    if ($settings.DisallowStartIfOnBatteries -ne 'false') {
        return 'refuses to start on batteries'
    }
    if ($settings.StopIfGoingOnBatteries -ne 'false') {
        return 'stops on batteries'
    }
    if ($settings.ExecutionTimeLimit -ne 'PT0S') {
        return 'time limit ' + $settings.ExecutionTimeLimit
    }
    $exec = $xml.Task.Actions.Exec
    if ($exec.Arguments -ne ('/c "{0}"' -f $StartBat)) {
        return 'runs ' + $exec.Arguments
    }
    return $null
}

$problem = Get-TaskProblem
if (-not $problem) {
    Write-Output "$TaskName ok"
    exit 0
}

# New-ScheduledTaskTrigger -AtLogOn sets no StartBoundary; only the time-based
# triggers (-At) take one. No -User: the task fires at any logon, as before.
$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument ('/c "{0}"' -f $StartBat)
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $UserName -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Force | Out-Null

$after = Get-TaskProblem
if ($after) {
    Write-Output "$TaskName still wrong after re-registering ($problem): $after"
    exit 1
}
Write-Output "$TaskName re-registered ($problem)"
exit 0
