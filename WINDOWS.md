# Windows install & start (mandir PC)

This is the Windows counterpart to the Quick start in [README.md](README.md#quick-start),
which uses `deploy.sh` for macOS. **`deploy.sh` and the macOS path are unchanged** — this is a
parallel path, not a replacement. Nothing in `live_captions.py` is platform-specific: every
dependency in `pyproject.toml` is cross-platform, and the Windows build of the `sounddevice`
package bundles PortAudio, which is exactly the native library `deploy.sh` has to install by
hand via Homebrew on macOS. That's why the steps below have no equivalent of `brew install
portaudio` — there's nothing to install.

> ✅ **This has now been run on the real mandir PC (Windows 11 Pro 26200, PowerShell 5.1) and
> the tool works there**: it installs, captures from the mixer's `Line In` while vMix holds the
> same input, and returns English captions from spoken Gujarati about 2 seconds later.
>
> It was originally written on macOS without being executed, and **seven defects were found the
> first time it met real Windows** — an empty API key reported as present, a build failure
> reported as success, PATH not visible to the installer's own session, a missing directory that
> stopped the server booting, a scheduled-task registration that needed elevation, a browser
> opened before the server existed, and a running server reported as a port conflict. All are
> fixed. That record is the reason to trust what follows and the reason to distrust any future
> addition to it written the same way.
>
> **Still not observed:** a full restart of that machine (vMix has been live on it continuously),
> and a katha-length run with vMix streaming. Those are issues #25 and #24.

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

   Then it starts the server and opens `http://localhost:8765/` in the default browser.

4. Two things still need a human at the keyboard the first time — see "Manual, one-time steps"
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

## Stopping it — and why Ctrl+C is not the way

**To stop the server: double-click `stop-captions.bat`.**

It asks the server to shut down over `POST /api/shutdown` on localhost, which ends it through
the same teardown a clean exit uses — the mDNS advert is withdrawn, any reprocessing job
drains, and the log records a clean stop instead of a hole. Pass a port if you are not on the
default: `stop-captions.bat 9000`.

### Why there is a script for this at all — and what actually happened

On the evening of 3 September the captions server died **twice**, 19:19 and 22:16, and for a
fortnight the recorded cause was wrong.

**What the evidence showed:** Task Scheduler recorded `3221225786` (`0xC000013A`,
`STATUS_CONTROL_C_EXIT`) and the log's last bytes were literally `^C`, straight after healthy
lines. From that, the diagnosis was "somebody pressed Ctrl+C, probably to copy text out of the
window".

**What actually happened, from the owner, 18 September:**

> *"A terminal popped up, I didn't know what for, it was just blank, so I closed it by
> pressing X intentionally."*

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
   console at all**, which is the owner's own answer: *"run it headless so no one closes it."*
   A window that does not exist cannot be closed by someone trying to tidy up. This is the
   recommended way to run on this machine, and it is still unproven only because the machine
   could not be restarted — see #25.

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
Windows is different in a way that may defeat it, and issue #61 is open again because of it.

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
actually survives — that is what #61 is now open for.

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
  from anyone — matching the "no manual step a machine could do" rule this ticket is built
  under.
- The alternative (start by hand every time) is still fully supported and requires no setup:
  just double-click `start-captions.bat` before each katha. If the mandir prefers that, skip
  the step below entirely — this file documents the decision either way, per the acceptance
  criteria, and both paths exist in the repo.

To apply the recommendation, run once during machine setup (same Windows account that will run
katha):

```powershell
powershell -ExecutionPolicy Bypass -File register-startup-task.ps1
```

To undo: `Unregister-ScheduledTask -TaskName "LiveCaptions" -Confirm:$false`

### The task runs with no console window — on purpose

The registered task starts `deploy.ps1 -StartOnly` through
`powershell.exe -WindowStyle Hidden`, not `start-captions.bat`. There is deliberately nothing
on screen to close.

That is issues #57 and #61. On 3 September the server took two console control events —
19:19 and 22:16, both landing as `STATUS_CONTROL_C_EXIT`. The owner has since confirmed the
cause of one of them: the window sat on the desktop looking empty and useless, so he closed
it. A window that must not be closed, on a machine other people use, is not a safeguard; it
is a trap with a delay on it.

This does **not** make the interrupt survivable — that is #61, and it is not built. It
removes the window people were closing.

[UNVERIFIED on the mandir PC: the hidden-window task has not yet run there. It is written
and parse-checked, not observed. If the server does not come up at the next logon, check
`Get-ScheduledTask -TaskName "LiveCaptions"` first, then the log file.]

What replaces the window, all three of which are better signals than a scrolling console:

- **`logs/live-captions.log`** — written by the server itself, so it survives whatever started
  it. Rotated at midnight, a fortnight kept. Move it with `--log-dir` or `LIVE_CAPTIONS_LOG_DIR`.
- **The operator page**, which `deploy.ps1` opens as soon as the port answers, and which now
  shows a red *No connection* banner whenever this page cannot reach the server — including
  before anyone has pressed Start.
- **`CAPTIONS OFFLINE`** on the overlay itself, ten seconds after the socket goes, so the hall
  screen stops being able to look identical whether the server is quiet or dead.

**Every run of `deploy.ps1` — including the daily `-StartOnly` — now states whether the logon
task exists**, and prints a full-width red banner when it does not. On 3 September this machine
went to katha with no task registered because `register-startup-task.ps1` had failed with
*Access is denied* and nobody read the error. The machine gave no other sign, and the hall had
no captions for 98 minutes.

[UNVERIFIED on the mandir PC: the autostart banner's three branches have been executed
against stub cmdlets, not against a real Task Scheduler.]

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
   See the diagnostic checklist for what breaks if this is dismissed instead of accepted.

## Diagnostic checklist (for issue #2, `runs-on:mandir-pc`)

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

## What has been verified, and what has not

**Verified on the mandir PC (Windows 11 Pro 26200) on 2026-08-31:**

- `winget` is present and works; the Node LTS package ID resolves.
- The `irm https://astral.sh/uv/install.ps1 | iex` install works under `-ExecutionPolicy Bypass`.
- The Windows `sounddevice` wheel bundles PortAudio — no manual native install of any kind.
- `npm install -g pnpm` needs no elevation.
- The tool and vMix hold the same audio input simultaneously, in shared mode, with real signal.
- Spoken Gujarati through the mixer returns English captions, first one about 2 s in.

**Still unverified, and each has an issue:**

- **A restart of that machine.** vMix has been live on it continuously, so nothing has tested
  reboot survival or the start-at-logon task end to end — #25.
- **A katha-length run with vMix streaming.** Everything measured so far has been in bursts of
  seconds, on a machine that was not streaming — #24.
- **The captions have never been put into vMix at all** — #23. That is the deliverable.

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
