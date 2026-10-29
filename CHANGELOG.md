# October 2026: what a full katha week changed

This update comes from the Bolton 53rd Patotsav, a week of morning and evening katha
(4–11 Oct 2026) captioned live into vMix and YouTube. As before, each change exists because
something went wrong on air. The September account starts at "What changed since May 2026"
below.

| | September | Now |
|---|---|---|
| Speech model | `saaras:v3` default | **`saaras:v4`** default; v3 still selectable |
| Long unbroken speech | a line could be held 15 s or more | **forced flush every 4 s**: hall p90 7.7 s → 6.3 s; lines of 6 s or more 14% → under 1% |
| Speech service falling behind | went unnoticed (it ran 11 min behind on 7 Oct) | **lag watchdog** reconnects; fired live on 7 Oct and was back in 1.1 s |
| Session after the audio ends | could hang for hours | **bounded**: closes 5 s after the socket goes quiet, 60 s at most |
| Offensive words in a caption | shown | **masked** to "…" after the operator's rules; the line is kept |
| "AI-generated subtitles" label | a separate vMix input | **part of the overlay**, bottom right, on by default (`ailabel=0` hides it) |
| Starting and stopping | by hand on the PC | **the desk's vMix overlay 2 button** starts and stops the captions, through a gate that is part of the product and starts at logon |
| Server and gate after a crash or a closed window | stayed down | **the server is relaunched by its logon task** 10 s after a crash, with Task Scheduler's restart as a backup for both (a `stop-captions.bat` stop is not undone) |
| Blank overlay after vMix opened before the server | a person re-navigated it (about an hour on 11 Oct) | **the gate navigates it back** within about 30 to 45 seconds |
| Audio device | remembered by number; the number moved 6 → 1 | **by name** in the server, the operator page and the gate |
| Docs for a new mandir | the install notes | **a one-page desk checklist, vMix setup step by step, the gate's settings, and a written dry run** |

**Setting up a new hall PC from scratch**, keys and vMix included, is [`SETUP.md`](SETUP.md): numbered
steps, each with the check that says it is done. In short, installing it on a new PC is three steps: `deploy.ps1`, then `register-startup-task.ps1
-StartNow`, then the one-time vMix setup. They are in [`WINDOWS.md`](WINDOWS.md), with the
dry run that proves "runs with nobody there", and the desk's one page is
[`DESK-CHECKLIST.md`](DESK-CHECKLIST.md). Bolton already runs a hand-made version of all this:
"A PC that already has hand-made tasks" in `WINDOWS.md` replaces it step by step.

**Out of the box** the overlay shows two lines, scrolling line by line, on a 25% panel, with the
label bottom right. The one time the hall saw a single line (5 Oct), it came from `lines=1` in a
stored vMix URL, not from the defaults. See "Getting the captions on screen" in the README.

### What changed

- **`saaras:v4` is the default** in the server and the operator page. A browser whose saved
  settings named the old default is moved onto v4, and keeps its layout.
- **Forced flush** (`force_flush_sec`, default 4 in the operator page, 0 = off). During long
  unbroken speech the tool asks Saaras for a final every N seconds, and a flushed fragment goes
  out at once instead of waiting in the sentence assembler.
- **Lag watchdog** (`min_coverage` 0.4, `lag_limit_sec` 20, both on by default from the operator
  page). Reconnects when Saaras finalises under 40% of the audio sent over 3 minutes, or when a
  final arrives more than 20 s after the audio stopped. On a replay it fired once, inside
  singing, and never during speech. It added no delay.
- **A silent socket after the audio ends no longer hangs the session.** A replay once waited
  2 h 26 min for a socket that never closed. The wait now ends 5 s after the last message, and
  never runs past 60 s.
- **Bad-word mask** on every translated final, after the operator's rules. It is word-boundary
  aware: "class", "assassin", "Scunthorpe" and "shitake" are left alone.
- **The bad-word rule ships in a starter file.** `rules.starter.json` is tracked and holds the
  rule; a new install copies it to `rules.json` on first run, so it appears in the Rules tab.
  `rules.json` is no longer tracked (it is gitignored), so a hall's own rule edits are never
  overwritten or lost by an update. The built-in mask above still runs either way.
- **`rules.json` is written as UTF-8.** A file saved by an older Windows install in cp1252 still
  loads.
- **The "AI-generated subtitles" label** is drawn inside the overlay: right 60 px, bottom 14 px,
  22 px, white at 50% with a soft shadow. It goes on and off air with the captions.
- **`tools/vmix/overlay_gate.py`**: follows the desk's overlay button. Button on starts a
  capture. Button off for 10 s stops it. It finds the vMix input and the audio device by name,
  because their numbers moved between sessions. Set the hall's audio input in
  `overlay-gate.json` as `device_name` (copy `overlay-gate.example.json`): the defaults name no
  hall's device, and with none set it starts on the server's default input and says so in its
  log. It sends a language direction only if the file sets `source_lang` / `target_lang`;
  otherwise the server keeps its own (`.env`, or the operator page's last choice). Its log
  rolls over at midnight and keeps two weeks.
- **The overlay gate puts a blank overlay back.** When overlay 2 is on and no overlay surface
  has reported to the server for 15 s, it tells vMix to navigate the input to `overlay_url` (the
  agreed URL by default). On 11 Oct vMix was opened before the server, its browser input never
  loaded the page, and the hall screen was blank for about an hour. It navigates rather than
  reloads, so a wrong URL vMix restored from its preset is replaced too, **but only when the
  page is not reporting**: an old URL that loads fine, say one with `lines=1`, is left
  alone, so the preset must be saved once with the right URL. An overlay that still has not
  reported is navigated again after 30 s, then once a minute. An overlay surface that is
  reporting is never touched, and neither is the input while overlay 2 is off, vMix is
  unreadable or the server is down. Each navigate is logged
  with its reason. An `overlay_url` that uses a name outside the overlay URL's vocabulary
  (ADR-0002), or has no `overlay=1`, is refused and the default is used. **Not run against a
  real vMix**: the tests drive a fake one.
- **One install run registers both background jobs** (`register-startup-task.ps1`): the
  captions server (`LiveCaptions`) and the overlay button gate (`LiveCaptionsOverlayGate`, run
  from the checkout with `pythonw.exe`, no console). Both start at logon with no window and no
  time limit, and restart on failure every minute, up to 999 times (Task Scheduler's own setting, kept as a
  backup: it is widely reported to fire only when a task fails to start). The server task also
  passes `-Supervise`, so `deploy.ps1` itself relaunches a server that exits with an error, 10 s
  later with no cap, checks the port before each launch so a live server is never doubled, and
  stops when the server exits 0 (`stop-captions.bat`); it logs to `logs/supervisor-<date>.log`.
  The script reads both back
  and exits 1 if either is missing or does not restart; re-running it replaces them (and stops
  the old instance first, so there are never two gates). `register-startup-task.ps1 -Remove`
  removes both. `deploy.ps1` gains `-NoBrowser` (the logon task passes it, so a restart never puts a browser tab
  over vMix; the desk opens `http://localhost:8765/` by hand) and ends with the server's exit code, so a crash is restarted and a
  `stop-captions.bat` stop is not, and its autostart check now names both tasks, says which is
  missing, disabled or not restarting, and flags the old `LiveCaptionsAdhoc`. Neither task
  redirects output to a fixed-name file; each process writes its own rolling log.
  **Not run on Windows.** The scripts were written and checked as text on macOS (the tests read
  them for the task names, the paths inside the checkout, the restart settings and the absence
  of redirects). Whether Task Scheduler accepts them, and that a closed or crashed server comes
  back, is for the dry run in the install docs.
- **The audio device is picked by name everywhere.** The server's device list marks the system
  default input, and `POST /api/start` takes `device_name` (and optionally `device_host_api`)
  as well as a `device` number: it finds today's number for that name, and refuses the start
  when no device of that name is listed. It never falls back to another device. The operator
  page remembers the chosen device by name; when that name is gone it preselects nothing and
  says so. It starts by name too, sending the number only to tell two inputs of one name apart.
  The overlay gate starts by name. The number moved 6 → 1 in katha week and a start
  landed on a silent "Stereo Mix".
- **A glossary fix.** The terms for "cursed" and "conviction" are out of `glossary.json`: they
  came from one passage, reached every line, and the translator showed "cursed intellect" about
  28 times and "conviction" 20 times in a week where neither was said nearby. Re-translating the
  1,310 audited lines took "cursed" from 11 to 1 and "conviction" from 2 to 0.
- **`tools/replay_audio.py`** gains `--model`, `--force-flush`, `--min-coverage` and
  `--lag-limit`, and prints UTF-8 on Windows (a "₹" crashed it on cp1252).
- **WINDOWS.md** has a new section, "Running it in the hall": devices and inputs by name, the
  restart wait, the transparency check, how to read "delay", and the muted-mixer-send outage.
  It now also holds a short install path, "Setting up vMix" step by step (the input titled
  `Captions Overlay`, its URL, overlay 2, and saving the preset once), "The overlay gate and its
  settings" (every key in `overlay-gate.json`, and what each line of its log means), how to
  replace the hand-made tasks of the October PC, and **the dry run**: the written acceptance
  test on a clean PC with nobody at it, covering each recovery in this section. The desk's page is
  `DESK-CHECKLIST.md`.

### Updating a PC that already has a modified `rules.json`

`rules.json` used to be a tracked file, and the Bolton mandir PC carries a local edit to it
(rule `0c70a155`, saved in cp1252). `git pull` refuses to run over that edit, which is safe but
stops the update. Copy the file aside, drop the edit, update, and put the copy back:

    copy rules.json rules.json.keep
    git checkout -- rules.json
    git pull
    copy rules.json.keep rules.json

If the copy is not put back, the next server start seeds a fresh `rules.json` from the starter,
which holds the same rule. A cp1252 file still loads, and is rewritten as UTF-8 on the next save.

The four commands were run in a scratch git repository on macOS, not on the Bolton PC.

### Updating the Bolton PC: name its audio input

The overlay gate used to have the Bolton hall's input built in. It now names no device unless
`overlay-gate.json` does, and would start on the server's default input. Before the first
katha after the update, create `overlay-gate.json` in the checkout, beside `.env`, holding:

    { "device_name": "Line In (Realtek(R) Audio)", "device_host_api": "MME" }

then restart the gate:

    Stop-ScheduledTask -TaskName LiveCaptionsOverlayGate
    Start-ScheduledTask -TaskName LiveCaptionsOverlayGate

Its log then says `device 'Line In (Realtek(R) Audio)' (MME)` in the `overlay gate armed` line,
and has no `no device_name set` line.

### Still not fixed

- **Nothing alerts a person** when the captions go silent on air or the hall screen is blank. On
  8 Oct a muted mixer send left the stream without sound for an hour (64 minutes) and nothing
  said so. The gate puts a blank overlay back and logs it; it does not raise anything.
- **The vMix preset has to be saved once with the right URL**, by a person. The gate corrects
  an overlay that has not loaded, not an old URL that loads: a preset saved with `lines=1` or
  without `tbg=25&panel=1` keeps showing that, and a URL changed by hand does not survive a
  vMix restart.
- **The scrolled-off line's descenders show above the top line** in vMix's Chromium 51 (the
  "glitched" reports of 7 Oct).
- **The mixer is not watched** (a mute upstream of the PC looks like a quiet speaker). Label
  the send that feeds the PC on the desk.
- **Translation faults found by the subtitle check** (a lost "not" or "never", glossary words
  added where nobody said them) are waiting on a decision, apart from the glossary fix above.
- **Nobody who reads Gujarati has reviewed the glossary yet.**
- **None of the October update has run on Windows or against a real vMix.** That covers the
  two logon tasks and their restarts, the gate's re-navigate (vMix's `BrowserNavigate` with an
  input key), the by-name start on a real device list, and the midnight rollover of the gate's
  log. The dry run in `WINDOWS.md` is the test; a failure in it belongs here.
- **Two suspected faults the dry run will confirm or clear.** The server marks PortAudio's
  default input, which under MME is usually `Microsoft Sound Mapper - Input` and not the hall's
  `Line In`. And an input plugged in, disabled or re-enabled after the server started: the
  server used to read Windows' device list only when it started. A start by name now has it
  re-read the list first (never while a capture or the level monitor has a device open, and
  at most once every 10 s; the operator page's device list does the same), so the start should
  find the device as it is now, with no server restart. The re-read uses sounddevice's own
  private calls (`_terminate`, `_initialize`). Neither point has been seen on Windows.

---

# What changed since May 2026

This is everything between commit `834171a` — the last version in this repository — and the
one you are looking at now. It arrives as a single commit, so this file is the account of what
is in it.

Almost all of it came out of running the tool live: **28 hours of a Gujarati katha**, captioned
into a vMix broadcast in front of a congregation, over five days at the start of September
2026. Where a number appears below, something was measured. Where something is unproven, it
says so.

**Nothing in it is specific to that event.** Every name, colour and language default goes
through `.env` as it always did.

---

## The short version

| | Then | Now |
|---|---|---|
| Terminology correct | 13 of 24 | **24 of 24** |
| Tests | 0 | **370** |
| Captions on a blank screen after a failure | possible | **impossible by construction** |
| Windows | unsupported | installer, double-click start, start-at-logon |
| A caption clipped by the box it renders into | silent and common | fixed, and provable without a browser |

---

## 1. The translator learned what the words mean

**This is the change that matters most.** Everything else is plumbing around it.

The tool used to hand Gujarati to Sarvam's Saaras model, which transcribes and translates in
one hop. That is fast and competent, but the model takes no instruction: there is no way to
tell it that this is a devotional discourse, that **કથા** is the recital of scripture and not a
"story", or how a swami's name is spelled. On the first live sample it rendered
`ભગવાનની કથાનો આરંભ કરીએ છીએ` as *"the Lord"* — the central noun simply vanished.

So there is now a **translator ladder**, and `translate_line()` is its single door:

1. **Gemini**, the default, carrying a brief, a glossary and the last few lines for continuity.
2. **Mayura**, Sarvam's text translator, whenever Gemini returns nothing — timeout, safety
   block, HTTP error, empty candidate.
3. **The source text**, which is what Mayura itself falls back to.

**No rung is allowed to return nothing.** A blank caption bar in a full hall is the one outcome
with no recovery, so the system is built so that it cannot happen: a worse caption always beats
no caption.

### The margin is the briefing, not the model

Scored against a published English translation of the Vachanamrut, with full coverage from both:

| | Terminology | Median |
|---|---|---|
| Sarvam Mayura | 13/24 — 54% | 682 ms |
| **Gemini, briefed** | **24/24 — 100%** | 1,770 ms |

> ⚠️ **Asked cold, with no glossary and no passage, Gemini produced the same errors as Mayura**
> — *"We begin the story of God"*. The model is not the margin. `glossary.json` is, and it is in
> this commit. See the glossary section of the README.

On six minutes of the speaker's own katha, replayed through the real pipeline:

| Sarvam | Gemini, briefed |
|---|---|
| "Maharaja Shikshapatri is written" | "Maharaj wrote the Shikshapatri" |
| "Write down all your **movements**" | "Write down all your news" |
| "then Start the details" | "he should begin the details" |

### Also here

- **Whole sentences, not the halves a pause cuts them into.** Sarvam returns a *final* whenever
  the speaker draws breath. Those are assembled into sentences before anything is translated,
  because a translator given half a clause invents the rest.
- **A deterministic substitution list** (`rules.json`) applied after translation, for a
  mishearing you want fixed the same way every time. It runs *after* the ladder and cannot
  undo what the glossary just got right.
- **An offline scoring harness** (`tools/benchmark_translators.py`) so a translator choice is
  settled by measurement rather than by reading.
- **The source Gujarati is recorded**, not just the English. Choosing an external translator
  changes the *shape* of the pipeline, not only the call — Saaras's one-hop shortcut translates
  as it transcribes, so the source never exists, and an outside translator has nothing to work
  from.

> ⚠️ **A fault worth knowing about, because it shipped and nobody noticed.**
> `CAPTION_TRANSLATOR` was a raw string read independently in four places with no allowlist. One
> letter wrong — `gemeni` — and the pipeline forced the expensive two-hop path, the translator
> went straight to Mayura, and the startup banner announced *"Translator: Gemini"*. You paid for
> one model and got the other, and every check passed. It is now validated where it is read, and
> the startup log names the translator **really** in the path, at ERROR level when they differ.

---

## 2. What the hall actually reads

Every item here was a real complaint from the back of a hall, or a fault found by watching a
screen.

- **Captions replace, they do not stack.** One sentence on screen at a time.
- **Whole blocks on a steady cadence**, with the next one preloaded, rather than text appearing
  a word at a time.
- **A bounded dwell** — a floor, a ceiling, and a drop rather than a delay. Settled against a
  measured interval from the recording, not a judgement.
- **A line-by-line roll**, so the previous line is still on screen when the next arrives.
  Replacing the whole screen each time reads fine in testing and is unreadable in a hall.
- **Nothing is ever clipped.** This one was silent and destructive: the number of lines the
  renderer trimmed to was one setting, the height of the box it rendered into was a *different*
  setting, and nothing reconciled them. When the configured line count needed more vertical
  space than the box had, the trim reported success and `overflow: hidden` cut the bottom off —
  and the bottom line is the **newest** line. The fault destroyed the most important words on
  screen, told nobody, and looked from the hall like the captions "cutting off". The arithmetic
  now lives in `web/src/fit.ts` as pure functions of numbers, with **57 tests** and no DOM, so
  the part that can be proven without a browser is proven without one.
- **A translucent panel behind the text**, fixed and rounded — a panel that hugs the text also
  moves with it, which is worse.
- **The caption clears after a silence** instead of leaving the last thing said on the screen
  for the rest of the interval.
- **The "still speaking" indicator is off the hall screen.** It belongs to the operator.
- **Saved settings no longer beat new defaults.** Stale `localStorage` silently won over a
  changed default, and a fix confirmed on screen never reached the hall.

> ⚠️ **The overlay runs in Chromium 51** when vMix hosts it — a 2016 browser, not a modern one.
> Both the JavaScript bundle and the CSS are held to what it can execute. If you change the
> overlay, test it in vMix, not only in Chrome.

---

## 3. Staying up for 28 hours

- **Publishing a caption can no longer block audio capture.** It used to, and a slow translate
  cost 3.5 seconds of the speaker. Now a slow translate costs a late caption.
- **A keep-alive through long silences.** The premise turned out to be wrong — there is no
  reliable 60-second idle timeout; four of five idle sessions survived three minutes of total
  silence, and the one that died reported `1011 keepalive ping timeout`, which was **our own
  client** hanging up on Sarvam. It ships anyway, defaulted on, as insurance across 28 hours,
  and it is provably harmless: digital silence returns no captions, so it cannot put invented
  words on a temple screen.
- **Reconnects back off**, with a floor measured attempt-to-attempt, and on clean closes too.
- **Three states, not two.** *Idle* (nothing running) is distinct from *disconnected* (a fault),
  so a stopped tool never paints like a broken one. Connection state is stored as well as
  broadcast, so a tab opened *during* an outage shows the fault rather than an innocent blank
  screen.
- **The overlay clears on disconnect** rather than freezing the last line, which is a lie.
- **The log outlives the window that started the server.** One outage ran 98 minutes and left
  no log at all, because every line went to a console window's stderr and died with it; the
  cause had to be reconstructed from Task Scheduler timings. Lines are now on disk the instant
  they are logged, a crash is logged as a crash rather than looking like a clean shutdown, and
  a directory that cannot be written to costs the log, never the server.
- **A status board** that can be asked, from a terminal, what a hall screen is actually
  displaying — and that says when a surface on it is a ghost.
- **The window that runs the server now identifies itself**, and a stray Ctrl+C is refused. See
  section 6 — the first of those addresses what actually went wrong, the second does not.

---

## 4. Windows

The tool was macOS-only. The machine it had to run on is a Windows PC in a control room.

- `deploy.ps1` — the installer, including Node and pnpm for the web build.
- `start-captions.bat` — a double-click start.
- `register-startup-task.ps1` — start at logon.
- `stop-captions.bat` — the way to stop it.
- The audio device list is disambiguated: the machine showed **four identically-named "Line In"
  entries** and only the MME and DirectSound ones work at 16 kHz.

> ⚠️ **Never open the audio device in exclusive mode.** The tool and vMix can hold the same
> input simultaneously in shared mode — measured, with real signal, neither dropping. But an
> exclusive-mode open silently killed vMix's audio, survived every one of our processes being
> killed, and needed a full vMix restart to clear, while vMix's own input kept reporting
> *Running*.

> ⚠️ **`deploy.ps1` broke in four places the first time it was ever run on Windows, and three of
> the four printed `OK` while failing.** The worst was Windows-only: `^SARVAM_API_KEY=.+$`
> matches an *empty* key on a CRLF file, because `.` matches `\r` — so the installer reported a
> key it did not have. All four are fixed and re-verified from a clean checkout.

> ⚠️ **Task Scheduler reports a task as `Running` after the server inside it has died.** Windows
> status is not a liveness check.

---

## 5. Models, tests and tooling

- **Dead Saaras models removed** — `saaras:v2` (withdrawn) and `saaras:v2.5` (reachable only
  through a client this app never calls, so it fails exactly like v2). **`saaras:v4` added and
  made the default.** Same audio, same clock, one variable: median hold **0.91s → 0.00s**, worst
  case 4.57s → 3.03s, mean 1.28s → 0.98s, with no quality regression (96.2% character
  similarity, and usually the better reading). A stored model that no longer exists is migrated
  rather than rendered blank. The model list now lives in **one place** in the server and is
  served to the UI, so the dropdown and the server cannot drift.
- **The default direction is pinned** to `gu-IN → en-IN`, so a fresh machine never starts on a
  guessed language pair.
- **370 tests, from zero.** 210 Python, 160 TypeScript. No network, no API key and no audio
  device: a five-minute silence costs about a second, because the clock is faked.
- **`tools/replay_audio.py`** streams a recording through the real pipeline. A katha happens
  once; a recording replays against any change. **Never evaluate a change live when a replay
  would answer it.**
- **The developer's `.env` no longer decides whether the suite passes.**

---

## 6. This hand-over

- **`README.md` rewritten** for someone who is not a programmer: what you need and what it
  costs, both quick starts, the translator choice with the numbers, the glossary explained, how
  to get captions on screen, and an honest list of what is broken.
- **`docs/how-it-works.md`** is the deep end for whoever changes the code.
- **`AGENTS.md`** orients a coding agent — or a person — in about a page: what to read first,
  what to prove on a replay rather than live, and the five things the code cannot tell you.
- **The console window now says what it is.** The captions died twice in one evening during the
  live katha because an unlabelled console window appeared, and the person who found it closed
  it — reasonably, having no way to know what it was. It now carries the title
  `*** LIVE CAPTIONS RUNNING - DO NOT CLOSE THIS WINDOW ***` and a banner naming what closing it
  costs. **Better still, run it headless** with `register-startup-task.ps1`: a window that does
  not exist cannot be closed by someone tidying up.
- **A stray Ctrl+C is separately refused**, and three presses inside two seconds still exit;
  `CAPTION_INTERRUPT_PRESSES=0` refuses every one. This is a guard on a different door from the
  one the outage came through — useful, but not the fix for it.
- **`POST /api/shutdown`** is the way to stop it, fronted by `stop-captions`. Localhost-only
  **and** requiring a custom header — localhost alone is not a guard, because the operator's own
  browser is also `127.0.0.1` and any page open on that machine could otherwise submit a form
  to it.

### What is NOT fixed, and you should know before you merge

- ⚠️ **Closing the console window still kills the server**, and always will. This is what
  actually took the captions down, so the labelled window is a mitigation and the headless task
  is the real answer — and the headless task has never been run for real (see below).
- ⚠️ **The Ctrl+C guard is unproven on Windows.** A console control event there reaches *every*
  process attached to the console, and `start-captions.bat` builds a chain — batch, PowerShell,
  Python. Python holding its ground does not stop `cmd.exe` asking *"Terminate batch job
  (Y/N)?"*. Tested with real signals on macOS and Linux only.
- ⚠️ **vMix restores its stored overlay URL on restart**, silently reverting any setting put into
  that URL — and re-saving the preset overwrites your change rather than keeping it. This has
  already cost two live sessions. The real fix is for the overlay to fetch its configuration
  from the server instead of parsing it out of a query string; it is not done.
- ⚠️ **`start-captions.bat` and `register-startup-task.ps1` have never been run in anger**,
  because the machine they were written for was live and could not be restarted.
- ⚠️ **No native Gujarati reader has ever reviewed the glossary.** A developer chose those 24
  terms from published translations. This is the largest open risk in the project, it is not a
  software problem, and **you are better placed to fix it than anyone who has touched it so
  far.**
