# Live Captions

Real-time multilingual caption system built on Sarvam AI. Designed for live
events — lectures, sermons, conferences, broadcasts — where speech needs to
appear on screen (and/or YouTube CC) within a couple of seconds.

Runs on a Mac (or Linux host) with a USB audio input. Outputs to a browser
overlay you can drop into ProPresenter / OBS, to ProPresenter Messages, and
to one or more YouTube CC streams in parallel.

## Features

- **Any source ↔ any target** — pick from Sarvam Saaras's 23 Indic
  languages + English; the pipeline auto-selects:
  - Indic → English: Saaras `translate` mode, one streaming call
  - Same-language: Saaras `transcribe` mode, no MT step
  - Anything else: Saaras `transcribe` + Sarvam Mayura text translation
- Mid-session ⇆ flip between source ↔ target in 1–2s
- Browser overlay sized for 1920×1080 (ProPresenter Web Capture, OBS,
  any LED wall or signage display)
- **Multiple YouTube CC destinations** — each feed has its own stream key,
  target language, advance offset, enable toggle. One Saaras session fans
  out to all enabled feeds via parallel Mayura calls per FINAL.
- Optional ProPresenter Message overlay
- Substitution rules — fix common AI mishearings (e.g. proper nouns,
  domain-specific terms) with a managed list applied to every FINAL.
- Persistent transcript log with per-flip dividers
- VOD reprocessing — re-caption past YouTube broadcasts via GCP
  Speech-to-Text + Cloud Translation (see [REPROCESS_VOD.md](REPROCESS_VOD.md))

---

## How it works

```
USB audio in (16 kHz mono)
        │
        ▼
live_captions.py  ──── Sarvam streaming WebSocket ────►  text
        │                (Indic→en: saaras translate mode;
        │                 other pairs: saaras transcribe + Mayura)
        ▼
aiohttp web server
        ├── GET  /                → React operator UI (Live / Outputs / Rules / Reprocess / Transcript)
        ├── GET  /?overlay=1      → transparent caption overlay for OBS / ProPresenter Web Capture
        ├── GET  /api/config      → branding + defaults + lang matrix (fetched once on boot)
        ├── GET  /api/devices     → list audio input devices
        ├── POST /api/start       → start transcription (direction + sarvam config)
        ├── POST /api/stop        → stop transcription
        ├── POST /api/direction   → flip direction mid-session
        ├── POST /api/youtube/*   → YouTube CC live config
        └── GET  /ws              → WebSocket feed (captions, status, server logs)
```

Caption events are broadcast to every browser tab open on `/`. The
operator tab gets a transcript sidebar, audio meter, and status pills; the
overlay mode (`/?overlay=1`) renders just the caption text on a
transparent background, ready to drop into ProPresenter Web Object or OBS
Browser Source.

---

## Quick start

```bash
# 1. Clone, then run the one-shot installer (Homebrew + portaudio + uv + .env scaffold)
bash deploy.sh

# Or, if you prefer manual steps:
uv sync
cp .env.template .env
# edit .env, paste SARVAM_API_KEY
uv run python live_captions.py
```

Then open <http://localhost:8765/> in a browser on the same machine. Pick a
source + target, pick the mic device, click ▶ Start.

> **macOS:** grant microphone permission to your terminal app the first time
> (System Settings → Privacy & Security → Microphone) — macOS doesn't prompt;
> the audio meter will just stay at SILENT until permission is granted. Quit
> and reopen the terminal after granting.

---

## Using in ProPresenter

**Web Capture source (recommended)**

1. In ProPresenter, add a **Web** prop pointing at
   `http://<host>:8765/?overlay=1`
2. Size the web object 1920×1080 (or whatever you've set the caption-area
   bounds to)
3. Toggle the overlay on/off from Companion or a Stream Deck like any other prop

The overlay mode hides operator chrome and respects the layout you've set —
font size, area position, reserved blocks for screen content underneath.

**Message overlay (push captions to a PP Message)**

Start the server with `--propresenter`. Each FINAL writes to a ProPresenter
Message named "Live Caption" (auto-created if absent). Configure host/port
via `PP_HOST` / `PP_PORT` in `.env`.

---

## Direction & flip

The Simple bar at the top has two searchable inputs for **Source** and
**Target** languages (type to filter the full Sarvam list, ~23 langs), plus
a ⇆ button to swap them.

The Sarvam pipeline is derived from the pair:

| Source → Target            | Pipeline                                           |
|----------------------------|----------------------------------------------------|
| Indic → en-IN              | Saaras `mode=translate` (one streaming call)       |
| same → same                | Saaras `mode=transcribe` (no translation)          |
| any other combo            | Saaras `mode=transcribe` + Sarvam Mayura translate |

When pipeline 3 is active, the UI shows a one-time "+ ~200–500 ms latency"
toast so operators know to bump up each feed's *advance* slider.

| State        | What happens on flip                                              |
|--------------|-------------------------------------------------------------------|
| Idle         | Server state updated; next ▶ Start uses the new pair              |
| Running      | Live Sarvam WS is closed → reconnects with new mode + language    |
| In overlay   | A "Switching to X → Y…" chip flashes for ~2s during the reconnect |
| Transcript   | A divider line is inserted at the flip point for review later     |
| YouTube CC   | Each feed keeps its own target; the display picks settings.target |

End-to-end latency on flip is typically 1–2s. The audio capture is **not**
restarted — only Sarvam's WS is re-handshaked.

## YouTube CC feeds

Right-side **Outputs** tab manages a list of YouTube CC destinations:

- Each feed: label, stream key, target language, advance (seconds), enable toggle
- All feeds run from a single Sarvam Saaras session; the server fans out
  via parallel Sarvam Mayura translates so each feed gets its own language
- Add multiple feeds for: multi-language captions on one broadcast (same key,
  different langs), or simultaneous broadcasts on multiple channels
- Persisted to `outputs.json` (gitignored)
- Delete permanently removes the stream key — re-add by pasting it again
- Migration: a legacy `YOUTUBE_STREAM_KEY` in `.env` is seeded as the first
  feed on first run

---

## Simple vs Expert mode

The top bar defaults to **Simple**: direction pill, ⇆ swap, mic device,
layout preset (Small / Medium / Large / Custom), Start/Stop, audio meter.

The **⚙ Expert ▸** toggle reveals two extra rows for tuning:

- **Layout**: precise X/Y/W/H sliders for caption area + reserved block,
  font size and weight, transparent vs solid background
- **Sarvam internals**: model variant (v3 / v2.5 / v2 / v1 / flash),
  high-VAD sensitivity, VAD signals, client-side silence gate (% threshold
  and hangover seconds)

Expert tweaks are persisted to localStorage and the layout dropdown
automatically flips to "Custom" once you nudge a slider away from a preset.

---

## Branding

The HTML title, header, and accent colour are driven by two `.env` vars:

```ini
APP_NAME=Live Captions      # browser title + operator header
ACCENT_COLOR=#FF8C00        # primary highlight (start button, direction pill)
```

Both default to neutral values so out-of-the-box there's no event-specific
branding. The accent colour propagates into every button highlight, focus
ring, and the "switching…" chip border via a single CSS variable.

---

## Environment variables (`.env`)

| Variable              | Required | Description                                                                                  |
|-----------------------|----------|----------------------------------------------------------------------------------------------|
| `SARVAM_API_KEY`      | Yes      | One key drives Saaras (STT/translate) and Mayura (text translate). Get one at <https://dashboard.sarvam.ai/> |
| `APP_NAME`            | No       | Browser title + header (default: `Live Captions`)                                            |
| `ACCENT_COLOR`        | No       | Hex / CSS colour for buttons + highlights (default: `#FF8C00`)                               |
| `DEFAULT_SOURCE_LANG` | No       | Sarvam code (e.g. `gu-IN`) pre-filling the source dropdown on first load                     |
| `DEFAULT_TARGET_LANG` | No       | Sarvam code pre-filling the target dropdown on first load                                    |
| `PP_HOST` / `PP_PORT` | No       | ProPresenter network API endpoint when `--propresenter` is set (default `127.0.0.1:49566`)   |
| `GCP_PROJECT`         | No       | GCP project ID for VOD reprocessing (see REPROCESS_VOD.md)                                   |
| `GCS_BUCKET`          | No       | GCS bucket for VOD audio uploads (see REPROCESS_VOD.md)                                      |
| `YOUTUBE_STREAM_KEY`  | No       | Legacy — only used on first run to seed the initial feed in `outputs.json`                   |

See [`.env.template`](.env.template) for a copy-pasteable starter.

---

## File layout

```
live_captions.py      Main app — web server, Sarvam streaming, Mayura translate, flip handler
sarvam_stream.py      Standalone terminal test (no web UI; bypasses Mayura)
tools/
  reprocess_vod.py    CLI: re-caption a past YouTube broadcast (see REPROCESS_VOD.md)
  vod_pipeline.py     Shared pipeline used by both the CLI and the Reprocess tab
web/                  React 18 + Tailwind + shadcn UI served at /
  src/                Source
  dist/               Built assets (gitignored; run `pnpm build`)
.env.template         Copy to .env and fill in keys
pyproject.toml        Python dependencies (uv-managed)
deploy.sh             Idempotent macOS installer (Homebrew, portaudio, uv, .env)
rules.json            Substitution rules (managed via Rules tab; empty by default)
outputs.json          YouTube CC feed list (gitignored; managed via Outputs tab)
vod-jobs.json         VOD reprocess job history (gitignored)
samples/              Test audio (gitignored — drop your own .wav files in here)
results/              JSONL/SRT output from sessions (gitignored)
uploads/              User-uploaded audio for VOD reprocessing (gitignored)
```

### Building the React UI

The React app at `web/` is served at `/` by the Python backend in
production. Build artefacts are gitignored.

```bash
cd web
pnpm install   # one-time
pnpm build     # writes dist/ — the live server picks this up on next start
```

Restart `live_captions.py` after a build so the new dist is mounted. For dev
iteration use `pnpm dev` (runs Vite at `:5173` with a proxy back to `:8765`
for `/api` + `/ws`).

If `web/dist/` doesn't exist, the server serves a friendly placeholder at
`/` telling you to build first.

---

## Why Sarvam (vs Azure / Google / Whisper)

Several streaming STT providers were evaluated. The core requirement for
live discourse (lectures, sermons, long-form speech) is **frequent
finalisation** — captions must commit every 2–4 seconds, not wait for
natural sentence pauses that never come in continuous speech.

- **Sarvam Saaras v3** with `high_vad_sensitivity=True` fires after 0.5 s
  of silence, handles all major Indic languages natively, and emits final
  segments fast enough for live captioning.
- **Sarvam Mayura** (`POST /translate`) handles bidirectional text
  translation between English and 10 Indic languages, used here for the
  `en→Indic` direction where Saaras can only transcribe (it doesn't produce
  English-to-Indic streaming translation).
- Azure AI Speech and Google Cloud `latest_long` were both unusable for
  continuous discourse — 10–30 s final latency.
- A second provider (Whisper local, Deepgram, ElevenLabs Scribe) could be
  plumbed in behind a thin abstraction — not implemented yet.

---

## License

Apache 2.0 — see [LICENSE](LICENSE).
