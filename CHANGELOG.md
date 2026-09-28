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

Issue numbers below (`#61`, `#25`, …) refer to the tracker of the project this was developed in.
They are there for provenance and will not resolve in this repository.

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
