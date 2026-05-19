# Live Captions

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
        ├── /ws              → broadcast bus: captions + status + log records
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

4. **websockets pinned `<14`.** sarvamai 0.1.28 ships the legacy
   `websockets` module; 14+ changed send-flow semantics and the first
   `ws.transcribe()` call hangs forever on 16.0. Keep the pin.

5. **`web/dist/` is the production frontend.** `live_captions.py`
   serves files directly out of `web/dist/`. After editing anything in
   `web/src/`, you MUST run `pnpm build` and restart the server.

6. **`gu-IN` is no longer the default.** The tool ships neutral
   (`DEFAULT_SOURCE_LANG=en-IN`, `DEFAULT_TARGET_LANG=en-IN`). Orgs
   override via `.env`.

7. **YouTube CC = one Saaras session, N Mayura fan-outs.** Each enabled
   feed in `outputs.json` triggers a parallel `POST /translate` call
   per FINAL. Adding feeds doesn't multiply the Saaras quota cost.

8. **Branding is two env vars.** `APP_NAME` (browser title + header)
   and `ACCENT_COLOR` (highlight + focus + chip border, via a single
   CSS variable). No org-specific assets baked in.

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
```

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
- **No org branding in code.** All event-specific names/colours/IPs go
  through `.env`. If you find yourself typing an org or event name, or
  a static LAN IP in a comment or default value, route it through
  `.env` instead.
- **`web/dist/` must be rebuilt after `web/src/` changes.** The Python
  server does not bundle on demand. There's a placeholder served at
  `/` when `dist/` is missing, which tells you to build.
- **macOS microphone permission is not auto-prompted.** Grant it to the
  terminal app (System Settings → Privacy & Security → Microphone) and
  restart the terminal. Symptom of missing permission: audio meter
  stuck at SILENT.

## Things that have been tried and don't work

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

## Where to look first when debugging

| Symptom | Look at |
|---|---|
| No captions appearing | Open the Debug panel from the operator UI; check Sarvam connection status |
| Audio meter SILENT | macOS mic permission; mic device picker; `uv run python sarvam_stream.py` to bypass the web layer |
| `/` shows "UI not built yet" | `web/dist/` is missing — `cd web && pnpm build` |
| YouTube CC feed not firing | Outputs tab → feed enabled? stream key correct? advance reasonable (10–25 s)? |
| Sidecar Pi not connecting | Check mDNS service `_captions._tcp.local.` is advertised; firewall on port 8765 + 5353/udp |
| VOD STT hangs | GCP STT v2 chirp_2 op runs server-side; don't ⌃C, rerun with `--skip-stt` later |
