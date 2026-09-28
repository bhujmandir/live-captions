<#
.SYNOPSIS
    Live captions installer/starter for Windows.

.DESCRIPTION
    Windows counterpart to deploy.sh. Sets up the live caption server on a
    clean Windows PC and (optionally) starts it. Idempotent: safe to re-run;
    only installs what's missing.

    WRITTEN AND REASONED ON macOS, NOT EXECUTED ON WINDOWS. Every command
    below is believed correct from documented tool behaviour, not proven by
    running it. See WINDOWS.md, section "What is unverified", for the exact
    list of claims that still need confirming on the real mandir PC
    (tracked as issue #2, runs-on:mandir-pc). Treat any failure here as a
    normal bug report, not a surprise.

.PARAMETER NoStart
    Install only; don't start the server.

.PARAMETER StartOnly
    Skip the install phase and just start the server (this is what
    start-captions.bat calls day to day).

.PARAMETER Port
    Server port. Defaults to 8765.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy.ps1
    powershell -ExecutionPolicy Bypass -File deploy.ps1 -NoStart
    powershell -ExecutionPolicy Bypass -File deploy.ps1 -StartOnly
#>

param(
    [switch]$NoStart,
    [switch]$StartOnly,
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"

# ── Output helpers ───────────────────────────────────────────────────────────
function Info { param([string]$Msg) Write-Host "-> $Msg" -ForegroundColor Cyan }
function Ok   { param([string]$Msg) Write-Host "OK $Msg" -ForegroundColor Green }
function Warn { param([string]$Msg) Write-Host "!! $Msg" -ForegroundColor Yellow }
function Err  { param([string]$Msg) Write-Host "XX $Msg" -ForegroundColor Red }
function Hdr  { param([string]$Msg) Write-Host ""; Write-Host "== $Msg ==" -ForegroundColor White }

$CaptionsDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $CaptionsDir

Write-Host ""
Write-Host "Live captions (Windows)" -ForegroundColor White
Write-Host "Working directory: $CaptionsDir"
Write-Host "Server port:       $Port"

# Make sure a just-installed uv / node / pnpm are on PATH for the rest of
# this session even if the installer only updated the persistent (registry)
# PATH, which a running process does not pick up automatically.
$localBin = Join-Path $env:USERPROFILE ".local\bin"
if (Test-Path $localBin) { $env:Path = "$localBin;$env:Path" }

function Sync-PathFromRegistry {
    # A running process's PATH is a snapshot taken when it started. An MSI
    # installer (winget's Node package, for one) writes the new directory to
    # the registry, so this session never sees it and the very next
    # Get-Command fails. Re-reading both scopes is what makes a one-pass
    # install possible instead of "close PowerShell and run it again".
    $machine = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $user    = [System.Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = (@($machine, $user, $env:Path) | Where-Object { $_ }) -join ";"
    if (Test-Path $localBin) { $env:Path = "$localBin;$env:Path" }
}

$LogonTaskName = "LiveCaptions"
# Scaffolding left on the mandir PC on 3 September: a one-shot task with a
# start time in the past, so it never re-fires, currently the only thing
# holding the server up. It must be named here, or this script tells a
# machine whose captions are running that its autostart is off — and a
# banner that cries wolf is a banner nobody reads.
$AdhocTaskName = "LiveCaptionsAdhoc"

function Show-LogonTaskState {
    # Issue #57. On 3 September this machine went to katha with no logon task
    # registered, the server therefore never started, and the hall had no
    # captions for 98 minutes. Nothing anywhere said so. The task had been
    # "set up" — register-startup-task.ps1 had failed with Access is denied
    # and nobody read the error.
    #
    # So every run of this script now states, out loud, whether the machine
    # will start the server by itself. It is a WARNING and never fatal: a
    # katha with a hand-started server beats a script that refuses to run
    # twenty minutes before a katha.
    $task  = $null
    $adhoc = $null
    try {
        $task  = Get-ScheduledTask -TaskName $LogonTaskName -ErrorAction SilentlyContinue
        $adhoc = Get-ScheduledTask -TaskName $AdhocTaskName -ErrorAction SilentlyContinue
    } catch {
        # Get-ScheduledTask is absent on some Windows editions. Not knowing is
        # not the same as knowing it is missing, and saying "MISSING" here
        # would train people to ignore the banner that matters.
        Warn "Cannot check the logon task on this Windows edition ($($_.Exception.Message))."
        Write-Host "    Check by hand:  schtasks /query /tn $LogonTaskName"
        return
    }

    if ($task -and $task.State -ne "Disabled") {
        Ok "Autostart: task '$LogonTaskName' is registered ($($task.State)) - the server starts at logon."
        if ($adhoc) {
            Warn "'$AdhocTaskName' is still present. It is the 3 September scaffolding and is now"
            Write-Host "    redundant. Remove it:  schtasks /delete /tn $AdhocTaskName /f"
        }
        return
    }

    $why = if ($task) { "is registered but DISABLED" } else { "is NOT registered" }
    Write-Host ""
    Write-Host "  ############################################################" -ForegroundColor Red
    Write-Host "  ##  AUTOSTART IS OFF - THIS PC WILL GO TO KATHA SILENT     ##" -ForegroundColor Red
    Write-Host "  ############################################################" -ForegroundColor Red
    Write-Host "  The logon task '$LogonTaskName' $why." -ForegroundColor Red
    Write-Host "  Nobody logging in will start the captions server. On 3 September" -ForegroundColor Red
    Write-Host "  that cost the hall 98 minutes of no captions (issue #57)." -ForegroundColor Red
    Write-Host ""
    if ($adhoc) {
        Write-Host "  NOTE: '$AdhocTaskName' exists - the one-shot left behind on 3 September." -ForegroundColor Yellow
        Write-Host "  It has a start time in the past, so it will NOT re-fire. If the server is" -ForegroundColor Yellow
        Write-Host "  running right now, that task is why, and it will not survive a reboot." -ForegroundColor Yellow
        Write-Host ""
    }
    Write-Host "  Fix it now, in this window:" -ForegroundColor Yellow
    Write-Host "     powershell -ExecutionPolicy Bypass -File register-startup-task.ps1"
    Write-Host "  If that says 'Access is denied', re-run it from an elevated PowerShell." -ForegroundColor Yellow
    if ($adhoc) {
        Write-Host "  Then clear the scaffolding:  schtasks /delete /tn $AdhocTaskName /f" -ForegroundColor Yellow
    }
    Write-Host ""
}

function Assert-LastExitOk {
    # $ErrorActionPreference = "Stop" does NOT apply to native executables in
    # Windows PowerShell 5.1, so a failing uv/pnpm/npm prints its error and
    # the script sails on to print "OK". That is worse than crashing: it sends
    # someone to katha believing the install succeeded. Check explicitly.
    param([string]$What)
    if ($LASTEXITCODE -ne 0) {
        Err "$What failed with exit code $LASTEXITCODE. Stopping here rather than reporting success."
        exit 1
    }
}

# ──────────────────────────────────────────────────────────────────────────
# Install phase
# ──────────────────────────────────────────────────────────────────────────
if (-not $StartOnly) {

    Hdr "1. uv (Python package manager)"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Info "uv not found - installing (official astral.sh installer)..."
        # [UNVERIFIED on the real PC: this is uv's documented Windows install
        # command as of when this was written. Confirm it still resolves and
        # that PowerShell's execution policy allows `irm | iex` to run.]
        Invoke-Expression (Invoke-RestMethod -Uri "https://astral.sh/uv/install.ps1")
        if (Test-Path $localBin) { $env:Path = "$localBin;$env:Path" }
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
            Err "uv install completed but 'uv' is not on PATH in this session. Close and reopen PowerShell and re-run this script, or add $localBin to PATH manually."
            exit 1
        }
        Ok "uv installed ($(uv --version))."
    } else {
        Ok "uv already installed ($(uv --version))."
    }

    Hdr "2. PortAudio (native audio library)"
    Info "Nothing to install here on Windows: the 'sounddevice' package's Windows wheel bundles PortAudio, unlike the macOS wheel, which is why deploy.sh has to run 'brew install portaudio' and this script does not. 'uv sync' below pulls it in automatically."

    Hdr "3. Python dependencies"
    Info "Running uv sync (this also downloads a matching Python automatically if one isn't already on this machine - uv manages its own Python toolchains on Windows the same way it does on macOS)..."
    uv sync
    Assert-LastExitOk "uv sync"
    Ok "Python environment ready in .venv\"

    Hdr "4. .env (Sarvam API key)"
    if (-not (Test-Path ".env")) {
        if (Test-Path ".env.template") {
            Copy-Item ".env.template" ".env"
            Info ".env created from .env.template"
        } else {
            Warn ".env.template missing - creating a minimal .env"
            "SARVAM_API_KEY=`n" | Out-File -FilePath ".env" -Encoding utf8
        }
    }

    $envContent = Get-Content ".env" -Raw
    # `.` matches \r in .NET regex and .env has CRLF endings, so `.+` was
    # satisfied by the carriage return alone: an EMPTY key reported as
    # present, and nothing complained until captions were needed. Require a
    # character that is not a line ending.
    if ($envContent -notmatch '(?m)^SARVAM_API_KEY=[^\r\n]') {
        Warn "SARVAM_API_KEY is not set in .env"
        $apiKey = Read-Host "    Paste your Sarvam API key now (or press Enter to fill in manually later)"
        if ($apiKey) {
            $lines = Get-Content ".env" | Where-Object { $_ -notmatch '^SARVAM_API_KEY=' }
            $lines += "SARVAM_API_KEY=$apiKey"
            $lines | Set-Content ".env" -Encoding utf8
            Ok "SARVAM_API_KEY written to .env"
        } else {
            Warn "Skipped. Edit .env later and re-run. Captions won't work without a key."
        }
    } else {
        Ok "SARVAM_API_KEY present."
    }

    Hdr "5. Node.js (needed to build the web UI)"
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
        Info "Node.js not found - installing via winget (LTS)..."
        # [UNVERIFIED on the real PC: winget ships by default on Windows 10
        # 2004+ / Windows 11, but its presence and this exact package ID
        # have not been confirmed on the mandir machine. If winget is
        # missing or too old, install Node.js LTS manually from
        # https://nodejs.org/ and re-run this script.]
        try {
            winget install -e --id OpenJS.NodeJS.LTS --source winget --accept-package-agreements --accept-source-agreements
        } catch {
            Err "winget install failed or winget is unavailable. Install Node.js LTS manually from https://nodejs.org/ then re-run this script."
            exit 1
        }
        # The MSI writes C:\Program Files\nodejs to the machine PATH in the
        # registry, which this already-running process cannot see. Re-read it
        # rather than telling a person to close and reopen PowerShell.
        Sync-PathFromRegistry
        if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
            Err "Node.js installed, but 'node' is still not on PATH even after re-reading it from the registry. Close and reopen PowerShell and re-run this script."
            exit 1
        }
        Ok "Node.js installed and picked up without needing a new shell."
    }
    Ok "Node.js present ($(node --version))."

    Hdr "6. pnpm (web package manager)"
    if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
        Info "pnpm not found - installing via npm..."
        npm install -g pnpm
        Assert-LastExitOk "npm install -g pnpm"
        # npm's global bin lands in %APPDATA%\npm, which the Node MSI adds to
        # the USER PATH — same registry-snapshot problem as Node itself.
        Sync-PathFromRegistry
        if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
            Err "pnpm installed, but 'pnpm' is still not on PATH even after re-reading it from the registry. Close and reopen PowerShell and re-run this script."
            exit 1
        }
    }
    Ok "pnpm present ($(pnpm --version))."

    Hdr "7. Building the web UI"
    Push-Location "web"
    try {
        # --frozen-lockfile so the mandir PC installs what web\pnpm-lock.yaml
        # says and nothing else. A bare install silently resolves fresh when
        # package.json and the lockfile disagree, which means the hall screen
        # can end up running a dependency tree nobody has tested.
        Info "Running pnpm install --frozen-lockfile..."
        pnpm install --frozen-lockfile
        Assert-LastExitOk "pnpm install --frozen-lockfile"
        Info "Running pnpm build..."
        pnpm build
        Assert-LastExitOk "pnpm build"
        if (-not (Test-Path "dist\index.html")) {
            Err "pnpm build reported success but web\dist\index.html does not exist."
            exit 1
        }
        Ok "web\dist\ built."
    } finally {
        Pop-Location
    }

    Hdr "8. Microphone permission (manual - see WINDOWS.md diagnostic checklist)"
    Write-Host "    Windows normally prompts for microphone access the first time the"
    Write-Host "    server tries to read from the mic (Settings -> Privacy & security ->"
    Write-Host "    Microphone -> 'Let desktop apps access your microphone'). [UNVERIFIED:"
    Write-Host "    exact prompt wording/behaviour not confirmed on the mandir PC's"
    Write-Host "    Windows build - if no prompt appears, check that setting by hand.]"

    Hdr "9. Windows Firewall (manual, one-time - see WINDOWS.md diagnostic checklist)"
    Write-Host "    The first time the server starts, Windows Firewall will likely prompt"
    Write-Host "    to allow python.exe network access (needed for: other devices on the"
    Write-Host "    LAN reaching http://<this-pc>:$Port/, and for the mDNS LAN-discovery"
    Write-Host "    broadcast used by caption sidecars). Allow it on Private networks."

    Hdr "10. Autostart at logon"
    Show-LogonTaskState

}  # install phase

# ──────────────────────────────────────────────────────────────────────────
# Run phase
# ──────────────────────────────────────────────────────────────────────────
if ($NoStart) {
    Write-Host ""
    Ok "Install phase complete. Skipping server start (-NoStart)."
    Write-Host "    Start later with:  powershell -ExecutionPolicy Bypass -File deploy.ps1 -StartOnly"
    exit 0
}

# Only when the install phase did not already print it. The autostart banner
# is the loudest thing this script says; printing it twice in one run is how
# a signal gets learned as noise.
if ($StartOnly) {
    Hdr "Autostart check"
    Show-LogonTaskState
}

Hdr "Port check"
$inUse = $false
try {
    $inUse = [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
} catch {
    # Get-NetTCPConnection isn't present on every Windows edition; fall back
    # to netstat, which is universally available.
    $inUse = (netstat -ano | Select-String ":$Port\s.*LISTENING") -ne $null
}
if ($inUse) {
    # Almost always this is OUR OWN server, already started by the logon task
    # — which is the normal daily path, not a fault. Telling the operator it
    # is broken and handing them a command to type, at the moment everything
    # is working, is the worst thing this script can do. Ask the port who it
    # is before assuming the worst.
    $ours = $false
    try {
        $resp = Invoke-WebRequest -Uri "http://localhost:$Port/api/config" `
                                  -UseBasicParsing -TimeoutSec 3
        $ours = ($resp.StatusCode -eq 200)
    } catch { $ours = $false }

    if ($ours) {
        Ok "The captions server is already running on port $Port - opening it."
        Start-Process "http://localhost:$Port/"
        exit 0
    }

    Err "Port $Port is held by something that is not the captions server. Either stop it, or:"
    Write-Host "        powershell -ExecutionPolicy Bypass -File deploy.ps1 -StartOnly -Port 8766"
    exit 1
}
Ok "Port $Port is free."

Hdr "Starting captions server"
Write-Host ""
Write-Host "    Operator URL:    http://localhost:$Port/"
Write-Host "    Overlay URL:     http://localhost:$Port/?overlay=1"
Write-Host "    Debug panel:     append  ?debug=1  to the operator URL"
Write-Host "    Log file:        $(Join-Path $CaptionsDir 'logs\live-captions.log')"
Write-Host "                     (if that folder is not writable the server falls back to"
Write-Host "                      %LOCALAPPDATA%\live-captions\logs, then TEMP - it prints the"
Write-Host "                      path it actually used on its first line)"
Write-Host ""
Write-Host "    Stop with Ctrl+C. Re-start with:"
Write-Host "        powershell -ExecutionPolicy Bypass -File deploy.ps1 -StartOnly"
Write-Host ""

# Open the browser only once the port actually answers. Launching it on the
# line above the server guaranteed a connection error and a manual refresh.
# The poll runs in a background job so the server keeps the foreground and
# Ctrl+C still stops it.
Start-Job -ScriptBlock {
    param($p)
    for ($i = 0; $i -lt 60; $i++) {
        try {
            Invoke-WebRequest -Uri "http://localhost:$p/api/config" `
                              -UseBasicParsing -TimeoutSec 2 | Out-Null
            Start-Process "http://localhost:$p/"
            return
        } catch { Start-Sleep -Milliseconds 500 }
    }
} -ArgumentList $Port | Out-Null

uv run python live_captions.py --port $Port
