# AGENTS.md

Live subtitles for a spoken event: Gujarati in, English on screen two or three seconds later,
composited into a live video feed. It has run in front of a congregation for 28 hours.

**That is the fact that changes how you work here.** A wrong caption is not a failed assertion,
it is a wrong word on a temple screen, read by people who trusted it. Most of this file exists
because something on the list below was learned the expensive way.

---

## Before you change anything

1. **Read [`CONTEXT.md`](CONTEXT.md).** The words are used precisely — *caption*, *line*,
   *final*, *fragment*, *the overlay*, *the ladder*, *on air*, *dropped* — and reasoning with
   the loose meaning of any of them produces a plausible change that is wrong. Five minutes.
2. **Read the part of [`docs/how-it-works.md`](docs/how-it-works.md) that covers your area.**
   It is long because it is the record of what has already been tried; its
   *Things that have been tried and don't work* section is the cheapest thing in this repo.
3. **Check [`docs/adr/`](docs/adr/).** Two decisions are written down and frozen. Changing
   either is a decision, not a refactor.
4. **Run both suites before you start**, so a red you inherit is not a red you think you made:
   `uv run pytest -q` and `cd web && npx vitest run`. No network, no API key and no audio
   device. Seconds, not minutes.

---

## Prove it on the replay

**A katha happens once. A recording replays against any change.**

`tools/replay_audio.py` streams a real recording through the real pipeline. It is the
instrument for every question about latency, caption timing, terminology or assembly.

**Reach for the replay whenever a claim is about what the audience would see.** Reserve live
audio for what only live audio can answer.

---

## Five things the code cannot tell you

**The margin is the briefing, not the model.** `glossary.json` — a brief plus the terms and
their renderings — is what earns the quality. Briefed, Gemini scores 24/24 on terminology; asked
cold it makes the same errors as the cheap translator. Any change to the translator path keeps
the glossary reaching the model, or it has removed the reason the model is there.

**No rung of the ladder returns nothing.** `translate_line()` is one door over three rungs:
Gemini, then Mayura, then the source text. A blank caption bar in a full hall is the one outcome
with no recovery, so a worse caption always beats none. Preserve that property in anything you
change there.

**Fluent is not correct.** A confident wrong caption reads *better* than a hesitant right one,
which makes reading the English a misleading test. Terminology is scored against the published
translation — `tools/benchmark_translators.py` — never judged on how the sentence sounds.

**The overlay runs in Chromium 51.** When vMix hosts it, the browser is from 2016, not the one
you would test in. Both the bundle and the CSS are held to what it executes. A change that works
in Chrome and not in vMix has not worked.

**Nobody who reads Gujarati has reviewed the glossary.** A developer chose those terms from
published translations. It is the largest open risk here, it is not a software problem, and it is
not yours to close by writing more code.

---

## Where everything is

| You want to | Read |
|---|---|
| Run it, or find out what it needs | [`README.md`](README.md) |
| Change the server or the overlay | [`docs/how-it-works.md`](docs/how-it-works.md) |
| Use the right word for something | [`CONTEXT.md`](CONTEXT.md) |
| Know what is deliberately frozen | [`docs/adr/`](docs/adr/) |
| Know what changed, and what is still broken | [`CHANGELOG.md`](CHANGELOG.md) |

`live_captions.py` is the server, the Sarvam client, the ladder, the sentence assembly and the
output fan-out, in one file on purpose — they share one event loop and one broadcast bus.
**Edit it in place.** Its size is the first thing anyone wants to fix and the co-location is
load-bearing; `docs/how-it-works.md` gives the reasoning.

---

## Two things that will catch you

**Rebuild the web bundle after touching `web/src/`.** The Python server does not bundle on
demand: `cd web && pnpm build`. Skip it and you are testing the previous build while reading
your new source. `/` serves a placeholder when `web/dist/` is missing, which is the symptom.

**Keep every key in `.env`.** It is gitignored, and so are the operator logs — a caption log is
a complete transcript of what was said in a temple.

---

## Report what you did not prove

Several faults here survived because something reported success while doing nothing: an
installer that printed `OK` four times while failing, a scheduled task that reads `Running` after
the server inside it has died, a log line that named the translator it was asked for rather than
the one in the path, two settings confirmed on screen and absent from the saved file.

**Name the gap.** "Green on macOS, untested on Windows" is a useful result. "Green" is not,
when the surface that matters is the one you could not reach.

---

*Issue numbers in this repository's documents (`#61`, `#25`, …) refer to the tracker of the
project this code was developed in. They record provenance and will not resolve here.*
