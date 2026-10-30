<#
.SYNOPSIS
    One-time setup: makes the captions server AND the overlay-button gate
    start by themselves when the mandir PC's user logs in, and start again
    if either stops.

.DESCRIPTION
    Registers two Windows Task Scheduler tasks for the current Windows
    account. Run this ONCE during machine setup, as the same Windows account
    that will run katha, from the checkout:

      LiveCaptions             the captions server (deploy.ps1 -StartOnly)
      LiveCaptionsOverlayGate  the gate that follows vMix's overlay 2 button
                               (tools\vmix\overlay_gate.py, settings in
                               overlay-gate.json)

    Both tasks start at logon with NO window, have no time limit, and restart
    on failure every minute, up to 999 times (Task Scheduler's ceiling, in
    practice unlimited). That setting is only the backup: it is widely
    reported to fire when a task fails to launch, not when the program exits
    non-zero. The server task therefore passes -Supervise, so deploy.ps1
    itself restarts a crashed server after 10 s (logs\supervisor-<date>.log).
    The script reads both back and exits 1 if either is missing or does not
    restart.

    Safe to re-run, but a task of the same name is STOPPED and replaced, so a
    running server or gate stops (the server does not come back until the
    next logon, or run with -StartNow). Do not re-run it during a katha. This
    also swaps in the hand-registered tasks of the October katha (the server
    task that ran start-captions.bat in a console, and a gate run from a
    folder outside the checkout).

    Neither task redirects output to a file. The server writes
    logs\live-captions.log and the gate writes logs\overlay-gate.log, each
    rolled at midnight with two weeks kept.

    The server task runs headless: it passes -NoBrowser, so it never opens the
    operator page (a restart must not put a tab over vMix mid-katha). The desk
    opens http://localhost:8765/ by hand when it wants the operator page.

    Why no window: two console control events on 3 September, and the
    server ending when its console was closed on 23 October. A window that
    must not be closed, on a machine other people use, is not a safeguard.
    Why restart: a crash must not end the captions for the rest of the katha.
    A deliberate stop (stop-captions.bat) exits 0 and is NOT restarted. The
    gate catches every error in its loop, so it needs no supervisor of its own;
    Task Scheduler relaunches it if the process itself ever ends.

    [UNVERIFIED on a real PC: written and parse-checked on macOS, never run
    on Windows. The dry run in WINDOWS.md is the test. If registration says
    'Access is denied', run it from an elevated PowerShell (right-click ->
    Run as administrator).]

.PARAMETER Remove
    Stop and delete both tasks, then read back that they are gone.

.PARAMETER StartNow
    Also start both tasks after registering, instead of waiting for the next
    logon. Starting the server task while a server is already running on the
    port just opens the operator page.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File register-startup-task.ps1

.EXAMPLE
    # To remove both tasks:
    powershell -ExecutionPolicy Bypass -File register-startup-task.ps1 -Remove
#>

param(
    [switch]$Remove,
    [switch]$StartNow
)

$ErrorActionPreference = "Stop"

$ServerTaskName = "LiveCaptions"
$GateTaskName   = "LiveCaptionsOverlayGate"
$RestartCount   = 999

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$ScriptPath = $MyInvocation.MyCommand.Path

# -ErrorAction does not save us here: if the ScheduledTasks module is absent
# the name fails to resolve as a command at all, and $ErrorActionPreference
# = "Stop" kills the script before it can say why. deploy.ps1 wraps the same
# call for the same reason.
try {
    Get-ScheduledTask -TaskName $ServerTaskName -ErrorAction SilentlyContinue | Out-Null
} catch [System.Management.Automation.CommandNotFoundException] {
    Write-Host "XX This Windows edition has no Get-ScheduledTask (the ScheduledTasks module)." -ForegroundColor Red
    Write-Host "   Autostart cannot be registered from here. Start the server by hand before"
    Write-Host "   each katha with start-captions.bat, and see WINDOWS.md."
    exit 1
}

function Stop-TaskIfPresent {
    # Stop first. Replacing or unregistering a task does not end the process
    # it started, and a second gate beside the old one would start every
    # capture twice. Returns whether there was a task of that name.
    param([string]$Name)
    $existing = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "!! A task named '$Name' already exists - stopping it and replacing it." -ForegroundColor Yellow
        Stop-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    }
    return [bool]$existing
}

if ($Remove) {
    foreach ($name in @($ServerTaskName, $GateTaskName)) {
        if (Stop-TaskIfPresent $name) {
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
        }
    }
    $left = @($ServerTaskName, $GateTaskName) | Where-Object {
        Get-ScheduledTask -TaskName $_ -ErrorAction SilentlyContinue
    }
    if ($left) {
        Write-Host "XX Still registered: $($left -join ', '). Try again from an elevated PowerShell." -ForegroundColor Red
        exit 1
    }
    Write-Host "OK Removed '$ServerTaskName' and '$GateTaskName'. Nothing starts at logon any more." -ForegroundColor Green
    exit 0
}

# Everything the tasks run must exist BEFORE an existing task is touched: a
# broken checkout must not replace a working task with one that cannot start.
$Deploy      = Join-Path $ScriptDir "deploy.ps1"
$GatePython  = Join-Path $ScriptDir ".venv\Scripts\pythonw.exe"
$GateScript  = Join-Path $ScriptDir "tools\vmix\overlay_gate.py"
$Missing = @($Deploy, $GatePython, $GateScript) | Where-Object { -not (Test-Path $_) }
if ($Missing) {
    foreach ($m in $Missing) { Write-Host "XX Not found: $m" -ForegroundColor Red }
    Write-Host "   Run deploy.ps1 first (it creates .venv with uv sync), then this script, from the checkout." -ForegroundColor Yellow
    exit 1
}

# Not start-captions.bat: that opens a console window, and a console window
# is a thing someone closes. -NoProfile so a stray user profile script cannot
# break the katha start. The gate runs under pythonw.exe, which has no console
# at all; its own log is logs\overlay-gate.log.
$Tasks = @(
    @{
        Name        = $ServerTaskName
        Execute     = "powershell.exe"
        Argument    = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Deploy`" -StartOnly -NoBrowser -Supervise"
        Description = "Starts the live captions server when this user logs into Windows, and again if it stops."
    },
    @{
        Name        = $GateTaskName
        Execute     = $GatePython
        Argument    = "`"$GateScript`""
        Description = "Follows vMix's overlay 2 button: on starts the live captions, off stops them. Restarts if it stops."
    }
)

# -AtLogOn with no -User is an ANY-user trigger, and registering one needs an
# elevated session - which is why this failed with "Access is denied" on the
# mandir PC. Both tasks only ever need to run for the account that runs katha,
# and scoping them to that account registers fine unelevated.
$CurrentUser = "$env:USERDOMAIN\$env:USERNAME"
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $CurrentUser
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount $RestartCount `
    -RestartInterval (New-TimeSpan -Minutes 1)

$Problems = @()
$Failed = @()
foreach ($t in $Tasks) {
    try {
        # -Force replaces a task of the same name in one step, so a failed
        # registration leaves the old definition rather than nothing.
        Stop-TaskIfPresent $t.Name | Out-Null
        $Action = New-ScheduledTaskAction `
            -Execute $t.Execute `
            -Argument $t.Argument `
            -WorkingDirectory $ScriptDir
        Register-ScheduledTask `
            -TaskName $t.Name `
            -Action $Action `
            -Trigger $Trigger `
            -Settings $Settings `
            -User $CurrentUser `
            -Description $t.Description `
            -Force | Out-Null
    } catch {
        # The failure that actually happened on the mandir PC, and it was never
        # reported as a failure - setup was assumed done and the machine went to
        # katha one logon away from silence. Say what to do next, in one line.
        $Failed += $t.Name
        $Problems += "'$($t.Name)' could not be registered."
        Write-Host ""
        Write-Host "XX Could not register '$($t.Name)': $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "   If that says 'Access is denied', re-run this from an ELEVATED PowerShell:" -ForegroundColor Yellow
        Write-Host "     right-click PowerShell -> Run as administrator, then"
        Write-Host "     powershell -ExecutionPolicy Bypass -File `"$ScriptPath`""
    }
}

# Read both back. "Register-ScheduledTask printed no error" is not the same
# claim as "the task is there", and on the mandir PC the difference was 98
# minutes of a silent hall. Whoever ran setup should leave this screen
# knowing which of the two happened, for each task.
foreach ($t in $Tasks) {
    if ($Failed -contains $t.Name) { continue }
    $got = Get-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue
    if (-not $got) {
        $Problems += "'$($t.Name)' can not be read back: it will NOT start at logon."
    } elseif ($got.Settings.RestartCount -ne $RestartCount) {
        $Problems += "'$($t.Name)' is registered but restarts $($got.Settings.RestartCount) times, not $RestartCount: a crash would end it."
    } elseif ($got.Actions[0].Execute -ne $t.Execute -or $got.Actions[0].Arguments -ne $t.Argument) {
        $Problems += "'$($t.Name)' is registered but runs '$($got.Actions[0].Execute) $($got.Actions[0].Arguments)', not '$($t.Execute) $($t.Argument)'."
    } else {
        Write-Host "OK Task '$($t.Name)' registered and read back (state $($got.State), restarts up to $($got.Settings.RestartCount) times, every minute)." -ForegroundColor Green
    }
}
if ($Problems.Count -gt 0) {
    Write-Host ""
    foreach ($p in $Problems) { Write-Host "XX $p" -ForegroundColor Red }
    Write-Host "XX Setup is NOT complete. Until this succeeds the captions do not run by themselves." -ForegroundColor Red
    exit 1
}

if ($StartNow) {
    foreach ($t in $Tasks) { Start-ScheduledTask -TaskName $t.Name }
    Write-Host "OK Both tasks started."
}

Write-Host ""
Write-Host "   Runs as: $CurrentUser, at logon, with no console window."
Write-Host "   Logs:    $(Join-Path $ScriptDir 'logs\live-captions.log')"
Write-Host "            $(Join-Path $ScriptDir 'logs\overlay-gate.log')"
Write-Host "            (the server falls back to %LOCALAPPDATA%\live-captions\logs if that"
Write-Host "             folder is not writable - it prints the path it actually used)"
Write-Host "   Gate settings: $(Join-Path $ScriptDir 'overlay-gate.json') (see overlay-gate.example.json)"
Write-Host ""
Write-Host "   Check them any time with:  Get-ScheduledTask -TaskName `"$ServerTaskName`",`"$GateTaskName`""
Write-Host "   Start them now:            Start-ScheduledTask -TaskName `"$ServerTaskName`"; Start-ScheduledTask -TaskName `"$GateTaskName`""
Write-Host "   To remove both:            powershell -ExecutionPolicy Bypass -File register-startup-task.ps1 -Remove"
