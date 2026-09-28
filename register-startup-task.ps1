<#
.SYNOPSIS
    One-time setup: makes the captions server start automatically when the
    mandir PC's user logs in.

.DESCRIPTION
    Registers a Windows Task Scheduler task that starts the captions server
    whenever the current Windows account logs in. Run this ONCE during
    machine setup, as the same Windows account that will run katha.

    This is the recommended boot behaviour for this tool - see WINDOWS.md,
    section "Boot behaviour", for the reasoning. It is optional: if you'd
    rather start the tool by hand each time, skip this script and just
    double-click start-captions.bat before each katha instead.

    The task runs the server with NO console window. That is deliberate.
    Two console control events on 3 September, 19:19 and 22:16, both
    STATUS_CONTROL_C_EXIT (#61); the owner confirmed he closed one of the
    windows himself, because it was empty and looked useless. A window
    that must not be closed, on a machine other people use, is not a
    safeguard. This does not make the interrupt survivable - that is #61.
    It removes the window people were closing. What replaces it:

      * the log file (logs/live-captions.log), which now outlives whatever
        started the server;
      * the operator page, which deploy.ps1 opens once the port answers;
      * "CAPTIONS OFFLINE" on the overlay if the server is not there.

    [UNVERIFIED on the real PC: Register-ScheduledTask requires no special
    privilege for a per-user logon trigger under the invoking account in
    the documented Windows behaviour, but this has not been run on the
    mandir machine. If it errors, running it from an elevated PowerShell
    (right-click -> Run as administrator) is the first thing to try.]

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File register-startup-task.ps1

.EXAMPLE
    # To undo:
    Unregister-ScheduledTask -TaskName "LiveCaptions" -Confirm:$false
#>

$ErrorActionPreference = "Stop"

$TaskName = "LiveCaptions"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# -ErrorAction does not save us here: if the ScheduledTasks module is absent
# the name fails to resolve as a command at all, and $ErrorActionPreference
# = "Stop" kills the script before it can say why. deploy.ps1 wraps the same
# call for the same reason.
try {
    $Existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
} catch [System.Management.Automation.CommandNotFoundException] {
    Write-Host "XX This Windows edition has no Get-ScheduledTask (the ScheduledTasks module)." -ForegroundColor Red
    Write-Host "   Autostart cannot be registered from here. Start the server by hand before"
    Write-Host "   each katha with start-captions.bat, and see WINDOWS.md."
    exit 1
}
if ($Existing) {
    Write-Host "!! A task named '$TaskName' already exists - removing it first so this run is clean." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# Not start-captions.bat. That opens a console window, and a console window
# is a thing someone closes — which is exactly how the evening of 3 September
# went (issue #57). PowerShell with -WindowStyle Hidden gives the same server
# with nothing to close; -NoProfile so a stray user profile script cannot
# break the katha start.
$Deploy = Join-Path $ScriptDir "deploy.ps1"
if (-not (Test-Path $Deploy)) {
    Write-Host "XX deploy.ps1 not found next to this script at $Deploy" -ForegroundColor Red
    exit 1
}
$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Deploy`" -StartOnly" `
    -WorkingDirectory $ScriptDir
# -AtLogOn with no -User is an ANY-user trigger, and registering one needs an
# elevated session — which is why this failed with "Access is denied" on the
# mandir PC. The task only ever needs to run for the account that runs katha,
# and scoping it to that account registers fine unelevated.
$CurrentUser = "$env:USERDOMAIN\$env:USERNAME"
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $CurrentUser
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero)  # no auto-kill after N hours - kathas run long

try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $Action `
        -Trigger $Trigger `
        -Settings $Settings `
        -User $CurrentUser `
        -Description "Starts the live captions server when this user logs into Windows, so it's already running before katha without anyone having to double-click start-captions.bat." | Out-Null
} catch {
    # The failure that actually happened on the mandir PC, and it was never
    # reported as a failure — setup was assumed done and the machine went to
    # katha one logon away from silence. Say what to do next, in one line.
    Write-Host ""
    Write-Host "XX Could not register the task: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ""
    Write-Host "   If that says 'Access is denied', re-run this from an ELEVATED PowerShell:" -ForegroundColor Yellow
    Write-Host "     right-click PowerShell -> Run as administrator, then"
    Write-Host "     powershell -ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Path)`""
    Write-Host ""
    Write-Host "   Until this succeeds the server does NOT start at logon." -ForegroundColor Yellow
    exit 1
}

# Read it back. "Register-ScheduledTask printed no error" is not the same
# claim as "the task is there", and on the mandir PC the difference was 98
# minutes of a silent hall. Whoever ran setup should leave this screen
# knowing which of the two happened.
$Registered = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $Registered) {
    Write-Host ""
    Write-Host "XX Register-ScheduledTask returned without an error, but no task named" -ForegroundColor Red
    Write-Host "   '$TaskName' can be read back. The server will NOT start at logon." -ForegroundColor Red
    Write-Host "   Try again from an elevated PowerShell (right-click -> Run as administrator)."
    exit 1
}

Write-Host ""
Write-Host "OK Task '$TaskName' registered and read back." -ForegroundColor Green
Write-Host "   State:   $($Registered.State)"
Write-Host "   Runs as: $CurrentUser, at logon, with no console window."
Write-Host "   Log:     $(Join-Path $ScriptDir 'logs\live-captions.log')"
Write-Host "            (or %LOCALAPPDATA%\live-captions\logs if that folder is not writable -"
Write-Host "             the server prints the path it actually used on its first line)"
Write-Host ""
Write-Host "   Check it any time with:  Get-ScheduledTask -TaskName `"$TaskName`""
Write-Host "   To remove it:            Unregister-ScheduledTask -TaskName `"$TaskName`" -Confirm:`$false"
