# Windows install & start (mandir PC)

This is the Windows counterpart to the Quick start in [README.md](README.md#quick-start),
which uses `deploy.sh` for macOS. **`deploy.sh` and the macOS path are unchanged** — this is a
parallel path, not a replacement. Nothing in `live_captions.py` is platform-specific: every
dependency in `pyproject.toml` is cross-platform, and the Windows build of the `sounddevice`
package bundles PortAudio, which is exactly the native library `deploy.sh` has to install by
hand via Homebrew on macOS. That's why the steps below have no equivalent of `brew install
portaudio` — there's nothing to install.

> ✅ **Run on the real mandir PC (Windows 11 Pro 26200, PowerShell 5.1) and working there**: it
> installs, captures from the mixer's `Line In` while vMix holds the same input, and returns
> English captions from spoken Gujarati about 2 seconds later. In katha week (5–11 Oct 2026) it
> captioned the mornings and evenings, with someone at the PC starting and mending it by hand (the
> October section of [CHANGELOG.md](CHANGELOG.md) lists what that took). The first run on real Windows found seven defects (an
> empty API key reported as present, a build failure reported as success, PATH not visible to the
> installer's own session, a missing directory that stopped the server booting, a
> scheduled-task registration that needed elevation, a browser opened before the server
> existed, and a running server reported as a port conflict). All are fixed. That record is
> the reason to trust what follows and the reason to distrust any addition to it written the
> same way.
>
> ⚠️ **Not yet run on Windows: the October update.** Two logon tasks (the server and the
> overlay button gate), their restart on failure, the gate's re-navigate of a blank overlay and
> the device-by-name start were written and tested on macOS against fakes. They have never run
> on a Windows PC or against a real vMix. Every claim about them below is marked
> `[UNVERIFIED]` until the **dry run** below has been done on a clean PC with
> nobody at the PC to fix anything by hand. Do that dry run before the first katha.

## The short version

For a volunteer setting up a PC for the first time. Each step is explained further down.

1. **Install:** `powershell -ExecutionPolicy Bypass -File deploy.ps1` (one-time setup, below).
   Put your keys in `.env` when it asks. It leaves a server running in that window: answer
   the firewall prompt, then stop it with `stop-captions.bat`.
2. **Register the two background jobs:**
   `powershell -ExecutionPolicy Bypass -File register-startup-task.ps1 -StartNow`. From then on
   the captions server and the overlay button gate start at every logon, with no window, and
   start again if they stop.
3. **Set up vMix once** ("Setting up vMix", below): a browser input titled `Captions Overlay`,
   on overlay 2, and **save the preset**.
4. **Check it** with the dry run (below) before the first katha, and put
   [`DESK-CHECKLIST.md`](DESK-CHECKLIST.md) next to the vMix PC.

After that a katha needs nobody at the PC: the desk presses overlay 2 and the subtitles run.

## One-time setup (clean machine to a running server)

1. Copy this whole `live-captions/` folder onto the Windows PC (USB drive, network share, or
   `git clone` if the PC has git).
2. Open PowerShell — Start menu → type `PowerShell` → Enter. Admin rights are not required for
   any of the steps below.
3. Run:

   ```powershell
   cd path\to\live-captions
   powershell -ExecutionPolicy Bypass -File deploy.ps1
   ```

   `deploy.ps1` is the Windows counterpart to `deploy.sh`. It is idempotent — safe to re-run —
   and installs, in order:

   | Step | What | Why |
   |---|---|---|
   | 1 | `uv` (Python package manager) | via the official `astral.sh` Windows installer |
   | 2 | *(nothing)* | PortAudio ships inside the Windows `sounddevice` wheel — no manual native-library install, unlike macOS |
   | 3 | Python dependencies | `uv sync` — also downloads a matching Python automatically if none is present; uv manages its own Python toolchains on Windows the same way it does on macOS |
   | 4 | `.env` | copied from `.env.template`; prompts once for `SARVAM_API_KEY` |
   | 5 | Node.js LTS | via `winget`, only if not already present |
   | 6 | `pnpm` | via `npm install -g pnpm`, only if not already present |
   | 7 | Web bundle | `pnpm install --frozen-lockfile && pnpm build` inside `web\`, producing `web\dist\` |

   Then it starts the server and opens `http://localhost:8765/` in the default browser. It
   ends by checking the two logon tasks of step 4 and says in red that they are missing: on a
   new PC that is expected until you have done step 4.

4. Stop the server `deploy.ps1` left running in its window (double-click `stop-captions.bat`;
   Ctrl+C is refused by design). Then register the two background jobs from a new
   PowerShell (see "Boot behaviour" below for what this does and how to undo it). Do not
   register while that server is still up: the task would find the port taken, call that server
   its own, and leave it unsupervised and dying with its window.

   ```powershell
   powershell -ExecutionPolicy Bypass -File register-startup-task.ps1 -StartNow
   ```

   It must end with two green `OK Task ... registered and read back` lines. If it ends in red,
   the captions will not run by themselves: read what it says and fix that first.

5. Set up vMix once (see "Setting up vMix" below), and name the hall's audio input in
   `overlay-gate.json` as `device_name` ("The overlay gate and its settings", below). Until
   you do, the gate starts the captions on the server's default input.

6. Two things still need a human at the keyboard the first time — see "Manual, one-time steps"
   below. Everything else in the sequence is unattended.

Re-run any time with `-NoStart` to install/update without starting, or `-StartOnly` to skip
straight to starting (this is what the daily entry point uses — see below).

## Entry point — starting it without typing commands

**Double-click `start-captions.bat`.** That is the whole interaction for whoever is opening
katha — on a machine where the logon task has not been registered. Where it has (the
recommendation below), the server is already up before anyone sits down and this is only the
manual fallback. It runs `deploy.ps1 -StartOnly` (assumes the one-time setup above has already happened;
it does not install or update anything) and opens the operator page in the default browser. No
PowerShell window needs to be typed into — the operator only ever sees `http://localhost:8765/`
in a browser, exactly as on macOS.

`.bat` rather than `.ps1` as the entry point deliberately: PowerShell scripts don't run on
double-click by default (Windows opens them in a text editor instead, and even when run,
default execution policy blocks unsigned scripts). The `.bat` sidesteps both by invoking
PowerShell with `-ExecutionPolicy Bypass` itself, so a plain double-click just works.

A server started this way is **not** the logon task's: it lives in the window this opens, so
closing that window ends it, and nothing restarts it. Use it only when the task's server is not
running (see the desk checklist), and leave its window alone.

## Stopping it — and why Ctrl+C is not the way

**To stop the server: double-click `stop-captions.bat`.**

It asks the server to shut down over `POST /api/shutdown` on localhost, which ends it through
the same teardown a clean exit uses — the mDNS advert is withdrawn, any reprocessing job
drains, and the log records a clean stop instead of a hole. Pass a port if you are not on the
default: `stop-captions.bat 9000`.

**A server stopped this way stays stopped.** `stop-captions.bat` ends the server with a clean
exit, which the logon task does not treat as a crash, so it does not bring the server back.
That is the point: a stop you asked for is not undone. To start it again, run
`Start-ScheduledTask -TaskName LiveCaptions` in PowerShell, or sign out of Windows and in
again, or (the manual fallback) double-click `start-captions.bat`. A server that *crashes*, or
is killed, is started again by the task within a minute. [UNVERIFIED until the dry run.]

### Why there is a script for this at all — and what actually happened

On the evening of 3 September the captions server died **twice**, 19:19 and 22:16, and for a
fortnight the recorded cause was wrong.

**What the evidence showed:** Task Scheduler recorded `3221225786` (`0xC000013A`,
`STATUS_CONTROL_C_EXIT`) and the log's last bytes were literally `^C`, straight after healthy
lines. From that, the diagnosis was "somebody pressed Ctrl+C, probably to copy text out of the
window".

**What actually happened:** a terminal window popped up, blank, and nobody at the desk knew
what it was for, so it was closed with its X button, on purpose.

So it was **the window being closed**, deliberately, by someone who had no way of knowing what
it was. Not a stray keystroke. Windows delivers `CTRL_CLOSE_EVENT` for that, and Python
surfaces it down the same path, which is why the log looked like Ctrl+C.

**That reframes the fix.** The fault was never a keystroke — it was *an unlabelled console
window in a control room*, which is an invitation to close it. Two things follow, and the
second matters far more than the first:

1. **The window now says what it is.** `start-captions.bat` sets a title —
   `*** LIVE CAPTIONS RUNNING - DO NOT CLOSE THIS WINDOW ***` — and prints a banner naming
   what closing it costs and how to stop properly. Nobody should be in that position again.
2. **Better: do not have a window.** `register-startup-task.ps1` runs the server with **no
   console at all**: run it headless, so no one can close it.
   A window that does not exist cannot be closed by someone trying to tidy up. This is the
   recommended way to run on this machine, and it stays `[UNVERIFIED]` until the dry run below
   has been done on a PC that has actually been logged out and in again.

### Ctrl+C, separately

A stray Ctrl+C is also now refused, and that behaviour ships. It is **not** the fix for the
outage above and should not be read as one:

- **A single Ctrl+C is refused** and logged at ERROR level, so the attempt leaves a trace
  instead of a silence. The captions stay up.
- **Three Ctrl+C presses within two seconds still exit.** A server nobody can stop is its own
  outage, so a developer at a terminal keeps a way out — it just cannot happen by accident.
- Set `CAPTION_INTERRUPT_PRESSES=0` in `.env` to refuse **every** interrupt. On a control-room
  machine that is the right setting: `stop-captions.bat` remains the way to stop it.

### ⚠️ Not yet proven on this machine

The server refuses the interrupt, and that is tested with real signals — **on macOS and Linux**.
Windows is different in a way that may defeat it, and that part is still unproven.

A Windows console control event is delivered to **every process attached to the console**, and
`start-captions.bat` builds a chain: the batch file, then `powershell -File deploy.ps1
-StartOnly`, then Python. Python refusing the event does not stop `cmd.exe` asking *"Terminate
batch job (Y/N)?"*, and does not stop PowerShell exiting. If that chain tears the console down,
Python dies by `CTRL_CLOSE_EVENT` regardless.

There is supporting evidence that the layer which died was never Python: the recorded exit code
was `3221225786` on the **task** process, and Python's pre-existing `except KeyboardInterrupt`
would have exited **0**.

**So: do not treat Ctrl+C as safe on this machine yet.** Use `stop-captions.bat`. Someone with
this PC needs to raise a console control event against a running server and record what
actually survives. Until that is done, the question stays open.

### What this does NOT protect against

⚠️ **Closing the console window still ends the server, and always will.** Windows gives a
process a few seconds on `CTRL_CLOSE_EVENT` and then kills it regardless of what it wants.
**This is what actually took the captions down on 3 September**, and no amount of interrupt
handling changes it. The answer is the headless task, not a guard — run the server with no
window and there is nothing to close.

⚠️ **Task Scheduler still reports the task as `Running` after the server inside it has died.**
That was true before this change and is true after it. Windows status is not a liveness check;
the captions on the screen are.

## Boot behaviour — decided: start at user logon

**Recommendation: the tool starts automatically when the mandir PC's dedicated user logs in,
via a Task Scheduler entry — not a true "start before anyone logs in" boot service.**

Reasoning:

- A true boot-time Windows *service* runs in Session 0, which cannot access audio hardware or
  show a UI on modern Windows — so "starts on boot" in the strict OS sense isn't viable for
  this tool on Windows regardless of preference. The practical equivalent is starting as soon
  as the operating user's desktop session exists, which is what a logon trigger does.
- Starting the server does **not** start transcription or touch the mic input beyond querying
  device names — per the existing UI flow (README: "pick the mic device, click ▶ Start"), the
  operator still has to press Start. So having the server already running before the operator
  sits down carries no risk of capturing audio unintentionally; it only removes a step from the
  pre-katha routine.
- If the PC reboots mid-day (Windows Update, power blip), the tool comes back with zero action
  from anyone — matching the rule this setup is built under: no manual step a machine
  could do.
- The alternative (start by hand every time) is still fully supported and requires no setup:
  just double-click `start-captions.bat` before each katha. If the mandir prefers that, skip
  the step below entirely; both paths exist in the repo.

To apply the recommendation, run once during machine setup, from the checkout, as the same
Windows account that will run katha (after `deploy.ps1` has built `.venv`):

```powershell
powershell -ExecutionPolicy Bypass -File register-startup-task.ps1 -StartNow
```

An ordinary PowerShell is enough: both tasks are registered for your own account, which Task
Scheduler allows without administrator rights. [UNVERIFIED: if it says *Access is denied*, run
it again from an elevated PowerShell (right-click, Run as administrator) and tell the people
who maintain this, because this paragraph is then wrong.]

That one run registers **two** tasks, both for that account, both at logon:

| Task | Runs | Log |
|---|---|---|
| `LiveCaptions` | the captions server, headless: `deploy.ps1 -StartOnly -NoBrowser`, through a hidden `powershell.exe` | `logs\live-captions.log` |
| `LiveCaptionsOverlayGate` | the gate that follows vMix's overlay 2 button: `.venv\Scripts\pythonw.exe tools\vmix\overlay_gate.py` (no console at all) | `logs\overlay-gate.log` |

Neither task has a time limit. **Who restarts what:**

- **The server task supervises the server itself.** It passes `-Supervise`, so `deploy.ps1`
  stays running and launches the server in a loop. When the server exits with an error (a
  crash, a kill), `deploy.ps1` waits 10 seconds and starts it again, with no limit on how many
  times. Before each launch it looks at the port the way a normal start does: a server already
  answering is watched, not doubled, and if it goes the supervisor starts a new one. When the
  server exits cleanly (`stop-captions.bat`, exit code 0) the supervisor stops too, and the
  task ends with result `0`. Its decisions go to `logs\supervisor-<date>.log` (a fortnight
  kept); the server writes its own `logs\live-captions.log` as before.
- **Task Scheduler is the backup.** Both tasks also have its **restart on failure** set, every
  minute, up to 999 times (its ceiling, in effect unlimited). That setting is widely reported
  to fire only when a task fails to start, not when the program ends with an error, so nothing
  relies on it for a crashed server. It is what relaunches a task whose own process ended
  (the supervisor's `powershell.exe` killed, or the gate's `pythonw.exe`).
- **The gate is not supervised separately.** `overlay_gate.py` catches the error of every step
  of its loop and carries on, so only its start-up can end it; Task Scheduler's backup covers
  that.

Neither task redirects output to a file: each process writes its own log, rolled at midnight
with two weeks kept. `-StartNow` starts both at once instead of waiting for the next
logon. [UNVERIFIED on Windows: the supervisor and the backup restart are proven only by dry-run
steps E3 and E3b.]

The script reads both tasks back and exits 1, in red, if either is missing, runs the wrong
program or does not restart. **Do not run it again during a katha:** a task of the same name is
stopped and replaced, so a running server stops until the next logon (or `-StartNow`).

To remove both with one command:

```powershell
powershell -ExecutionPolicy Bypass -File register-startup-task.ps1 -Remove
```

(Or by hand: `Unregister-ScheduledTask -TaskName "LiveCaptions" -Confirm:$false` and the same
for `LiveCaptionsOverlayGate`.)

### A PC that already has hand-made tasks (the Bolton PC)

In October the mandir PC was set up by hand, and its tasks are not the ones above. Registering
replaces a task *of the same name*, so sort the rest out before the next katha:

1. See what is there:

   ```powershell
   Get-ScheduledTask | Where-Object { $_.TaskName -like 'LiveCaptions*' -or $_.Actions.Arguments -match 'overlay_gate|deploy.ps1|start-captions' } |
     ForEach-Object { "$($_.TaskName)  $($_.State)  $($_.Actions[0].Execute) $($_.Actions[0].Arguments)" }
   ```

2. `LiveCaptions` and `LiveCaptionsOverlayGate` are replaced by the register script, whatever
   they ran before (the server in a console through `start-captions.bat`, and a gate run from
   `captions-tools\overlay_gate.py`). The old instance is stopped first, so there are never two
   gates.
3. **A gate registered under any other name, and `LiveCaptionsAdhoc`, are not touched.** Stop and
   delete them by hand, or two gates will both start captures:

   ```powershell
   Stop-ScheduledTask -TaskName LiveCaptionsAdhoc
   Unregister-ScheduledTask -TaskName LiveCaptionsAdhoc -Confirm:$false
   ```

   (the same two lines for any old gate task, with its own name).
4. The old `captions-tools\overlay_gate.py` is no longer used; delete the folder once you have
   copied its hall settings into `overlay-gate.json` ("The overlay gate and its settings",
   below).
5. Check there is exactly one gate and one server:

   ```powershell
   Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'overlay_gate|live_captions' } |
     Select-Object ProcessId, Name, CommandLine
   ```

   One gate (`pythonw.exe ... overlay_gate.py`) and one server (`python.exe ...
   live_captions.py`, under `uv.exe`). Windows may show each as a launcher plus the real process
   with the same command line; two *separate* gates or servers are a fault.

The same PC also carries a local edit to `rules.json`; see "Updating a PC that already has a
modified `rules.json`" in [CHANGELOG.md](CHANGELOG.md).

[UNVERIFIED on a real PC: `register-startup-task.ps1` and the autostart check were written and
checked as text on macOS and have never run on Windows. They stay unverified until the dry run
below has been done.]

### The tasks run with no console window — on purpose

The registered server task starts `deploy.ps1 -StartOnly -NoBrowser` through
`powershell.exe -WindowStyle Hidden`, not `start-captions.bat`. It never opens a browser: a
restart after a crash must not put a tab over vMix in the middle of a katha. **The desk opens
`http://localhost:8765/` by hand** when it wants the operator page. There is deliberately nothing
on screen to close. The gate runs under `pythonw.exe`, which has no console at all.

That answers two faults: the 98 minutes on 3 September with no server running, and the
server being killed from its console. On 3 September the server took two console control
events — 19:19 and 22:16, both landing as `STATUS_CONTROL_C_EXIT`. The cause of one of them is
known: the window sat on the desktop looking empty and useless, so someone closed it. A
window that must not be closed, on a machine other people use, is not a safeguard; it is a
trap with a delay on it.

This does **not** make the interrupt survivable; that is not built. It removes the window
people were closing.

[UNVERIFIED on the mandir PC: the hidden tasks have not yet run there. Starting a hidden
PowerShell may still flash a console window for a moment at logon; the dry run records whether
it does. If the server does not come up at the next logon, check
`Get-ScheduledTask -TaskName "LiveCaptions" | Get-ScheduledTaskInfo` first, then the log file.]

What replaces the window, all three of which are better signals than a scrolling console:

- **`logs/live-captions.log`** — written by the server itself, so it survives whatever started
  it. Rotated at midnight, a fortnight kept. Move it with `--log-dir` or `LIVE_CAPTIONS_LOG_DIR`.
- **The operator page** (opened by hand at `http://localhost:8765/`; `start-captions.bat` opens it
  for you, the logon task does not), which now
  shows a red *No connection* banner whenever this page cannot reach the server — including
  before anyone has pressed Start.
- **`CAPTIONS OFFLINE`** on the overlay itself, ten seconds after the socket goes, so the hall
  screen stops being able to look identical whether the server is quiet or dead.

**Every run of `deploy.ps1` — including the daily `-StartOnly` — now states whether both logon
tasks exist** (`LiveCaptions` and `LiveCaptionsOverlayGate`), warns when one does not restart
if it stops, flags the old `LiveCaptionsAdhoc` task if it is still there, and prints a
full-width red banner naming whichever task is missing or disabled. On 3 September this machine
went to katha with no task registered because `register-startup-task.ps1` had failed with
*Access is denied* and nobody read the error. The machine gave no other sign, and the hall had
no captions for 98 minutes.

[UNVERIFIED on the mandir PC: the autostart banner has not been run against a real Task
Scheduler.]

## Setting up vMix (once per vMix preset)

The overlay gate and the desk both go by names, so the names below must be exact. Do this once,
with the captions server running (started by its logon task) and no katha on.

1. **Turn on vMix's Web Controller.** vMix Settings → Web Controller → enable it, on port
   `8088` (the default). The gate reads vMix and sends it commands at
   `http://127.0.0.1:8088/api/`. Check: open that address in the PC's browser; it should show a
   page of XML beginning `<vmix>`.
2. **Add the captions as a browser input.** Add Input → Web Browser. In the URL box type
   exactly this (it is the address the gate also uses, and it has no comma, which vMix
   refuses):

   ```
   http://127.0.0.1:8765/?overlay=1&lines=2&fs=64&fw=600&tbg=25&panel=1&ailabel=1
   ```

   Set the size to 1920 × 1080 (or your production's size) and press OK.
   If vMix is on another PC, use the captions PC's address in place of `127.0.0.1`, and put
   the same address in `overlay_url` and `captions_server` in `overlay-gate.json` ("The overlay
   gate and its settings", below).
3. **Name the input `Captions Overlay`.** Right-click the input → Rename → `Captions
   Overlay`, with those capitals and the space. The gate finds the input by this title, never
   by its number, because the number moved four times in katha week. Check: the XML at
   `http://127.0.0.1:8088/api/` now contains `title="Captions Overlay"`.
4. **Put it on overlay 2.** Under the input are four small overlay buttons, 1 to 4. Overlay 2
   is the desk's captions button: pressing it puts the input on air, and the gate then starts
   the captions. Press it once to see captions on the programme (speak into the mixer, or
   play some Gujarati), then press it again to take it off.
   Leave overlay 2 **off** when you finish.
5. **Save the preset.** Save the vMix preset (the Save button, or Save As to keep the
   old one). vMix keeps the URL it was last given and loads that one on every start, so a
   preset saved now starts with the right URL every time. Without this step the URL has to be
   put back by hand after every vMix restart.

   **If a preset already has this input with an older URL** (for example `lines=1`, or no
   `tbg=25&panel=1`): open the input's settings, paste the URL above, and save the preset
   again. The gate does not rewrite a URL that loads and reports: it navigates only an input
   that has not reported at all (see below), so an old, working URL stays as it is.
6. **Audio.** The hall's sound reaches the captions through one of the PC's audio inputs
   (in Bolton, its `Line In`), which the server reads directly (shared mode, alongside vMix).
   Nothing is routed from vMix to the captions. Put that input's name in `overlay-gate.json`
   as `device_name`, exactly as the operator page lists it before the ` — `, so the desk's
   button starts the captions on it. See "Running it in the hall" for the traps.

Nothing else needs doing in vMix. The AI-generated subtitles label is part of the overlay, so
it needs no separate input or layer.

## The overlay gate and its settings

`tools\vmix\overlay_gate.py` is the program behind `LiveCaptionsOverlayGate`. Once a second it
looks at vMix and at the captions server, and does one of four things:

| It sees | It does |
|---|---|
| Overlay 2 carries `Captions Overlay` and nothing is capturing | **Starts** a capture on the named audio device with the hall's speech settings |
| Overlay 2 has been off for the stop grace (10 s) and a capture is running | **Stops** it. Coming back on within the grace cancels the stop |
| Overlay 2 is on and no overlay page has reported to the server for 15 s | **Navigates** the vMix input to `overlay_url`. If that does not bring it back, again after 30 s, then once a minute |
| vMix cannot be read (closed, loading, busy), or the server does not answer | **Nothing.** A vMix restart does not stop a running capture |

A start is refused, and logged, when the audio device is not there today; the gate keeps trying
each second, so it starts by itself once the server can see the device. Before a start by name
the server re-reads Windows' list of audio devices, at most once every 10 s and never while a
capture or the level monitor has a device open, so a device plugged in, disabled or re-enabled
after the server started is seen as it is now, without a restart. [UNVERIFIED on Windows: the dry
run records whether it is.] It never starts on a different device.
The input and the device are found by name every time.

### The settings file

The hall's own settings are in **`overlay-gate.json`** in the checkout, beside `.env`. It is not
tracked, so an update never overwrites it. Copy `overlay-gate.example.json` to start one, and
keep only the keys you want to change: any key left out takes the default. A missing file or a
file that does not parse is not fatal; the gate runs on the defaults and says so in its log.

**Every hall sets `device_name`.** The defaults name no hall's device, so with it left empty
the gate sends no device and the server captures from its default input, which under MME is
usually `Microsoft Sound Mapper - Input` and not the hall's input. The Bolton hall's file, for
example:

```json
{ "device_name": "Line In (Realtek(R) Audio)", "device_host_api": "MME" }
```

The gate reads the file when it starts, so after editing it:

```powershell
Stop-ScheduledTask -TaskName LiveCaptionsOverlayGate
Start-ScheduledTask -TaskName LiveCaptionsOverlayGate
```

| Key | Default | What it is |
|---|---|---|
| `input_title` | `Captions Overlay` | the vMix input's title, as typed in "Setting up vMix" |
| `input_key` | empty | optional: the input's vMix key (from the XML), tried if no input has the title |
| `overlay` | `2` | the overlay button the desk uses |
| `device_name` | empty | the hall's audio input, exactly as Windows names it (Bolton's is `Line In (Realtek(R) Audio)`). Empty sends no device: the server's default input. **Set it** |
| `device_host_api` | empty | the Windows audio API of that input. Empty means any. Use `MME` or `Windows DirectSound`; see the trap in "What has been verified" |
| `source_lang` / `target_lang` | empty | the language direction a start asks for (`gu-IN`, `en-IN`, ...). Empty sends none, and the server keeps its own: `DEFAULT_SOURCE_LANG` / `DEFAULT_TARGET_LANG` in `.env`, or what the operator page last set |
| `speech` | v4, `force_flush_sec` 4, `min_coverage` 0.4, `lag_limit_sec` 20 | what a start sends to the speech service. Any key you leave out keeps its default |
| `stop_grace_sec` | `10` | how long overlay 2 stays off before the capture stops |
| `navigate_grace_sec` | `15` | how long an overlay page may stay silent, with overlay 2 on, before the input is navigated |
| `overlay_url` | the URL in "Setting up vMix" | where the input is pointed. Must contain `overlay=1` and only the names the overlay understands (ADR-0002); otherwise the default is used and the log says why |
| `vmix_api` | `http://127.0.0.1:8088/api/` | vMix's Web Controller |
| `captions_server` | `http://127.0.0.1:8765` | the captions server |
| `poll_sec` | `1` | how often it looks |

### Its log

`logs\overlay-gate.log`, rolled at midnight, two weeks kept. One line per change, with the
reason. To read what happened at a katha:

```powershell
Get-Content logs\overlay-gate.log -Tail 40
```

What the lines mean:

| Line says | Meaning |
|---|---|
| `overlay gate armed: follows overlay 2 ...` | it started, with these settings |
| `settings from ...; defaults for ...` / `no settings file ...` | which keys came from the file |
| `no device_name set: using the server's default input ...` | set the hall's input in the file |
| `overlay 2 ON` / `OFF` | the desk's button |
| `capture start: device ...` / `capture stop: ...` | what it did to the server |
| `back on after N s: stop cancelled` | a quick flick of the button |
| `vMix unreadable ... doing nothing` / `vMix readable again` | vMix closed or restarting |
| `captions server not answering ...` / `answering again` | server down or restarting |
| `overlay 2 ON but no overlay surface has reported for N s ...: BrowserNavigate ...` | it put a blank overlay back |
| `overlay surface reporting again after N navigates` | that worked |
| `could not change capture state: ... No input device named ...` | the audio device is not there today |
| `could not navigate ...` | vMix refused or did not answer the navigate |

## Manual, one-time steps — and why they're unavoidable

Per the "no manual step a machine could do" rule, everything that can be scripted above is
scripted. Two things remain genuinely manual because they are OS-level permission prompts tied
to a specific piece of hardware/software on the specific machine, which cannot be pre-answered
from a script running on a different computer:

1. **Microphone permission.** Windows should prompt the first time the server reads from the
   mic (`Settings → Privacy & security → Microphone`). If no prompt appears, that setting needs
   checking by hand. [UNVERIFIED — see below.]
2. **Firewall prompt.** Windows Firewall will very likely prompt the first time the server
   starts, to allow `python.exe` network access. Accept it for **Private networks** at minimum.
   Answer it at the install's first start (`deploy.ps1` starts the server in the foreground);
   after that the server starts hidden, and a prompt nobody can see is a blocked port.
   See the diagnostic checklist for what breaks if this is dismissed instead of accepted.

## Diagnostic checklist

Run through this on the real machine after setup. Each item is something the person at the
mandir can check without developer knowledge.

1. **Web bundle actually built:**
   - Confirm `web\dist\index.html` exists in File Explorer, **or**
   - open `http://localhost:8765/` — if it shows the real operator UI (source/target pickers,
     mic meter), the bundle built; if it shows a plain placeholder page saying to build first,
     step 7 of `deploy.ps1` (`pnpm build`) did not complete and needs re-running with its output
     read for errors.

2. **Audio device visible and receiving signal:**
   - In the operator page, open the device dropdown — the mandir's USB audio input should be
     listed by name.
   - Select it and check the mic level meter moves when there's sound at the input. `0% /
     [SILENT]` with real sound present means either the wrong device is selected, or Windows
     hasn't granted microphone permission to the app yet (see below).
   - If the device isn't listed at all: check `Settings → Sound → Input` shows the device at
     the Windows level first — if Windows itself doesn't see it, the tool can't either.

3. **Microphone permission actually granted:**
   - `Settings → Privacy & security → Microphone` → confirm the toggle for letting desktop
     apps access the microphone is on, and that nothing is blocking it specifically for Python.
   - [UNVERIFIED: whether Windows auto-prompts for this the first time the server tries to
     read the mic, the way it does for most desktop apps, or whether it silently fails closed
     the way macOS does per the note in README.md. If the meter stays silent with permission
     seemingly on, this is the first thing to check by hand.]

4. **Firewall / mDNS didn't get silently blocked:**
   - On first start, watch for a Windows Defender Firewall popup ("Windows Defender Firewall
     has blocked some features of python.exe"). **Allow access** on Private networks. If it was
     dismissed or blocked instead:
     - other devices on the LAN (a second browser tab on another machine, a Pi sidecar) won't
       be able to reach `http://<this-pc>:8765/`, and
     - the mDNS LAN-discovery broadcast (`_captions._tcp.local.`, used so caption sidecars can
       auto-find the tool) won't reach the network.
     - Fix: `Settings → Privacy & security → Windows Security → Firewall & network protection →
       Allow an app through firewall` → find Python (or add the `.venv\Scripts\python.exe`
       inside this folder) → tick Private.
   - [UNVERIFIED: exact prompt wording, and whether it fires once per Python binary path — if
     `.venv` ever gets deleted and recreated, the firewall may treat the new `python.exe` as a
     different app and prompt again.]

5. **Server survives a restart / logon-trigger works (if boot-start was chosen):**
   - Restart the PC, log into the mandir account, and confirm `http://localhost:8765/` comes up
     on its own within a minute or two, with no window needing to be clicked.

6. **Both background jobs are alive:**

   ```powershell
   Get-ScheduledTask -TaskName LiveCaptions,LiveCaptionsOverlayGate | Get-ScheduledTaskInfo |
     Select-Object TaskName, LastRunTime, LastTaskResult
   Get-Content logs\live-captions.log -Tail 5
   Get-Content logs\overlay-gate.log -Tail 5
   ```

   Task Scheduler says `Running` even when the program inside has died, so judge by the
   logs: the last lines should be from the last minute or two, and the gate log should say
   `overlay gate armed` since the last logon. A `LastTaskResult` of `0x41301` means it is
   running; `0xC000013A` means its console was closed.
7. **The look of the overlay:** with overlay 2 on, the hall screen shows two lines on a dark
   panel and the AI-generated subtitles label at the bottom right. One line, or no panel,
   means vMix is showing an older URL: see "Setting up vMix", step 5.

## Running it in the hall (katha week, Oct 2026)

Learned in the Bolton hall during the 53rd Patotsav. Each one cost air time.

- **Find things by name, never by number.** The audio device index and the vMix input
  numbers both moved between sessions. Pick the device named `Line In (Realtek(R) Audio)`, and the vMix
  input whose title is `Captions Overlay` (its audio comes from the input titled
  `Audio Line In`). The operator page and the overlay gate both go by the device's name; if
  the page says the device used last time is not in the list, nothing is selected: check
  Windows still sees the hall's input before choosing one. By hand, start with
  `{"device_name": "Line In (Realtek(R) Audio)", "device_host_api": "MME", ...}` rather than
  a `device` number.
- **The agreed overlay URL** is `…/?overlay=1&lines=2&fs=64&fw=600&tbg=25&panel=1&ailabel=1`.
  vMix loads whatever URL its preset was saved with, and a URL put in with a navigate command
  is not what the preset saves. Set it in the input's settings and save the preset
  ("Setting up vMix"); then check the look after loading a preset.
- **The "AI-generated subtitles" label is part of the overlay** (`ailabel=1`, on by default).
  It goes on and off air with the captions, so it doesn't need a separate vMix input.
- **Forced flush at 4 s.** Long, unbroken speech made Saaras hold a line for 15 s or more.
  `force_flush_sec: 4` (the default in the operator UI) took the hall's p90 from 7.7 s to
  6.3 s, and lines of 6 s or longer from 14% to under 1%. Set it to `0` to turn it off.
- **Restarting a capture:** after `POST /api/stop`, wait for `/api/overlay-status` to show
  `capture.running: false` before `POST /api/start`. Sent back to back, the start replies
  `Already running` and then nothing is capturing (a 15 s outage on 6 Oct).
- **Any new vMix Browser overlay:** before putting it on air, check it is transparent with
  `SnapshotInput` (the corner pixel's alpha should be 0). A page that isn't transparent covers
  the whole programme.
- **"Delay" complaints:** the `[x s]` on a `FINAL` log line is the segment's `audio_duration`,
  not latency. For speech start → translated, take the FINAL time minus that duration, up to
  the `translate fan-out` line.
- **No `git pull`, no update and no reinstall on a katha day.** On 5 Oct a clone was moved to a
  newer commit by accident before the start, and had to be put back.
- **A muted mixer send looks exactly like a quiet speaker.** On 8 Oct the stream had no sound
  for an hour (09:26–10:30), because someone muted the mixer output that feeds the PC's
  `Line In`. On the desk it was labelled for something else. The captions had nothing to hear,
  and nothing on screen said why. Label that output on the desk, for example "PC / STREAM – DO
  NOT MUTE". If the operator page's level meter sits at zero while someone is speaking, check
  that mute first.
- **Judge a change by replay first:** `python tools/replay_audio.py --wav <clip.wav> --out <dir> --model … --force-flush …`
  on a recorded clip, before trying it on a live katha.

## Dry run: the acceptance test (a clean PC, nobody at the PC)

"Runs on its own" is only true once this has been done. It is the test of the October update:
the two logon tasks, their restarts, and each recovery below. **Do it on a day with no katha**,
with nobody at the PC to start, fix or restart anything by hand. From Part D onward
nobody types a command except where a step says *check*; hands go only on vMix.

Tick each box, write down what you saw, and do not skip an item because the one before passed.
Items marked **(first time on Windows)** are things nobody has seen work yet. If one fails,
the answer is a fix in the code or this file, not a note saying it was fine.

Have ready: the keys in `.env`, the mixer feeding the PC's `Line In`, vMix with its Web
Controller on, a few minutes of spoken Gujarati that can be played into the mixer (or a
speaker), and a second person to move audio and the vMix buttons. Write down the commit
(`git rev-parse --short HEAD`), the Windows version and the vMix version.

### Part A: install

- [ ] **A1. Install from a clean checkout.** `deploy.ps1` ends with every step `OK`, and the
      server answers at `http://localhost:8765/`. The autostart check at the end says in red
      that the two tasks are missing: expected here.
- [ ] **A2. `pythonw.exe` exists in the uv virtual environment (first time on Windows).**
      `Test-Path .venv\Scripts\pythonw.exe` prints `True`. The gate task cannot run without it.
- [ ] **A3. Registration from an ordinary, unelevated PowerShell (first time on Windows).**
      `register-startup-task.ps1 -StartNow` prints two green `OK Task ... registered and read
      back` lines and no *Access is denied*. If it needs an elevated PowerShell, record that
      and change the paragraph in "Boot behaviour".
- [ ] **A4. Restart on failure is what was asked for (first time on Windows).**
      `Get-ScheduledTask -TaskName LiveCaptions,LiveCaptionsOverlayGate | ForEach-Object { $_.TaskName; $_.Settings.RestartCount; $_.Settings.RestartInterval; $_.Settings.ExecutionTimeLimit }`
      shows `999`, `PT1M` and `PT0S` for each. Task Scheduler is allowed to reject a
      restart count this high; if it did, the script would have said so in red.

- [ ] **A5. Hand-made tasks are replaced, never left beside the new ones.** Only on a PC that
      has them (the Bolton PC), or make them *before A3*: a task named `LiveCaptionsOverlayGate`
      that runs a copy of the old gate from another folder, a gate task under another name,
      and a `LiveCaptionsAdhoc`. After A3, run the listing in "A PC that already has hand-made
      tasks". The same-name gate task now points at `tools\vmix\overlay_gate.py`. The register
      script leaves the other-named gate and `LiveCaptionsAdhoc` alone, so stop and delete
      them by hand as that section says; then `Get-ScheduledTask -TaskName LiveCaptionsAdhoc`
      says *no matching task*, and exactly one gate is running.

### Part B: logon

- [ ] **B1. Sign out of Windows and in again, and touch nothing.** Within two minutes:
      `http://localhost:8765/` opens (by hand: no browser tab opens by itself); `logs\live-captions.log`
      and `logs\overlay-gate.log` both have new lines, the gate's saying `overlay gate armed`;
      both tasks show a `LastRunTime` of the logon. **No console window is on screen.** Write down
      whether one flashed for a moment at logon (the hidden PowerShell might): a flash is
      harmless, a window that stays is a defect.
- [ ] **B2. The server marks the hall's input as the default, under MME (first time on
      Windows).** `(Invoke-RestMethod http://localhost:8765/api/devices).devices | Where-Object default`
      names `Line In (Realtek(R) Audio)` with host API `MME`, not `Microsoft Sound Mapper`. Set
      the PC's default recording device to the hall's input first if it is not. **This may well
      fail:** the server marks PortAudio's default input, and under MME that is usually
      `Microsoft Sound Mapper - Input`, which records from whatever Windows' default recording
      device is. Record which it marks. The Sound Mapper is acceptable if a start on it
      captures the hall's signal; the operator then picks the real input by name once and the
      page remembers it. It is a fault if it marks a silent input such as `Stereo Mix`.
- [ ] **B3. The operator page remembers by name.** Open the operator page, pick the hall's
      input, reload: it is still selected. Then disable that input in Windows (Sound settings →
      the input → Disable), reload: nothing is selected and the page says the device is not
      there. Enable it again.

### Part C: vMix

- [ ] **C1. Follow "Setting up vMix" from a blank vMix**, and save the preset. At the end
      `http://127.0.0.1:8088/api/` shows the input titled `Captions Overlay`.
- [ ] **C2. vMix 26 accepts `BrowserNavigate` with `Input=<key>` and a `%2C`-encoded
      comma (first time on a real vMix).** Copy the input's `key` from that XML. With overlay 2
      on, open these in the browser, one after the other, and look at the hall screen after
      each. They are the call the gate makes, written out by hand (`%3A`, `%2F` and the like
      are the URL encoded once for vMix; a comma inside it is `%252C`):
      1. a one-line look, no comma:
         `http://127.0.0.1:8088/api/?Function=BrowserNavigate&Input=<KEY>&Value=http%3A%2F%2F127.0.0.1%3A8765%2F%3Foverlay%3D1%26lines%3D1`
         The overlay shows one line, so `Input=<key>` works.
      2. a URL with commas in it:
         `http://127.0.0.1:8088/api/?Function=BrowserNavigate&Input=<KEY>&Value=http%3A%2F%2F127.0.0.1%3A8765%2F%3Foverlay%3D1%26lines%3D2%26area%3D60%252C830%252C1800%252C200`
         The overlay loads (the screen is not blank). A blank input here means vMix did not take the
         `%2C`; record it, because a hall that puts a comma in its URL would never be put back.
      3. put the agreed URL back the same way (the one in "Setting up vMix", encoded), and
         check the look is two lines on the panel again.
      Take overlay 2 off when you are done.

### Part D: a katha with nobody at the PC

- [ ] **D1. Press overlay 2.** Within a few seconds the gate log says `overlay 2 ON` and
      `capture start: device '<your device_name>' (<host API>)` (on the Bolton PC,
      `'Line In (Realtek(R) Audio)' (MME)`), and the operator page shows the capture running
      on that device.
- [ ] **D2. Ten minutes of audio.** Play the Gujarati into the mixer. The hall screen shows
      two lines on a 25% panel with the label bottom right, a new line a few seconds after the
      speech; `results\` has a new `.jsonl` file growing; no red banner on the operator page.
- [ ] **D3. A quick flick.** Take overlay 2 off and on again within 10 seconds. The capture
      keeps running; the gate log says `back on after ...: stop cancelled`.
- [ ] **D4. Off for real.** Take overlay 2 off and leave it. About 10 seconds later the log
      says `capture stop` and the operator page shows nothing capturing. Press overlay 2 on
      again for the next items.

### Part E: each recovery, with no hand on the PC

- [ ] **E1. vMix restarts while captions run.** Close vMix and open it again with the saved
      preset. The captions server and the capture are untouched; the gate log says `vMix
      unreadable ... doing nothing` and then `vMix readable again`, and does not stop the
      capture. Press overlay 2 on again: the look is the agreed one (two lines, panel,
      label) without anyone navigating, because the preset was saved with the URL.
- [ ] **E2. vMix opened before the server: the blank hour of 11 Oct.** Stop the server with
      `stop-captions.bat`. Close vMix, open it with the preset, press overlay 2 on, and wait a
      minute: the input shows a connection error. Now start the server
      (`Start-ScheduledTask -TaskName LiveCaptions`) and touch nothing else. **The overlay
      appears on its own within about 30 to 45 seconds of the server answering** (the gate
      waits 15 s, navigates, and the page loads and reports: a target to beat, not a measured
      figure), and the gate log has
      `overlay 2 ON but no overlay surface has reported ...: BrowserNavigate 'Captions
      Overlay' -> ...` followed by `overlay surface reporting again after 1 navigate`. Record the
      seconds. This is the proof that vMix's `BrowserNavigate` works as the gate sends it
      (with `Input=<key>`).
- [ ] **E3. The server crashes: the supervisor restarts it (first time on Windows).** Kill it
      hard:
      `Get-CimInstance Win32_Process | Where-Object CommandLine -match 'live_captions' | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }`
      Within about 30 seconds `http://localhost:8765/` answers again from a new process.
      `logs\supervisor-<today>.log` says `server exited <N>, restarting in 10 s` and then
      `starting the captions server`; the server log carries on in the same
      `logs\live-captions.log`; the gate log says `captions server answering again`; and with
      overlay 2 on a capture starts by itself. Record the seconds, and the exit code `<N>`. If
      the supervisor log says `server stopped cleanly (exit 0)` after a kill, a killed server
      reads as a deliberate stop and is not restarted: that is a fault in the supervisor, not a
      pass. Kill it three times in a row: it must come back each time.
- [ ] **E3b. The supervisor itself dies: Task Scheduler's backup (first time on Windows).** Kill
      the supervisor and its server:
      `Get-CimInstance Win32_Process | Where-Object CommandLine -match 'deploy.ps1.*-Supervise|live_captions' | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }`
      Wait three minutes. Record whether `http://localhost:8765/` answers again and
      `Get-ScheduledTask -TaskName LiveCaptions | Get-ScheduledTaskInfo` shows a new
      `LastRunTime`. Either result is information, not a failure of the supervisor: if nothing
      comes back, the backup does not fire for a killed task, and the only protection is that
      nothing kills the supervisor (it has no window to close).
- [ ] **E4. A deliberate stop is not undone.** Double-click `stop-captions.bat`. Wait five
      minutes. The server is still down, and `Get-ScheduledTask -TaskName LiveCaptions |
      Get-ScheduledTaskInfo` shows a `LastTaskResult` of `0`. Start it again with
      `Start-ScheduledTask -TaskName LiveCaptions`.
- [ ] **E5. `Stop-ScheduledTask` ends the whole server (first time on Windows).** Run
      `Stop-ScheduledTask -TaskName LiveCaptions`. Then no `live_captions` process is left
      (the listing in "A PC that already has hand-made tasks") and
      `Get-NetTCPConnection -LocalPort 8765 -State Listen` finds nothing. If a `python.exe`
      is left holding the port, a restart's supervisor finds the port taken, says `a captions
      server is already running` in its log and only watches it, leaving a server with no
      supervisor of its own; record it and fix the task's stop. Do the same with
      `LiveCaptionsOverlayGate` and check no `pythonw.exe ... overlay_gate.py` is left. Start both again
      with `Start-ScheduledTask`.
- [ ] **E6. The audio input is missing at the start.** Disable the hall's input in Windows
      (or unplug it; a jack with no cable may still be listed, so disabling is the surer
      test). Press overlay 2 on. The gate log says `could not change capture state: ... No
      input device named 'Line In (Realtek(R) Audio)' under MME. Nothing started.` once, no
      capture starts, and nothing starts on `Stereo Mix` or another input. Enable the input
      again. Expected: within about ten seconds the gate log says `capture start`, without anyone
      pressing anything, and the server log says `re-read the device list`. If it does not,
      restart the server (`Stop-ScheduledTask` then `Start-ScheduledTask`, task `LiveCaptions`)
      and see whether it starts then. Record which, because a restart being needed is a fault.
- [ ] **E7. The gate and server logs roll over at midnight.** Leave the PC on past midnight
      with both tasks running. After midnight press overlay 2 on and off once (the gate writes
      only when something changes, and a log file rolls over on its first write after
      midnight). Then `logs\` has `overlay-gate.log.<date>` and `live-captions.log.<date>` for
      the day before, and the new `overlay-gate.log` and `live-captions.log` have lines from
      after midnight.

### Part F: the word rules

- [ ] **F1. A new install seeds them.** On a PC with no `rules.json`, the first server start
      creates it from `rules.starter.json`; the Rules tab lists the bad-word rule, and
      `git status` does not show `rules.json`.
- [ ] **F2. A PC with a local edit keeps it.** On a PC whose `rules.json` is a modified
      tracked file (the Bolton PC), follow "Updating a PC that already has a modified
      `rules.json`" in [CHANGELOG.md](CHANGELOG.md). The update runs without `git pull`
      refusing, and the Rules tab shows the hall's own rule afterwards.

### Part G: finish

- [ ] **G1. Nothing was left tracked or changed.** `git status` shows no modified tracked
      file, and `logs\`, `results\`, `rules.json` and `overlay-gate.json` do not show as new.
- [ ] **G2. Write down the result.** For each item: passed, failed (what happened), or not
      done (why). Keep the record with the commit you wrote down, and change this
      file: delete the `[UNVERIFIED]` marks the dry run proved, and fix any step it disproved.
      An item that failed stays in "Still not fixed" in [CHANGELOG.md](CHANGELOG.md) until it
      is fixed.

## What has been verified, and what has not

**Verified on the mandir PC (Windows 11 Pro 26200) on 2026-08-31:**

- `winget` is present and works; the Node LTS package ID resolves.
- The `irm https://astral.sh/uv/install.ps1 | iex` install works under `-ExecutionPolicy Bypass`.
- The Windows `sounddevice` wheel bundles PortAudio — no manual native install of any kind.
- `npm install -g pnpm` needs no elevation.
- The tool and vMix hold the same audio input simultaneously, in shared mode, with real signal.
- Spoken Gujarati through the mixer returns English captions, first one about 2 s in.

**Still unverified: everything in the October update.** The two logon tasks and their
restarts, the gate's re-navigate, the device-by-name start and the rules starter were written
and tested on macOS against fakes. The dry run above is how each of them is proven on Windows.

**Verified in katha week (5–11 Oct 2026), with someone at the PC mending it by hand:** the captions ran
on the hall screen and the YouTube stream through morning and evening sessions, with vMix
streaming. That settles captions on the vMix programme output, and a katha-length run with
vMix streaming, as "it works". It does **not** settle "it works with nobody there": that is
what the dry run is for.

**Still unverified from before:** a restart of the machine with the new tasks in place.
The Bolton PC's old logon task did start at a logon on 23 Oct, but that task ran in a
console and had no restart.

**A trap worth knowing before it wastes an hour:** Windows lists the same physical input once
per audio API, all under the *same name*. On this PC `Line In (Realtek(R) Audio)` appears four
times and **two of them do not work** at the 16 kHz this tool captures at — WASAPI accepts only
the endpoint's own 48 kHz mix format, and WDM-KS opens without error and then delivers no
frames. Use the **MME** or **DirectSound** entry. The device picker now appends the API to each
name and disables the ones that cannot work, so this should no longer be discoverable the hard
way.

**And a hazard:** never open the audio device in *exclusive* mode on this machine. It succeeds,
and in succeeding it takes the input away from vMix — whose audio goes silent while its own
status still reads `Running`, and which needs a full restart to recover.
