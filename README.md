# Live Captions

Live subtitles for a spoken event. Someone speaks Gujarati; English text appears on the
screen two or three seconds later, burnt into the video that the hall and the livestream
both see.

It was built for a Swaminarayan katha and proven on one — 28 hours of a swami speaking
Gujarati, captioned live into a vMix broadcast. Nothing in it is specific to that event.
It handles 23 Indic languages and English, in any direction.

**You do not need to be a programmer to run this.** You need two accounts, a text editor,
and about twenty minutes the first time.

---

## Contents

- [What you need before you start](#what-you-need-before-you-start)
- [Quick start — macOS and Linux](#quick-start--macos-and-linux)
- [Quick start — Windows](#quick-start--windows)
- [Stopping it](#stopping-it)
- [Choosing your translator: Gemini or Mayura](#choosing-your-translator-gemini-or-mayura)
- [The glossary — the single most important file](#the-glossary--the-single-most-important-file)
- [Getting the captions on screen](#getting-the-captions-on-screen)
- [What the operator sees](#what-the-operator-sees)
- [Settings](#settings)
- [Running the tests](#running-the-tests)
- [Known problems](#known-problems)
- [Things that were tried and do not work](#things-that-were-tried-and-do-not-work)
- [Changing it](#changing-it)
- [Licence](#licence)

---

## What you need before you start

**A Sarvam AI account and API key.** This does the listening. It is not optional — Sarvam
transcribes the speech in every configuration. Around £8 for a 28-hour event.
<https://dashboard.sarvam.ai>

**A Google Gemini API key, on the paid tier.** This does the translating, and it is what
makes the captions good rather than passable. Around £2.50 for a 28-hour event.
<https://aistudio.google.com/apikey>

> ⚠️ **The Gemini free tier cannot run this live.** A speaker produces roughly 15 captions a
> minute, which is exactly the free tier's per-minute limit. A six-minute test already hit a
> rate-limit error. You need Tier 1. The cost is small; the free tier is simply the wrong
> shape.

**A computer with an audio input**, on the same machine or the same network as whatever puts
the video on screen. A Mac, a Linux box or a Windows PC all work.

**Somewhere to put the captions** — vMix, OBS, ProPresenter, or just a browser window on a
second screen. See [Getting the captions on screen](#getting-the-captions-on-screen).

You do **not** need a Gemini key to try it out. Without one the tool uses Sarvam's own
translator and still works — just less well. See the comparison below.

---

## Quick start — macOS and Linux

```bash
# 1. Install everything (Homebrew, portaudio, uv, and a starter .env)
bash deploy.sh

# 2. Put your keys in .env
#    Open .env in any text editor and fill in:
#      SARVAM_API_KEY=...
#      GEMINI_API_KEY=...

# 3. Start it
uv run python live_captions.py
```

Then open <http://localhost:8765/> in a browser **on the same machine**. Pick your source and
target languages, pick your microphone, and click ▶ Start.

> **macOS microphone permission.** The first time, macOS will not prompt you — the audio
> meter just sits at SILENT forever. Go to System Settings → Privacy & Security →
> Microphone and switch on your terminal app. Then quit the terminal and open it again.

`deploy.sh` also builds the operator page and the overlay. It needs
[Node.js](https://nodejs.org) for that — if Node is missing it says so, the server still
starts, and the operator page shows a placeholder until you build it.

If you would rather do it by hand instead of running `deploy.sh`:

```bash
uv sync
cp .env.template .env
# edit .env, paste your keys

# Build the web UI. Skip this and http://localhost:8765/ is a
# "UI not built yet" placeholder — web/dist/ is not in the repository.
cd web && pnpm install && pnpm build && cd ..

uv run python live_captions.py
```

> **If the operator page says "UI not built yet"**, that is this step. Run
> `cd web && pnpm install && pnpm build`. You need Node.js; `npm install -g pnpm` gets pnpm.
> Re-run it after any change under `web/src/`.

---

## Quick start — Windows

```powershell
# In PowerShell, from the folder you cloned into:
.\deploy.ps1
```

Then edit `.env`, and start the tool by double-clicking **`start-captions.bat`**.

`register-startup-task.ps1` will make it start on its own when the machine logs on, which is
what you want on a dedicated control-room PC.

> **Be honest with yourself about what is proven here.** `deploy.ps1` broke in four separate
> places the first time it was ever run on a real Windows machine — and three of those four
> printed `OK` while failing. They are fixed, and the fixed version has been run from a clean
> checkout. But `start-captions.bat` and `register-startup-task.ps1` have **never been run in
> anger**, because the machine they were written for was running a live broadcast and could
> not be restarted. Try them on a day that does not matter.
>
> Related: Windows Task Scheduler reports a task as **Running** even after the server inside
> it has died. Do not use that as your check that captions are alive. Look at the captions.

`WINDOWS.md` has the longer sequence and the full list of what has and has not been verified.

---

## Stopping it

**Use `stop-captions.bat` (Windows) or `./stop-captions.sh` (macOS/Linux).**

**And do not close the server's window.** During a live event the captions died twice in one
evening because a console window appeared, said nothing about what it was, and the person who
found it closed it — reasonably. On Windows, prefer `register-startup-task.ps1`, which runs the
server with no window at all: what does not exist cannot be closed by someone tidying up.

A stray Ctrl+C is separately guarded:

- **A single Ctrl+C is refused** and logged, and the captions stay up. Proven with real
  signals on macOS and Linux; see the Windows caveat under Known problems.
- **Three presses within two seconds still exit**, so a developer at a terminal always has a
  way out. It just cannot happen by accident.
- Set `CAPTION_INTERRUPT_PRESSES=0` to refuse every interrupt — the right setting for a
  control-room machine.

⚠️ **Closing the console window still ends the server**, and always will. The operating system
does not let a program refuse that. While captions are live, treat that window as untouchable.

The stop scripts call `POST /api/shutdown`. Two things guard it, and both are needed:

- **Localhost only.** The server listens on every interface so vMix on another machine can
  reach the overlay. Ending the whole process is not something the network is offered.
- **A custom header**, `X-Captions-Control: shutdown`. Localhost alone is not a guard, because
  the operator's own browser is *also* 127.0.0.1 — any web page open on that machine could
  otherwise auto-submit a form to this URL and stop the captions, with no preflight and no
  visible sign. A cross-origin form cannot set a custom header. The stop scripts send it; you
  do not have to think about it.

The scripts report honestly: a refused or failed request prints **FAILED**, not "sent".

---

## Choosing your translator: Gemini or Mayura

**Sarvam always does the listening.** The choice here is only about who turns the Gujarati
into English.

| | Terminology correct | Speed | Needs |
|---|---|---|---|
| **Gemini, with the glossary** | **24 out of 24** | ~1.8s | A Gemini key, paid tier |
| Gemini, no glossary | about half | ~1.8s | A Gemini key, paid tier |
| Sarvam Mayura | 13 out of 24 | ~0.7s | Nothing extra |

Scored against a published English translation of the same text, so "correct" means a human
translator's rendering, not a nice-sounding guess.

Set it in `.env`:

```ini
CAPTION_TRANSLATOR=gemini    # the default
CAPTION_TRANSLATOR=sarvam    # use Mayura instead
```

> ⚠️ **A confusing name, and we would rather warn you than quietly rename it.** The value
> `sarvam` selects the **Mayura** translator. Sarvam also does the listening, in both
> settings — so the word means two different things here. Setting `CAPTION_TRANSLATOR=sarvam`
> does **not** change anything about the speech recognition. It only means "let Sarvam do the
> translating too, instead of Gemini".

**What the difference looks like in practice**, from six minutes of real katha audio replayed
through the tool:

| Sarvam Mayura | Gemini, briefed |
|---|---|
| "Maharaja Shikshapatri is written" | "Maharaj wrote the Shikshapatri" |
| "Write down all your **movements**" | "Write down all your news" |
| "then Start the details" | "he should begin the details" |

**Nothing ever returns an empty caption.** If Gemini times out, is blocked, or errors, Mayura
answers instead. If Mayura fails, the original text goes up. A blank caption bar in a full
hall is the one outcome with no recovery, so the tool is built never to produce one — it will
show you a worse caption rather than none.

**Check which translator you are actually using.** Read the startup log. It names the
translator that is *really* in the path, not the one you asked for, and complains loudly if
they differ. This matters: an early version accepted `CAPTION_TRANSLATOR=gemeni` — one letter
wrong — and quietly used Mayura while the log said "Gemini". You would have paid for one and
received the other, and everything would have looked fine.

---

## The glossary — the single most important file

Read this section even if you skip everything else.

`glossary.json` is a short brief about what is being spoken, plus a list of terms and how you
want them rendered in English. It is what makes Gemini worth choosing. **Briefed, Gemini gets
24 out of 24 terms right. Asked cold, with no glossary, it makes the same mistakes as the
cheap translator** — it rendered "the recital of scripture" as "the story of God".

The model is not the margin. The briefing is the margin.

### What is in it

```json
{
  "brief": "A live Hindu devotional katha (scriptural discourse) given in Gujarati by a
            swami at a Swaminarayan mandir. The audience is a temple congregation.
            Register is reverent and plain — not academic, not casual.",
  "reference": "",
  "terms": {
    "કથા": "katha",
    "...": "..."
  }
}
```

- **`brief`** — a few sentences telling the model what kind of speech this is and who is
  listening. Change this first if you are running a different sort of event.
- **`terms`** — the words you want rendered a particular way. Proper nouns, scripture names,
  anything a general translator would turn into an English word and destroy.
- **`reference`** — optional; a published translation to match the register of.

### How to add terms

**Open `glossary.json` in any text editor and add lines to `terms`.** That is the whole
procedure. It is a plain JSON file, it is not compiled into anything, and you do not need to
touch the code. Restart the tool to pick up the changes.

Start from `glossary.example.json` if you want an empty one.

> ⚠️ **Please check the terms that ship with this, and change the ones that are wrong.** The
> 24 terms in `glossary.json` were chosen by a software developer working from published
> translations. **No native Gujarati reader has ever reviewed them.** They are our best guess,
> offered because a wrong-but-consistent rendering is still better than the tool inventing a
> different one every line — not because they are authoritative. If you read Gujarati, you are
> better qualified to fix this file than anyone who has touched it so far.

---

## Getting the captions on screen

The tool serves a **transparent overlay** at:

```
http://<the machine's address>:8765/?overlay=1
```

Point any browser source at that URL and it will composite over your video with a transparent
background. The server listens on all network interfaces, so this works from another machine.

**vMix** — add a **Web Browser** input with that URL, and place it on an overlay channel.

**OBS** — add a **Browser Source** with that URL, 1920×1080.

**ProPresenter** — add a **Web** prop pointing at that URL. There is also an optional
ProPresenter Messages path; start the tool with `--propresenter`.

**A plain screen** — just open the URL fullscreen in a browser.

**YouTube closed captions** — the tool can also push captions straight to YouTube as real
toggleable CC, with a stream key, entirely separately from any of the above. Each feed has its
own target language and timing offset. See the Outputs tab.

> ⚠️ **vMix's browser input is Chromium 51** — a 2016 browser, not the modern one you are
> reading this in. The overlay is deliberately written to stay inside what it supports. If you
> modify the overlay's CSS or JavaScript, test it in vMix and not only in Chrome.
>
> ⚠️ **vMix refuses any browser URL containing a comma.** If your overlay URL has settings in
> it, make sure none of them are comma-separated.

---

## What the operator sees

Open <http://localhost:8765/> without the `?overlay=1`.

- **Live** — start and stop, pick the microphone, watch the audio level, see captions as they
  go out, flip the language direction mid-session.
- **Transcript** — everything said so far, with a divider at each direction change.
- **Rules** — a substitution list applied to every line, for fixing a mishearing the same way
  every time. Different from the glossary: rules are find-and-replace on the output, the
  glossary briefs the translator before it writes.
- **Outputs** — YouTube CC destinations.
- **Reprocess** — re-caption a past YouTube broadcast (see `REPROCESS_VOD.md`).
- **Settings** — caption size, position, how many lines, colours, timing.

**Three states, not two.** The operator page distinguishes *idle* (nothing running) from
*disconnected* (something broke). A stopped tool never looks like a broken one, and a browser
tab opened during an outage shows the fault rather than an innocent blank screen.

---

## Settings

All in `.env`. Copy `.env.template` to get started; every line there is commented.

| Setting | Default | What it does |
|---|---|---|
| `SARVAM_API_KEY` | — | Required. Does the listening. |
| `CAPTION_TRANSLATOR` | `gemini` | `gemini` or `sarvam` (= Mayura). See the warning above. |
| `GEMINI_API_KEY` | — | Required if you chose `gemini`. |
| `GEMINI_MODEL` | `gemini-3.1-flash-lite` | `gemini-2.5-flash` no longer exists and returns 404. |
| `GEMINI_TIMEOUT_SEC` | `5.0` | Past this, Mayura answers instead. 2.5s was tried and cut healthy calls short. |
| `GEMINI_CONTEXT_LINES` | `3` | How many previous lines the translator sees, for pronouns and topic. |
| `CAPTION_GLOSSARY` | `glossary.json` | Where the glossary lives. |
| `CAPTION_INTERRUPT_PRESSES` | `3` | Ctrl+C presses within 2s needed to exit. `0` refuses every interrupt. |
| `DEFAULT_SOURCE_LANG` | `gu-IN` | So a fresh machine never starts on a guessed language. |
| `DEFAULT_TARGET_LANG` | `en-IN` | |
| `APP_NAME` / `ACCENT_COLOR` | — | Branding on the operator page. |
| `YOUTUBE_STREAM_KEY` | — | Only for the YouTube CC path. |
| `PP_HOST` / `PP_PORT` | `127.0.0.1:49566` | ProPresenter, only with `--propresenter`. |

> **Never commit your `.env`.** It is already in `.gitignore`. The same goes for any log file
> — a caption log is a complete transcript of what was said.

---

## Running the tests

```bash
uv run pytest -q          # 191 tests, about 18 seconds
cd web && npx vitest run  # 160 tests, under a second
```

No network, no API key and no microphone are needed. A five-minute silence in the tests costs
about a second of real time, because the clock is faked.

If you change anything, run these. They exist because this tool ran in front of a congregation
and several of the faults they now catch were found the hard way.

---

## Known problems

Listed honestly. These are open at the time of writing and are **not** fixed.

- **Closing the server's console window still kills the captions**, and always will — the
  operating system does not let a program refuse that. This is what actually took the captions
  down during the live event, so run headless where you can.
- **On Windows, refusing Ctrl+C is not yet proven to save the captions.** The server itself now
  refuses it, and that is tested with real signals on macOS and Linux. But a Windows console
  control event goes to *every* process attached to the console, and if you start the tool
  through `start-captions.bat` there is a chain — batch file, then PowerShell, then Python.
  Python holding its ground does not stop `cmd.exe` asking "Terminate batch job (Y/N)?". Treat
  that console window as untouchable while captions are live, whatever the setting says.
- **vMix restores its stored overlay URL when it restarts**, silently reverting any settings
  you put in that URL — and re-saving from vMix overwrites your change. If your caption
  settings keep reverting after a vMix restart, this is why.
- **`start-captions.bat` and `register-startup-task.ps1` have never been run for real.** See
  the Windows section.
- **Nobody who reads Gujarati has reviewed the glossary.** See the glossary section. This is
  the largest open risk in the whole project and it is not a software problem.

---

## Things that were tried and do not work

Recorded so nobody spends the days again.

- **Azure AI Speech for continuous Indic discourse** — 10–25 second delay before a final
  result. Unusable live.
- **Google `latest_long` for live streaming** — the same latency problem.
- **`websockets` version 14 or newer** — the first transcribe call hangs and the queue
  overflows. The pinned version is pinned for a reason.
- **Running the audio capture in Docker Desktop for Mac** — Docker cannot reach the host's
  audio devices.
- **Replacing the whole screen with each new caption** — readable in testing, unreadable in a
  hall. People need the previous line to still be there.
- **Trimming the front of the queue when it gets long** — puts a sentence on screen with its
  beginning missing, which reads as nonsense rather than as a delay.
- **Checking the overlay from a hidden browser tab** — a background tab reports sizes that are
  not what the visible one is doing. Verify on the actual screen.
- **`SetBrowserURL` to repoint a vMix browser input** — not a function in vMix 26.
- **Trusting `Register-ScheduledTask` because it printed no error** — see the Windows warning.
- **A console window as proof the server is up** — it is a thing that looks alive while
  nothing is happening.

---

## How it works, briefly

```
microphone (16 kHz mono)
        │
        ▼
live_captions.py ──► Sarvam streaming WebSocket ──► Gujarati text
        │
        ├─ gu → en with Gemini : Sarvam transcribes, Gemini translates (two hops)
        ├─ gu → en with Mayura : Sarvam transcribes and translates in one hop
        └─ other pairs         : Sarvam transcribes, Mayura translates
        │
        ▼
a small web server on port 8765
        ├── /              → the operator page
        ├── /?overlay=1    → the transparent overlay
        ├── /api/*         → devices, start, stop, direction, rules, outputs
        └── /ws            → captions and status pushed to every open page
```

Choosing Gemini changes the shape of the pipeline, not just which call is made: an outside
translator needs the Gujarati text to work from, so the tool stops using Sarvam's
translate-as-you-transcribe shortcut and records the source as well.

`live_captions.py` is one large file. It holds the web server, the Sarvam client, the
translator ladder, the sentence assembly and the output fan-out. Splitting it up would be a
good idea and has deliberately not been done here, so that what you receive is the code that
ran a real event rather than a rewrite of it.

---

## Changing it

[`docs/how-it-works.md`](docs/how-it-works.md) is the deep end: the architecture, the decisions
that are load-bearing, how a caption is assembled and put on screen, and the faults that have
already been paid for once. Read it before changing anything in `live_captions.py` or
`web/src/`.

[`AGENTS.md`](AGENTS.md) is the short version of all of it, written for a coding agent working
in this repository — and it is a decent orientation for a person too.

[`CONTEXT.md`](CONTEXT.md) is the vocabulary — what a *caption*, a *line*, a *final*, the
*overlay* and *on air* each mean here. The words are used precisely and it is worth five
minutes.

`docs/adr/` holds two decisions written down at the time, including why devotional vocabulary
is transliterated rather than translated.

---

## Licence

Apache 2.0. See [LICENSE](LICENSE).
