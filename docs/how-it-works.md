# How Live Captions works

**The deep end.** [`README.md`](../README.md) is what you need to *run* this tool. This file is
what you need to *change* it: the architecture, the decisions that are load-bearing, how a
caption is assembled and put on screen, and the faults that have already been paid for once.

Almost everything here was learned by running the tool in front of a live congregation for 28
hours. Where a number looks arbitrary, there is usually a measurement behind it, and the text
says which.

## What this is

A real-time multilingual caption tool for live events. Captures USB audio
on a Mac (or Linux host), streams it to Sarvam AI for speech-to-text
(plus optional translation), and renders captions to:

- a browser overlay (`/?overlay=1`) for ProPresenter Web Capture / OBS
- one or more **YouTube CC** streams (parallel, each with its own target
  language)
- a ProPresenter Message (optional, via `--propresenter`)
- caption sidecar clients (e.g. Raspberry Pi displays) that auto-discover
  the server over mDNS

Also includes a **VOD reprocessing** pipeline (`tools/reprocess_vod.py`
+ Reprocess tab in the operator UI) that re-captions past YouTube
broadcasts using GCP Speech-to-Text v2 + Cloud Translation v3.

The tool is event/org-neutral — branding (name, accent colour) and
default language direction are driven by `.env`.

## Architecture

```
USB audio (16 kHz mono)
        │
        ▼
live_captions.py  ──── Sarvam streaming WebSocket ────►  text
        │      ── derive_pipeline(source, target) ──►
        │         Indic→en-IN  : Saaras mode=translate (1 streaming call)
        │         same==same    : Saaras mode=transcribe (no MT)
        │         other         : Saaras transcribe + Sarvam Mayura POST /translate
        ▼
aiohttp app (port 8765)
        ├── /                → React operator UI (SPA built from web/, served from web/dist)
        ├── /?overlay=1      → transparent caption overlay (same SPA, chrome stripped)
        ├── /api/config      → branding + defaults + lang matrix (fetched once on boot)
        ├── /api/*           → JSON REST: devices, start, stop, direction, rules, outputs, vod
        ├── /ws              → broadcast bus: captions + status + connection
        │                      state + log records
        └── (mDNS)           → advertises `_captions._tcp.local.` for sidecar auto-discovery
```

### Key load-bearing decisions

1. **Single monolithic `live_captions.py`.** All server logic — Sarvam
   plumbing, audio capture, mDNS, YouTube CC fan-out, ProPresenter
   integration, and VOD pipeline glue — lives in one file. Don't try
   to split it without a strong reason; the cohesion is intentional
   (one process, one state, shared in-memory broadcasts).

2. **Single SPA at `/`.** The React app from `web/dist/` is the only
   UI surface. Overlay mode is `/?overlay=1` (same bundle, chrome
   stripped client-side). Branding, language matrix, and defaults
   arrive through `/api/config` — fetched once on boot, applied to the
   store before the React tree mounts.

3. **Sarvam, not Azure/Google/Whisper.** Sarvam Saaras is used because
   `high_vad_sensitivity=True` fires after 0.5 s of silence — critical
   for continuous discourse (lectures, sermons). Azure and Google
   `latest_long` were both unusable (10–30 s finalisation latency).

4. **websockets pinned `<14`.** sarvamai ships the legacy
   `websockets` module; 14+ changed send-flow semantics and the first
   `ws.transcribe()` call hangs forever on 16.0. Keep the pin.

5. **`web/dist/` is the production frontend.** `live_captions.py`
   serves files directly out of `web/dist/`. After editing anything in
   `web/src/`, you MUST run `pnpm build` and restart the server.

6. **This fork pins the default direction to `gu-IN` → `en-IN`.**
   Upstream ships neutral (`en-IN`/`en-IN`) and expects every deployment
   to override via `.env`. This fork does not: it exists for one event
   with one fixed direction, and a first-ever boot with no `.env` and no
   browser state must not land on a guessed pair. Still overridable by
   `.env` — only the fallback changed. This is a deliberate divergence
   from upstream, not drift.

7. **YouTube CC = one Saaras session, N Mayura fan-outs.** Each enabled
   feed in `outputs.json` triggers a parallel `POST /translate` call
   per FINAL. Adding feeds doesn't multiply the Saaras quota cost.

8. **Branding is two env vars.** `APP_NAME` (browser title + header)
   and `ACCENT_COLOR` (highlight + focus + chip border, via a single
   CSS variable). No org-specific assets baked in.

9. **A frozen caption is a lie, so the connection state is broadcast.**
   Every surface on the `/ws` bus is told the state of the link to the
   speech service, and blanks itself when it is down. See below.

10. **A caption is a sentence, not a VAD segment.** Saaras finalises on
    a pause, so a speaker who draws breath mid-sentence produces two
    finals that are each a fragment. Translating a fragment is where the
    English goes wrong, and Gujarati makes it worse than most: it is
    verb-final, so a fragment cut before the verb has no predicate in it
    and the translator invents one. `SentenceAssembler` holds fragments
    until they form a sentence, then translates once. This costs latency
    on purpose — the screen is read, not conversed with.

### Sentence assembly

Four things release a held sentence: terminal punctuation (`. ! ? । ॥`),
a quiet gap, a total-wait ceiling, and a character ceiling. The last two
are what keep it live — a speaker in full flow yields neither punctuation
nor a pause, and the hall still needs words.

| Env var | Default | What it does |
|---|---|---|
| `CAPTION_SENTENCE_MODE` | `on` | `off` restores the old per-utterance behaviour exactly. The revert path — no code change. |
| `CAPTION_SENTENCE_MAX_WAIT_SEC` | `8.0` | Hard ceiling on how stale a caption may be. |
| `CAPTION_SENTENCE_QUIET_SEC` | `2.5` | Silence after which the speaker is judged to have stopped. |
| `CAPTION_SENTENCE_MAX_CHARS` | `180` | Two lines' worth. A READABILITY ceiling, not a storage one — 220 permitted captions needing ~13s to read in a ~4s slot. Raising it re-opens the fault. |
| `CAPTION_SENTENCE_MIN_WORDS` | `5` | Below this, a full stop is treated as a VAD artefact, not a sentence end. |

**These defaults are measured, not guessed.** Replaying a real 336-second
Vachanamrut reading (138 segments, captured on the mandir PC) through the
assembler:

| Setting | Captions | Median words | ≤2 words | Median lag |
|---|---|---|---|---|
| mode off (previous behaviour) | 138 | 2 | **53%** | 0s |
| quiet 1.2 / wait 4 (first guess) | 93 | 3 | 33% | 1.3s |
| **quiet 2.5 / wait 8 (default)** | **69** | **5** | **19%** | **2.5s** |
| quiet 3.5 / wait 10 | 60 | 5 | 12% | 2.9s |

Two things that recording changed, both of which invalidated a guess:

1. **The median gap between segments of one continuous sentence was 1.5s.**
   The first-guessed 1.2s quiet threshold therefore released mid-sentence
   almost every time. Real between-sentence pauses ran 4.5s and up.
2. **Punctuation is not evidence of a sentence.** 90 of 138 segments ended
   in a full stop and 49 of those were two words or fewer — `સત્ય.`,
   `ભ્રમ.`, `માથ.`. Hence `CAPTION_SENTENCE_MIN_WORDS`.

⚠️ Tuned on **one** reading, at lectern pace with long deliberate pauses —
not katha delivery. Continuous speech has shorter gaps, so the same numbers
will join more aggressively; `CAPTION_SENTENCE_MAX_CHARS` is the guard.
Re-check against real katha audio.

**Publishing never blocks capture.** A released sentence starts its own
short-lived task, serialised by a lock so captions stay in the order they
were spoken. Nothing awaits the translator inline. The reason is measured:
with the emitter awaited inside the audio sender, a 7.5s translate cost
**3.5 seconds of the speaker's audio** — the capture queue holds ~4s and
drops the oldest frame, so speech was discarded before it was ever
transcribed, absent from the record with nothing in the output showing a
gap. `tests/test_emit_does_not_block_audio.py` fails without the fix.

Deliberately **not** a long-lived consumer. Two were tried — a ticker, then
a queue-and-publisher — and both perturbed the event loop's ready queue
enough to delay the message loop's discovery of a dropped connection. A
task that exists only while a caption is in flight costs nothing when
nobody is speaking. `CAPTION_SENTENCE_DRAIN_SEC` (default 10s) bounds how
long a closing session waits for in-flight captions.

The release timer is driven by the **audio sender**, once per frame, not
by a task of its own. Frames keep arriving whether or not anyone is
speaking, so they are a free heartbeat; the message loop cannot do the
job because it only wakes when Sarvam sends something, and a speaker who
has stopped sends nothing. A dedicated ticker task was tried and
reverted — an extra task in the ready queue measurably delayed the message
loop's discovery of a dropped connection, which is the worse fault.

### The rolling line scroll

Captions used to append into a rolling ticker, which was correct when a caption
was a fragment — before sentence assembly the median was 2 words and 53% were
two words or fewer, so `પણ` → "But" alone said nothing and several had to run
together to read.

**Sentence assembly changed that premise and the display was not changed with
it.** A caption became a whole sentence, median 9–12 words on this speaker, so
appending stacked whole sentences. Measured live during katha: a caption that
arrived at 09:32:27 was still the first thing the eye landed on at 09:33:13 —
the left edge of two wrapped lines running 45–60s behind.

🔴 **No timing measurement could show this.** The newest words were current to
within 4s; they sat at the far end of a block that began a minute earlier, and
a reader starts on the left. The fault reads exactly like latency and is not
latency — both people who looked at it went hunting for a queue that was not
there.

The fix for that was a one-deep pipeline of two-line BLOCKS, each replacing the
last wholesale. It held the lateness bound and it was wrong in a different way:
the eye finished line two, the screen blanked and refilled, and nothing carried
over — in a discourse where every sentence leans on the one before it. It also
dropped 24 of 690 sentences on Day 3 (3.5%), because one caption in reserve has
nowhere to put a burst.

🔴 **The unit of the display is now a LINE, and the screen SCROLLS.** The owner
settled it on Day 3 after watching the block model in the hall: *"What i want
Is three lines of text / 2 on the screen and 3rd 1 is on the queue and 4th one
is being constructed ... I want the text to scroll up in a queue as the next
line is generated / Only full line should be pushed upwards"*.

So: two lines visible, one complete line queued, and text still building into
the next. A line enters at the BOTTOM and the oldest falls off the TOP, one at a
time — that is the scroll, and it is why the previous line is still in front of
the reader while the new one arrives.

🔴 **All four lines are RENDERED; two of them are off screen.** The queued line
and the line being built are real rows in the column, below the clip. `fit.ts`
→ `columnRows()` owns that arithmetic and is where to change it.

Why they are rendered at all, when the hall cannot see them: the "still
speaking" indicator has to ride the line being BUILT. It shipped on the bottom
VISIBLE line for one katha, which is the sabha watching a sentence assemble
itself — the exact thing spec #56 user story 3 forbids. There was no off-screen
row for it to ride, so it fell back to the last row that existed.

⚠️ **The visible slots are PADDED to `maxLines` even when the screen is not
full.** A column that only holds the lines it has puts the building row inside
the clip whenever the screen is half full — one line into a katha, and after
every silence clear. A full screen never exercises it and no demo shows it.
There are tests over every line count 1–6 against every fill 0–`maxLines`;
delete the padding and three of them fail.

🔴 **A half-built line NEVER reaches the screen.** The sabha reads whole lines
appearing, never words assembling themselves. A line is complete when it fills
the measured width OR when the text building it ends a sentence — the owner
settled the second: *"Push the short 1 up"*. Neither waits for the other.
`LINE_ORPHAN_MS` (2.6s) releases text stranded in `building` when the server
released a caption mid-sentence on its max-wait ceiling and the speaker then
stopped; without it the display would repeat the old silence clear's fault of
only firing once the awaited thing had happened.

⚠️ **The queue is BOUNDED and the bound is the safety property.**
`MAX_QUEUED_LINES` (4) is a ceiling, not a target — roughly six seconds of
lateness at the dwell floor. A buffer allowed to grow is the Day 2 Morning fault
rebuilt deliberately.

🔴 **What gets dropped at the bound is a WHOLE SENTENCE, never part of one.**
The obvious implementation trims the front of the line queue and is wrong in the
worst available way: the server caps a caption at 180 characters and a line
holds about fifty, so one long sentence can be four lines, and trimming the
front would put it on the hall screen starting from its second line — fluent,
confident, beginning in the middle, with nothing to tell anyone. That exact
fault has shipped here once already (see the line trim below). Two sentences are
never dropped: the one that has just arrived, and the one already part-way onto
the screen. Queue entries carry a caption id for no other reason.

⚠️ **The dwell floor is PER LINE and that is not the same number as before.**
`LINE_MIN_DWELL_MS` / `settings.lineMinDwellSec` is 1.5s. The old floor was 2.0s
for a two-line BLOCK; kept per line it would make a two-line sentence take 4.0s
against a 3.83s cadence and the screen would slide behind by the difference on
every sentence — Day 2 Morning rebuilt by arithmetic. 1.5s is the broadcast
minimum for one line and sits under the ~1.9s per-line arrival interval.

**The safety check measures the interval, it does not assume it.** `arrivalGaps`
records the gap between real arrivals **divided across the lines that arrival
produced**, and the toolbar warns when the floor gets within 20% of the median.
Measuring captions instead would overstate the interval by however many lines
they carry, which is the dangerous direction — it makes an unsafe floor look
safe. Gaps longer than the silence threshold are excluded: they are pauses
between passages and drag the estimate upwards too.

**`captionMaxDwellSec` is ONE setting for the maximum dwell and the silence
clear.** They are the same question — how long before a caption stops reading as
current and starts reading as stuck. Two knobs for one question can disagree,
which is exactly how the line count and the box height ended up clipping text at
each other.

The timing knobs are settings, not module constants: the overlay is a separate
browser instance configured entirely through the minted URL, so a knob that is
not a setting cannot be changed on the day without a rebuild under a live
browser input. **Add a setting and you must add it to `buildOverlayUrl` AND to
`applyOverlayParams`, in both of its states**, or it silently reverts to default
on the hall screen. `applyOverlayParams` is exported and pure precisely so the
round trip can be asserted — while it lived inside `loadSettings()` reading the
module-level query string, nothing could test it, and a setting that minted only
when ON shipped twice.

🔴 **A caption too long for the space becomes MORE LINES. Never fewer words.**

The old line trim dropped the OLDEST rendered line to make text fit. That was
right when a caption was a rolling ticker of fragments and wrong once one
caption was one whole sentence: it removes the START. Watched on a real screen,
the hall read *"own hand, and set down in it the conduct expected of..."* — a
sentence beginning halfway through, with no subject, and nothing anywhere saying
a word had been lost. Losing the head is worse than losing the tail, because a
reader can guess an ending and never a subject. **The trim is gone**: every item
the splitter produces is exactly one line by construction, so there is nothing
to trim.

⚠️ **`store.splitCaption` is INJECTED, and the default is the wrong answer.**
The renderer installs a splitter that lays the real words out in a box with the
real font at the real width and adds words until they stop fitting — **cutting
at ONE line, not at `maxLines`**, because the store scrolls what it returns onto
the screen one item at a time and a two-line item would move the screen by two.
The store's own fallback divides by a character budget, kept only so the store
stays testable headlessly and the first caption of a session gets something.

**Do not replace the injected splitter with arithmetic.** Three attempts at
that failed on the real screen and passed every test:

| attempt | why it was wrong |
|---|---|
| "about 90 characters per line" | measured at the mandir PC's layout, applied to a different one. The shipped default holds about half that. |
| box width ÷ average character width | overstates capacity — real text breaks at word boundaries, so every line ends ragged |
| any character count at all | wrong unit. "Bhagvan Swaminarayan wrote the Shikshapatri" is far wider than the same number of characters of short common words |

**The scroll is a transform on the whole column, and the RESTING position is
correct with no transition at all.** The column holds one extra line at the top
— the one that has just left — and rests translated up by exactly one line box.
A new line puts it back to 0 with no transition, then animates back. If
`transition` is ever unavailable the position still lands where it belongs and
the scroll degrades to an instant reposition; it never degrades to a blank box
or text stranded mid-slide. That property is why it is one transform on a column
rather than a per-line animation. `transform` and `transition` are both fine
unprefixed on Chromium 51.

`prefers-reduced-motion: reduce` takes that same degraded path — same lines,
same place, no movement. The query is Chrome 74 and vMix runs Chromium 51,
where `matchMedia` exists but does not know the feature and reports
`matches: false`; that is the answer we want there, so the guards in
`prefersReducedMotion()` must never default to `true`. Defaulting the other
way would silently kill the scroll on every machine that cannot answer.

🔴 **`#stage` carries a `data-captions` attribute** with the line count,
character budget, how many lines are on screen, the queue depth and the dropped
count. It is on the DOM rather than a `window` global on purpose — globals set
by the page are invisible to an extension or automation tool running in an
isolated world, which is exactly the situation it was first needed in. The
overlay runs unattended in Bolton and "what does it think is on screen, and what
is it holding?" has no other answer from outside. Read it, do not delete it.

🔴 **The caption text is TOP-ALIGNED in the area, and that is load-bearing.**
Measured on a real page across a run of one-line, two-line, short and long
captions: the text's top-left stays at a single pixel position and the fixed
panel keeps one position and one height. The scroll is meant to be the ONLY
thing that moves. Vertically centring the text inside the panel would look
tidier for a one-line caption and would make the whole block jump as the line
count changed. Do not "improve" the alignment.

🔴 **`/api/overlay-status` is how you ask a hall screen what it is doing.**
`curl` it. Each surface reports what is on screen now, the SEQUENCE of the last
twelve lines displayed, the measured character budget, queue depth and dropped
count. It exists because the overlay runs inside vMix's browser input on an
unattended machine where nobody can open devtools, so the `data-captions`
attribute — readable in a browser, useless from a terminal — could not answer
it. `shown` is the sequence and `on_screen` is only the snapshot: the board
reports every 5s while lines change every second or two, so a snapshot alone
will always miss some of them. I got a verification wrong this way before adding
`shown`.

🔴 **The `capture` block is the other half, and without it the board can read
healthy while the hall has nothing.** The surfaces can only report what they
were GIVEN. If the overlay is fine and the AUDIO has stopped, every field on
the surface side looks correct — that is how a 37-second caption gap went
undiagnosed from outside. `capture` describes the input: whether a session is
running, the device or file feeding it, when audio was last seen, when speech
was last heard, when a caption was last published, the queue depth and the
chunks dropped.

**`verdict` and `why` come first in the response and are the whole point.**
`capturing` / `silent` / `not running`, plus one sentence naming what to go and
check. The reader is a diagnostician inside vMix on a locked Windows box who
cannot open devtools and cannot read this file; they should not have to
interpret a single other field, and the output should be pasteable straight
into the tracker.

**Every "when did X last happen" carries a derived `_sec_ago`.** A raw epoch
timestamp is not an answer to someone reading JSON on a phone at the back of a
mandir, and the arithmetic is exactly what a person under time pressure gets
wrong. Same reasoning as `stale` on the surface side.

🔴 **Loudness is measured in the audio PUMP, not in the sender, and moving it
back breaks the diagnosis.** The sender only runs inside a live speech-service
session. Driving the real endpoint with a bad API key showed the consequence:
a source delivering twenty seconds of loud tone reported "nothing above the
silence gate" and the verdict read `silent` — sending the reader to the mixing
desk for a fault that was in the network. Whether there is SOUND in the audio
is a property of the input and must not depend on anything downstream being
reachable. `test_a_dead_speech_link_is_not_reported_as_a_dead_microphone`
fails if it moves.

⚠️ **This endpoint must never be able to take the server down.** It is a
diagnostics path on a live production process, and a status endpoint that 500s
during a fault is worse than none. Every field is optional, every read is
guarded, and a failure to build the answer is reported *in* the answer.

**`tests/test_capture_status.py` has two halves and both are load-bearing.**
The verdict cases drive the handler over hand-built state, because a mic that
died ninety seconds ago cannot be produced on demand. `test_the_real_capture_
path_is_what_fills_the_board` drives the REAL supervisor and asserts the same
fields — without it every other case would pass against fields nothing in the
pipeline ever writes, and the board would report a permanently healthy silence.

🔴 **Two panel shapes, and only ONE paints at a time.** `panelFixed` draws a
single rounded rectangle sized to hold exactly `maxLines`, always the same size
and in the same place; without it the background paints on the text run and
hugs each rendered line. Painting both at once doubles the opacity behind the
words and reads as a dark smear. The fixed panel is the owner's ask — *"a
permanent rounded border 2 line border"* — and its point is that it does not
move: it must NOT shrink for a one-line caption. `border-radius` and `rgba()`
are both safe on Chromium 51; `inset` is not, which is why the panel is
positioned with left/top/width.

**The panel ships ON, two lines tall, at 25%** — *"i need 25% capacity block
background"*, the owner, 2026-09-03. It was built OFF by default and had
therefore never once been on air: the URL that carried it reverted every time
vMix restarted, so the hall got the plain build every day.

⚠️ **The headroom is 2.55 lines a caption, and a THREE-line caption already
overruns it.** Day 3 measured a 3.83s median caption gap; the line dwell floor
is 1500ms, so 2.55 lines can go up between arrivals. The measured shape was
~1.9s per line — two lines to a caption — and the soak in `store.test.ts`
("a katha-length run at the cadence Day 3 actually measured") gets through all
690 finals with ZERO drops at that shape. Push the same fixture to three lines
(110 characters — well under the server's 180 ceiling) and it sheds 342 lines
over the same katha.

So the exposure is not the ceiling, it is the AVERAGE sentence length: a
passage of longer sentences sheds captions whole while the numbers on the
status board all still read healthy. The levers are the character budget (a
wider area or a smaller font buys line capacity) and `lineMinDwellSec` — and
`dwellFloorIsUnsafe()` in `fit.ts` is what already knows when the floor has
outgrown the arrival interval. Do not "fix" this by beheading sentences.

**The panel STAYS UP through silence, and that was a decision.** The panel
renders on `panelFixed && textBgOpacity > 0` alone — it does not ask whether
there are any lines — so after the silence clear the hall is looking at an
empty translucent rectangle until the next caption arrives. That is deliberate
and it is the whole reason the shape is fixed: a panel that vanished and
returned would flash on every pause between passages, which is more movement
than the scroll it was built to replace, and it would move the hall's eye off
the place the words appear. The cost is real — a panel over an empty stage on
a long pause — and the operator's lever for it is `textBgOpacity=0` in the
overlay URL, not code. If this is ever revisited, it needs the owner: it is
what he sees, not what the code prefers.

**The clip box is `maxLines` line boxes, NOT `areaH`.** They are different
numbers — `areaH` is the operator's box and holds 3.4 lines at the medium
preset while the panel covers 2 — and the difference was invisible except
during the 220ms of a slide, when the arriving third line sat inside the clip
and below the panel, painting on bare background. The panel and the clip are
sized from the same `maxLines` for that reason, and `fit.test.ts` asserts it
for every preset plus the live mandir config.

**The reserved zone costs width now, and it did not before.** Under the block
model text wrapped around the block and the rows above and below it kept the
full width. A scrolled line cannot do that — it is one `nowrap` div, and a line
cut to fit a wide row would overflow the moment it scrolled into a narrow one.
So the scroll takes the wider clear side of the block and stays there.

With the SHIPPED default (area 1280 wide, a 400px block centred in it) that
leaves 440px — about 17 characters a line. Correct, and unusable. The live
mandir PC does not hit this: its URL carries `noblock=1`. **An operator turning
the zone on under the scroll should move the block to one side of the area, not
the middle.** A zone leaving less than four characters' width is ignored
entirely, because a blank caption bar is the one thing this never degrades to.

🔴 **The text panel is `rgba()`, never `#RRGGBBAA`.** Eight-digit hex arrived in
Chrome 62 and vMix's browser input is **Chromium 51**, where it fails silently —
no error, no background, and nobody finds out until the hall cannot read the
captions. `textPanelCss()` in `settings.ts` is the only place that builds it, and
a test fails if it ever emits hex.

🔴 **`settings.lines` is a PREFERENCE, not an instruction.** The renderer derives
`renderedLines()` from `src/fit.ts` — the smaller of that preference and what the
box can actually hold — and tells the store with `setMaxVisibleLines`. They used
to be independent settings with nothing reconciling them, so when the configured
count needed more vertical space than the area had, the box quietly cut the
bottom off — the NEWEST line, the one that mattered most. It told nobody, and the
owner saw it from the hall as captions "cutting off".

Everything in `fit.ts` is a pure function of numbers: no DOM, no font metrics,
no globals. That is deliberate — it is the half of the clipping fix that can be
proven headlessly, kept apart from the `Range.getClientRects()` measurement,
which genuinely needs a browser and stays covered by typecheck and build.

`CAPTION_CLEAR_AFTER_MS` (12s) takes the captions down once the speaker stops,
and it is a **timer**. It used to be checked only when the next caption arrived,
so the one case that matters — there is no next caption — was the one case it
could never fire on, and the last sentence of a katha stayed up through the
bhajan and everything after. A clear empties the queue and `building` too: a
line held back belongs to speech that has finished, and scrolling it on
afterwards puts a stale sentence back on the hall screen with nobody speaking.

**The DECISION lives in `tickCaptionClock` on the store; `CaptionRenderer` only
supplies the 250ms heartbeat.** Keep it that way. Putting the rule back in the
`useEffect` puts it somewhere nothing can assert it, which is how it shipped
broken the first time.

### Translator backends

Mayura is competent and fast, but it takes no prompt — there is no way to
tell it that this is a devotional discourse, that કથા is the recital of
scripture and not a "story", or how the swami's name is spelled. On the
first live sample it rendered `ભગવાનની કથાનો આરંભ કરીએ છીએ` as "the Lord":
the central noun vanished. A general model can be told all of that, on
every line. That is why there are two backends; speed is a constraint on
the second one, not its purpose.

`translate_line()` is the single door. Three rungs, and the bottom one
still puts words on the screen:

1. **Gemini** — the default since 2026-09-01, on measured evidence. Carries
   the brief, the glossary and the last few lines as context.
2. **Mayura**, always available, and the fallback whenever Gemini returns
   nothing (timeout, safety block, HTTP error, empty candidate).
3. **The source text**, which is what Mayura itself falls back to.

**Choosing an external translator changes the pipeline, not just the call.**
Saaras's one-call shortcut translates as it transcribes, so the source text
never exists — fine for Sarvam's own translator, useless for any other,
which has nothing to work from. This shipped broken: `CAPTION_TRANSLATOR=gemini`
was a no-op on the default gu→en direction because the fan-out had nothing
left to translate, and the captions looked entirely plausible throughout. The
startup log now states which translator is **really** in the path rather than
which one was asked for, and says so at ERROR level when they differ.

A blank caption bar in a full hall is the one outcome with no recovery, so
no rung is allowed to return nothing.

| Env var | Default | What it does |
|---|---|---|
| `CAPTION_TRANSLATOR` | `gemini` | `gemini` or `sarvam`; **validated at read** — anything else logs an ERROR and falls back to `gemini`. **Choosing gemini forces the two-hop pipeline**, because an external translator needs the source text — so the Gujarati is recorded too. |
| `GEMINI_API_KEY` | — | Read from `.env`. **Never** committed, whatever the repository's visibility. |
| `GEMINI_MODEL` | `gemini-3.1-flash-lite` | Measured live. `gemini-2.5-flash` **404s** — "no longer available to new users". |
| `GEMINI_MAX_OUTPUT_TOKENS` | `256` | Stops a runaway answer holding the caption bar. |
| `GEMINI_TIMEOUT_SEC` | `5.0` | Hard cap; past it, Mayura answers instead. 2.5s was a guess and cut healthy calls. |
| `GEMINI_CONTEXT_LINES` | `3` | Prior lines offered for pronoun/topic continuity. |
| `CAPTION_GLOSSARY` | `glossary.json` | Domain brief + term renderings. Editable without touching code. |

**One unrecognised value used to give three different answers.** `CAPTION_TRANSLATOR`
was a raw string read by four places independently, with no allowlist. Set
`gemeni` — one letter wrong — and `derive_pipeline` read "not sarvam" and forced
the expensive two-hop path, `translate_line` read "not gemini" and went straight
to Mayura, and the startup log read "not sarvam, key present" and announced
*"Translator: Gemini gemini-3.1-flash-lite"* — the exact line the mandir PC uses
to verify the switchover. You paid for Gemini, got Mayura, and the check passed.
It is now validated where it is read, so every reader sees the same value.

The fallback is deliberately *not* a hard exit: this is read at import, the
mandir launches the server by double-clicking a `.bat`, and a server that
refuses to start is invisible there and indistinguishable from one that did not
work. A running server on the documented default still captions the katha.

**The fan-out log line named the wrong translator too.** It was a hardcoded
`"Mayura fan-out"` printed whichever rung answered, and it cost real time —
the mandir PC saw "Mayura" at Gemini latencies with zero fallbacks logged and
reasonably concluded, mid-katha, that the briefed translator might not be in
the path. It now names the translator actually configured.

`thinkingConfig.thinkingBudget` is pinned to 0 and `temperature` to 0. A
caption that is right but arrives after the speaker has moved on is not
right.

**Three things only a live call revealed, all of which the first version got
wrong:**

- **`gemini-2.5-flash` returns 404** — "no longer available to new users." It
  was the shipped default and would have failed on first contact at the
  mandir. The fallback ladder held, so it would have degraded silently to
  Mayura rather than breaking, which is worse: nobody would have noticed.
- **`thinkingConfig` is model-dependent.** `gemini-3.1-flash-lite` accepts
  `thinkingBudget: 0`; `gemini-3.5-flash-lite` returns a hard 400 on the
  identical field. Nothing documents which. We try once per model, remember
  the answer, and retry without.
- **Safety thresholds are not optional for this material.** The Vachanamrut
  passage is *about* being cursed for hurting a sant or failing one's
  parents. A block returns nothing, which is indistinguishable from a
  timeout, so the hall would lose the line and the log would not say why.
  `describe_gemini_refusal()` names a block, a truncation and an empty answer
  apart — over four hours the pattern is the diagnosis.

⚠️ **Observed latency on the free tier ranged 544ms to 2670ms** — the upper
end was past `GEMINI_TIMEOUT_SEC` when it defaulted to 2.5s, so the fallback fired
on a healthy call. Whether that is free-tier throttling or the model is not
yet known. The default is now **5.0s** — do not read the paragraph above as
describing current behaviour. Note 5.0s is itself above the ~4.0s gap between
captions at katha pace, so a run of timeouts costs more time than it has.

**The glossary is the part worth maintaining.** It is the only place where
someone who knows the vocabulary — and not the code — can improve the
output, which is why it is a plain JSON file and not a constant.

**`glossary.json` is TRACKED; `glossary.example.json` remains the empty
schema for a fresh deployment.** It was gitignored for one day, on 2026-08-31,
because this repo was public and the file names the event. Both repos went
private on 2026-09-01 and that removed the reason.

Tracking it is now the point, not a concession:

- **It is the entire quality margin.** Briefed, the model scores 24/24 on
  terminology; cold, the same model makes the cheap translator's mistakes.
  A machine that "has the code" does not have the quality.
- **It was surviving as one untracked file on one Windows PC** with no
  backup and no history. Losing it costs more than the code.
- **It is the artefact a Gujarati reader reviews** (ticket #7). A file that
  cannot be diffed cannot be reviewed, and every rendering in it is a
  developer's guess until someone who reads Gujarati says otherwise.

⚠️ **This is a deliberate second divergence from the branding convention
below**, which says keep the event out of anything the code parses. Decision 6
already pins the default language direction for this one event; this is the
same trade made knowingly, and it holds only while the repo is private. If
this fork is ever made public again, ignore the file again first.

**Unchanged, and not softened by any of it: no keys in tracked files.** The
Sarvam and Gemini keys live in `.env` on the deployment machine only. Private
is not a reason to relax that.

A deployment copies the example, fills it in, or points `CAPTION_GLOSSARY`
elsewhere. An absent file is not an error — the tool runs unbriefed.

### Why Gemini is the default

Decided on measurement, after two earlier readings of the same evidence
turned out to be wrong.

On the Vachanamrut corpus, assembled into sentences and scored against the
published English, with full coverage from both backends:

| | terminology | median |
|---|---|---|
| Sarvam Mayura | 13/24 — 54% | 682ms |
| **Gemini, briefed** | **24/24 — 100%** | 1770ms |

And on six minutes of the speaker's own katha, replayed through the real
pipeline:

| Sarvam | Gemini |
|---|---|
| "Maharaja Shikshapatri is written" | "Maharaj wrote the Shikshapatri" |
| "Write down all your **movements**" | "Write down all your news" |
| "then Start the details" | "he should begin the details" |

⚠️ **The margin is the briefing, not the model.** Asked cold, with no
glossary and no passage, Gemini produced the same errors as Mayura — "We
begin the story of God". Whatever else changes, the glossary is the part
that earns this.

⚠️ **The free tier cannot run this live.** The speaker produces ~15 captions
a minute, which is exactly the free tier's per-minute cap; a six-minute
replay already hit one 429. Live use needs Tier 1. Cost is not the obstacle
— ~£2.50 for a 28-hour katha, measured from real token counts, against ~£8
for the speech-to-text already being paid for.

### Connection state

`{"type": "connection", "state": "connected" | "disconnected" | "idle",
"attempt": n, "reason": null | "closed" | "dropped" | "error",
"retry_in_sec": n}` goes out on every change, and `handle_ws` hands the
current value to each tab as it connects. A broadcast only reaches the
tabs that are open at the time; without the snapshot, a tab opened or
refreshed mid-outage would show an empty caption bar and no reason for it.

Three things hang off it:

- **The overlay blanks.** A `clear` goes out with every disconnect, using
  the verb the bus already had, so any surface on it can blank without
  learning a new message. The hall reading a line that no longer matches
  anything being said is worse than the hall reading nothing.
- **`reason` separates a fault from a tidy exit.** `closed` is the
  service hanging up an idle connection, or `/api/direction` closing it
  on purpose for a flip — both recover in about a second, and painting
  them fault-red would teach the operator to ignore the colour that
  matters. `dropped` / `error` are the real thing.
- **`idle` is not `disconnected`.** Nothing is capturing, so nothing is
  wrong. The supervisor sets it on shutdown.

**Attempt spacing has a floor.** `RECONNECT_MIN_INTERVAL_SEC` (default
`1.0`, read from the environment at import) is measured attempt-start to
attempt-start, not from the end of the previous session — so a session
that dies the instant it opens still costs a full interval before the
next one. Without that, the clean-close path (which resets the backoff by
design, so a flip reconnects promptly) reconnects as fast as the loop can
turn over, against Sarvam's rate limiter. Repeated faults then back off
past the floor, ×1.7 to a cap of 8 s.

### When the server is not there at all

Everything above is about the server's link to Sarvam. The other outage
is coarser and it happened first: **there is no server**. Issue #57 —
98 minutes of nothing, with three things all failing to say so.

- **The log did not exist.** `basicConfig` alone writes to stderr, and
  stderr was a console window nobody kept. `configure_logging()` in
  `live_captions.py` now adds a `TimedRotatingFileHandler` at
  `logs/live-captions.log` (midnight rotation, 14 days), overridable with
  `--log-dir` or `$LIVE_CAPTIONS_LOG_DIR`. It falls back through
  `%LOCALAPPDATA%` and the temp directory, and returns `None` rather than
  raising: a katha with captions and no log beats no katha. `entry()`
  writes a banner with the pid and port on the way in and names the exit
  cause on the way out, so a fortnight's file can be cut at the restarts
  and a Ctrl+C can be told from a crash.
- **The hall screen could not say it.** `main.tsx` composites a "server
  unreachable" card, but it is deliberately suppressed for `?overlay=1`
  — the card is opaque and uses `inset` and flex `gap`, which Chromium 51
  cannot lay out, so on the hall it would be a broken grey box over the
  programme feed. Suppressing it was right; leaving the overlay with
  nothing to say was not. `components/OfflineNotice.tsx` now shows a small
  `CAPTIONS OFFLINE` pill at the caption area's top-left after
  `HALL_OFFLINE_AFTER_MS` (10 s, comfortably past the 1500 ms reconnect
  cycle so it cannot strobe).
- **The desk banner was gated behind `running`** — and `running` is
  exactly what a page with no socket cannot know. A desk that had not
  pressed Start, or that reloaded mid-outage, was shown nothing.

Both decisions live in `web/src/link-health.ts` as pure functions
(`hallNotice`, `deskNotice`) for one reason: the surface that got this
wrong renders inside vMix's Chromium 51, where none of it can be run or
inspected. Anything that cannot be asserted off the browser cannot be
trusted before a katha.

## File layout

```
live_captions.py      Main app (web server + Sarvam streaming + Mayura translate + flip handler + VOD glue)
sarvam_stream.py      Standalone terminal test (no web UI; bypasses Mayura)
tools/
  reprocess_vod.py    CLI for VOD reprocessing
  vod_pipeline.py     Shared pipeline used by CLI + Reprocess tab
web/                  React 18 + Vite + Tailwind + shadcn
  src/                Source
  dist/               Built bundle (gitignored)
.env.template         Starter env file
pyproject.toml        Python deps (uv-managed)
uv.lock               Pinned lockfile
deploy.sh             macOS installer (Homebrew, portaudio, uv, .env)
deploy.ps1            Windows installer + start; see WINDOWS.md
start-captions.bat    Windows double-click start (no typing) - manual fallback
register-startup-task.ps1  One-time: register the Windows logon task (runs headless)
logs/                 Server log, rotated at midnight, 14 days kept (gitignored)
WINDOWS.md            Windows sequence, boot behaviour, and what is still unverified
rules.json            Substitution rules (managed via Rules tab; empty by default)
outputs.json          YouTube CC feed list (gitignored)
vod-jobs.json         VOD reprocess job history (gitignored)
samples/              Test audio (gitignored)
results/              Per-session JSONL/SRT output + VOD output dirs (gitignored)
uploads/              User-uploaded audio for VOD reprocessing (gitignored)
```

## Common dev tasks

```bash
# First-time setup
uv sync
cp .env.template .env
# edit .env, paste SARVAM_API_KEY

# Run the live server
uv run python live_captions.py
# → operator UI at http://localhost:8765/
# → overlay     at http://localhost:8765/?overlay=1

# Run with ProPresenter Message output
uv run python live_captions.py --propresenter

# Different port
uv run python live_captions.py --port 9000

# Build the React UI (after edits to web/src/**)
cd web
pnpm install      # one-time
pnpm build        # produces web/dist/ — restart the Python server to pick it up

# Dev iteration on the React UI
cd web
pnpm dev          # Vite at :5173, proxies /api + /ws to :8765

# VOD reprocess (needs `uv sync --extra vod` first, plus GCP setup)
uv run python -m tools.reprocess_vod --video <YT-URL>

# Standalone Sarvam terminal test (good for sanity-checking a mic)
uv run python sarvam_stream.py

# Run the tests (no network, no API key, no audio device — a few seconds)
uv run --extra dev pytest
```

## Tests

```bash
uv run --extra dev pytest      # Python — the capture, translate and publish path
cd web && pnpm test            # TypeScript — what the overlay shows over time
```

### The web suite

Runs under **plain node, with no jsdom**. That is deliberate. The overlay's real
runtime is vMix's Chromium 51, and a jsdom environment would let a test pass
while leaning on a DOM feature that browser does not have — a false green on the
one surface where a false green means a blank caption bar in a full hall.

So the suite asserts what is honest to assert headlessly:

- **`src/store.test.ts`** — what the store shows over time, given a sequence of
  wire messages and a clock. Messages go through the real `handleWsMessage`, not
  through store actions directly, so the wire protocol is under test too.
- **`src/settings.test.ts`** — that `settings.ts` still hydrates with no
  `location` and no `localStorage`. One new module-level browser read and the
  whole suite stops collecting, so this is worth its own guard.
- **`src/fit.test.ts`** — the clipping rule. Sweeps every font size the toolbar
  allows against every shipped layout and asserts nothing can render taller than
  its area, plus the dwell floor against the measured PER-LINE arrival interval.

⚠️ **These tests cannot see the screen, and the worst fault of this project so
far lived exactly there.** 202 of them passed while the hall was being shown
sentences with their first half missing. Before reporting a display change as
done, run the server and look at it — and check the browser actually loaded the
new bundle (`performance.getEntriesByType('resource')`), because Chrome caches
`index.html` and a stale bundle looks exactly like a fix that does not work.

The DOM measurement and the scroll transform in `CaptionRenderer` stay covered
by typecheck and build only. They genuinely need a browser, and pretending
otherwise would be worse than the gap.

**`src/env.ts` is why any of this imports.** Browser globals are read through it
and answer honestly — `""`, `null` — when there is no browser. Reach for it
rather than touching `location` or `localStorage` at module level again.

**`src/testing.ts`** holds the doubles: `VirtualClock`, mirroring the Python
suite's, so a sixty-second silence costs no wall clock; and `overlayUnderTest()`,
which builds a **fresh store per case** via the exported `captionStore`
initializer. Reaching for the `useStore` singleton instead lets one test's
caption leak into the next — and a caption surviving when it should not is the
exact fault class this suite exists to catch.

pytest + pytest-asyncio, chosen over stdlib `unittest.IsolatedAsyncioTestCase`
because every interesting path here is async and `asyncio_mode = "auto"` keeps
a plain `async def test_…` runnable with no decorator; `pyproject.toml` already
carries an extras group so the tooling adds no cost to a normal `uv sync`.

`tests/` drives the real session supervisor, `sarvam_loop`, end to end. Three
rules hold it together:

- **Two seams, both for the same reason.** `sarvam_loop(..., client=...)`
  takes the speech-service client: production never passes one and gets the
  real `AsyncSarvamAI` built from `SARVAM_API_KEY`; a test passes a fake.
  `sarvam_loop(..., retry_wait=...)` takes the hold between connection
  attempts, which is the one wait here that runs on the event loop's clock
  rather than on `time.time()` — see below. Audio and the broadcaster were
  already injected, so those two parameters run the whole supervisor with no
  network, no key and no microphone.
- **External behaviour only.** Assert what the tool *sends* to the service,
  what it *broadcasts* to browser tabs, and *when*. Never internal call
  sequences or private state — those are what make a supervisor painful to
  change later.
- **Time is virtual.** `VirtualClock` in `tests/fakes.py` is advanced one
  audio frame at a time by the scripted source and installed over
  `live_captions.time`, so a five-minute silence costs five minutes of audio
  timeline and about a second of wall clock. The scripted source is paced by
  the tool's own `level` broadcasts rather than by sleeping, which keeps the
  internal audio queue at depth <= 1 and the frame sequence deterministic.

`tests/fakes.py` holds the doubles for the audio path. The fake service records
every frame with the virtual time it arrived, and can emit transcripts, emit
VAD signals, close cleanly or drop like a network fault — enough to script
silence, reconnection and caption behaviour.

`tests/fakes_connection.py` holds the doubles for the connection: a microphone
that never hands anything over (so nothing but the supervisor moves the clock),
a substitute for the between-attempts wait, a client that records the virtual
time of every connect, and a broadcaster that fans out to real browser tabs as
well as recording.

**The between-attempts wait is a parameter because the virtual clock cannot
reach it.** It is `asyncio.wait_for(stop_event.wait(), timeout=…)`, which runs
on the event loop's clock, not on `time.time()`. `sarvam_loop(...,
retry_wait=...)` is how a test substitutes one that costs no wall clock;
`test_connection_state.py` asserts six attempts' worth of spacing in under a
second. Production passes none and gets `_wait_before_retry`.

**The scripted source races the receive loop, so script the failure, not the
frame it happens on.** `scripted_audio` is paced by the tool's own `level`
broadcasts, which makes the sender and the source a closed loop running as fast
as the event loop allows. Put a real `Broadcaster` with a live WebSocket client
in that path and the receive loop can go unscheduled for the whole script — a
session told to drop on frame 3 will still be handed every remaining frame. A
real microphone paces itself and does not do this (measured: 3 frames, then 37
to the replacement session). A test that mixes real tabs with a scripted drop
should therefore hand the fake session its outcome up front, before the tool
connects, rather than triggering it from `on_frame`.

## Conventions

- **Don't add comments that explain what code does** — only add a comment
  when the *why* is non-obvious (workarounds, hidden constraints,
  surprising behaviour). The websockets `<14` pin and the
  `derive_pipeline()` branching are good examples of comments worth
  keeping.
- **Edit `live_captions.py` in place.** Resist the urge to split it. Many
  internal concerns (Sarvam streaming, audio, web, registries, mDNS) are
  intentionally co-located so they share one event loop and broadcast bus.
- **State files are gitignored.** `outputs.json`, `vod-jobs.json`,
  `.env`, `.gcp-adc.json`, `results/`, `uploads/`, `samples/` are
  per-deployment. Only `rules.json` is tracked (empty by default; serves
  as the schema reference), and a `rules.starter.json` can sit next to
  the script to seed a fresh deployment with a preset dictionary.
- **No org branding in code — this means VALUES, not explanations.**
  Every event-specific name, colour and IP goes through `.env`; a static
  LAN IP or an org name as a default value is always wrong.
  **Comments are the exception, and deliberately so.** The default
  language direction is pinned rather than guessed (decision 6 above),
  and the comments name the event this was built for. A comment that says *why* a threshold was
  chosen is worth more when it names the real constraint it was chosen
  for — "a katha contains long silences" explains a keep-alive in a way
  that "some events have pauses" does not. Keep the event out of
  anything the code reads; keep it in what the next person reads.
- **`web/dist/` must be rebuilt after `web/src/` changes.** The Python
  server does not bundle on demand. There's a placeholder served at
  `/` when `dist/` is missing, which tells you to build.
- 🔴 **`web/pnpm-workspace.yaml` carries TWO keys and both are
  load-bearing. Do not delete either, and do not delete the file.**
  Three pnpm generations disagree about where this belongs, and the
  file is the only shape that satisfies all of them:
  - `packages:` — pnpm 9 and 10 treat the file's presence as declaring
    a workspace root, and a root without it fails `install` and `build`
    outright with `ERR_PNPM_WORKSPACE_PACKAGES_MISSING`.
  - `allowBuilds:` — pnpm 11 stopped reading `pnpm.onlyBuiltDependencies`
    from `package.json`, so without it esbuild's postinstall is blocked,
    pnpm exits 1 and **no bundle is produced**.

  Verified 2026-08-31 on **pnpm 9.15.9, 10.34.5 and 11.25.0**: all three
  install and build clean, and on 11 esbuild's postinstall runs. Note
  `pnpm.onlyBuiltDependencies` in `package.json` is kept as well — pnpm
  9/10 still read it — so the two mechanisms coexist deliberately.
  Testing only one pnpm version will convince you half of this is dead
  code. It is not.
- **macOS microphone permission is not auto-prompted.** Grant it to the
  terminal app (System Settings → Privacy & Security → Microphone) and
  restart the terminal. Symptom of missing permission: audio meter
  stuck at SILENT.

## Things that have been tried and don't work

- **A console window as the sign that the server is up.** It is a thing
  people close. Two console control events on 3 September, 19:19 and
  22:16, both `STATUS_CONTROL_C_EXIT` (#61); the owner confirmed he
  closed one of the windows himself, because it was empty and looked
  useless. The logon task now runs `powershell.exe -WindowStyle Hidden`,
  and the log file, the operator banner and the overlay's
  `CAPTIONS OFFLINE` pill carry the signal instead. The interrupt itself
  is still fatal — that is #61, not built here.
- **Trusting `Register-ScheduledTask` because it printed no error.** On
  the mandir PC it had failed with *Access is denied* and setup was
  assumed done. `register-startup-task.ps1` now reads the task back, and
  every run of `deploy.ps1` — including the daily `-StartOnly` — prints a
  full-width red banner when the task is missing or disabled.
- **Azure AI Speech for Indic continuous discourse** — 10–25 s final
  latency, segmentation too coarse. Don't reattempt without Custom
  Speech retraining.
- **Google `latest_long` for live streaming** — similar latency issues.
  Note that GCP Speech-to-Text v2 `chirp_2` is fine for *offline* VOD
  reprocessing — that's a different code path (`tools/vod_pipeline.py`).
- **Containerising audio capture on Docker Desktop for Mac** — Docker
  Desktop's Linux VM has no CoreAudio access. Native Linux hosts work
  via `/dev/snd` pass-through, but the project no longer ships a
  Dockerfile to keep the deployment surface small.
- **websockets >= 14** — first `ws.transcribe()` hangs, queue overflows,
  every chunk after the first is dropped. Keep the pin.
- **Replacing the whole screen with each caption** — the one-deep block
  pipeline. It held the lateness bound and cost the reader the thread between
  sentences: the eye finished line two, the screen blanked and refilled, and a
  discourse where every sentence leans on the last read as a series of islands.
  It also dropped 3.5% of sentences on Day 3, because one caption in reserve has
  nowhere to put a burst. Replaced by the rolling line scroll.
- **Trimming the front of the line queue at the bound** — puts a sentence on the
  hall screen starting from its second line, which is the beheaded-caption fault
  arrived at from a new direction. Drop whole sentences instead.
- **A per-line dwell floor carried over from the block model** — 2.0s was a
  TWO-LINE block's dwell. Per line it makes a two-line sentence take 4.0s
  against a 3.83s cadence and lateness grows on every sentence.
- **Verifying the slide from a background Chrome tab** — a hidden tab reports
  `document.hidden === true`, and Chrome suspends rendering in it: `requestAnimationFrame`
  fires once and never again, `setInterval` throttles to about a second, and
  `transitionstart`/`transitionend` never fire at all. Sampling the transform
  from there returns one frame and looks exactly like a scroll that snaps. The
  transition is only observable with the window actually in front of you.
- **CSS floats for the reserved zone, under the line scroll** — a float shortens
  a WRAPPING paragraph, which is what the block model was. Each line is now its
  own `nowrap` div, so the float does not shorten it, it shoves its start to the
  right: on screen every line began 440px in and ran off the right edge, cut
  mid-word. The 107 tests were green throughout. The zone now narrows the scroll
  COLUMN to the wider clear side of the block instead, and only when the block
  reaches the rows the scroll occupies.
- **Shipping a new DEFAULT and expecting it on a machine that has run this
  before** — stored settings used to beat `DEFAULTS` unconditionally. The 25%
  panel of #56 shipped on by default and the hall screen showed no background,
  because that vMix input had `captions-settings` written before the panel
  existed. Nothing errored; it rendered last month's appearance, and a reload
  does not help — localStorage outlives it. `captions-settings` now carries
  `__schema`, and a blob from an older bundle is discarded whole rather than
  merged. **Bump `SETTINGS_SCHEMA` when a default has to reach machines that
  have already saved** — it costs the operator their saved layout, so it is not
  automatic.
- **`npm ci` in a deploy runbook for this repo** — the manager here is pnpm and
  the committed lockfile is `web/pnpm-lock.yaml`. `npm ci` fails with `EUSAGE`
  on every machine, every time, because there is no `package-lock.json` and
  there should not be one. Use `pnpm install --frozen-lockfile && pnpm build`.
  A runbook that has never been run on a clean machine is a guess.
- **`SetBrowserURL` to repoint a vMix browser input** — not a vMix 26 function.
  It returns "No suitable Function could be found." `BrowserNavigate` works,
  ⚠️ but it changes the LIVE PAGE, not the input's stored URL, so the change
  reverts on a vMix restart or preset reload. A URL fix only sticks once
  somebody pastes it into the input's own settings and saves the preset.
  (`BrowserNavigate` also silently refuses any URL containing a comma — #34 —
  so `%2C` it.)
- **Assuming the overlay runs a modern Chromium** — it does not. Every request
  that vMix browser input has made, across four server logs, carries
  `Chrome/51.0.2704.103`, matching `vMix\browser\cef\libcef.dll`
  (`3.2704.1434`). **The LEGACY bundle is what the hall screen runs.** vMix 26
  ships engines 51, 62, 77, 86 and 103 side by side and that input is pinned to
  the oldest; moving it to V103 is a per-input desk change, not a rebuild, and
  costs nothing to try if the 220ms slide is ever reported as jumping.

## Where to look first when debugging

| Symptom | Look at |
|---|---|
| No captions appearing | Open the Debug panel from the operator UI; check Sarvam connection status |
| Nothing at all, for a long time | `logs/live-captions.log` — it outlives whatever started the server. If it is missing, check `%LOCALAPPDATA%\live-captions\logs` and `%TEMP%\live-captions-logs` before concluding anything: `configure_logging()` falls back in that order and the server prints the path it chose on its first line. Nothing in any of the three means the server never ran — check `Get-ScheduledTask -TaskName LiveCaptions` |
| Hall screen blank, cause unknown | If the overlay shows `CAPTIONS OFFLINE` the page is up and the server is gone; if it shows nothing, the page itself never loaded |
| Audio meter SILENT | macOS mic permission; mic device picker; `uv run python sarvam_stream.py` to bypass the web layer |
| `/` shows "UI not built yet" | `web/dist/` is missing — `cd web && pnpm build` |
| YouTube CC feed not firing | Outputs tab → feed enabled? stream key correct? advance reasonable (10–25 s)? |
| Sidecar Pi not connecting | Check mDNS service `_captions._tcp.local.` is advertised; firewall on port 8765 + 5353/udp |
| VOD STT hangs | GCP STT v2 chirp_2 op runs server-side; don't ⌃C, rerun with `--skip-stt` later |
