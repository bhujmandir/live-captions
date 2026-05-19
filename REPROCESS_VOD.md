# Reprocessing a YouTube VOD's captions

You have **two paths** to drive the same pipeline:

- **Operator UI** — open `http://<host>:8765/` in a browser, paste the
  YouTube URL into the **Reprocess** tab, click Reprocess. Watch the
  stages tick through, then preview + download the SRT in the same page.
  This is the recommended path for non-technical operators.
- **CLI** — `uv run python -m tools.reprocess_vod --video <YT-URL>` from
  the repo root. Same engine under the hood; useful when you want to
  batch-run or script it.

Both paths share `rules.json` (the live tool's substitution rules) so a
rule you add in the **Rules** tab also lands in reprocessed SRTs.

This page covers the one-time GCP setup, the per-VOD flow in either
surface, the local verification step, and the YouTube Studio upload.

## UI

The captions server (`live_captions.py`, port 8765) serves the React
operator UI at `http://<host>:8765/` — Live / Outputs / Rules /
Reprocess / Transcript tabs, all backed by the same REST + WS
endpoints. VOD reprocessing lives under the **Reprocess** tab.

### Building the React UI

The React app lives at `web/`. Build artefacts are gitignored.

```bash
cd web
pnpm install   # one-time
pnpm build     # writes dist/ — the live server picks this up
```

Restart `live_captions.py` after a build so the new dist is mounted.
For dev iteration use `pnpm dev` (runs Vite at `:5173` with a proxy
back to `:8765` for `/api` + `/ws`).

If `web/dist/` doesn't exist, the server serves a friendly placeholder
at `/` telling you to build first.

## What the tool does

For one past YouTube broadcast, it:

1. Downloads the video (≤720p MP4) and extracts 16 kHz mono FLAC audio
2. Uploads the FLAC to a GCS bucket
3. Runs GCP Speech-to-Text v2 with the `chirp_2` model in the source language
4. Groups words into ~3 s subtitle cues
5. Translates each cue to the target language via Cloud Translation v3
6. Applies `rules.json` (same rule list the live tool uses)
7. Writes an `.srt` + an HTML preview file so you can verify before
   uploading

The captions go up only after you manually upload the SRT in YouTube
Studio — the tool does **not** publish anything automatically.

---

## One-time setup

### 1. System tools

```bash
brew install yt-dlp ffmpeg
```

### 2. Python deps

The GCP SDKs are an optional dependency group (≈150 MB) so the live tool
stays light. Install them once:

```bash
uv sync --extra vod
```

### 3. GCP project, service account, bucket

You need a GCP project with these APIs enabled:

- `speech.googleapis.com` (Speech-to-Text v2)
- `translate.googleapis.com` (Cloud Translation v3)
- `storage.googleapis.com` (Cloud Storage)
- `iam.googleapis.com` + `iamcredentials.googleapis.com` (for the SA + ADC
  impersonation flow)

Create a service account with these roles:

- `roles/speech.client`
- `roles/cloudtranslate.user`
- `roles/storage.objectAdmin` *(or `objectCreator` + `objectViewer` on the
  specific bucket if you want to be tighter)*

Create a GCS bucket **in `us-central1`** (chirp_2 lives only there):

```bash
gcloud storage buckets create gs://<your-gcs-bucket> \
    --location=us-central1 --uniform-bucket-level-access
```

Optionally set a lifecycle rule so uploaded audio auto-deletes after a
week (the FLAC isn't needed once STT finishes):

```bash
cat > /tmp/lifecycle.json <<'EOF'
{"lifecycle":{"rule":[{"action":{"type":"Delete"},"condition":{"age":7}}]}}
EOF
gcloud storage buckets update gs://<your-gcs-bucket> \
    --lifecycle-file=/tmp/lifecycle.json
```

### 4. Auth — ADC with SA impersonation

Many GCP orgs disable service-account JSON key creation
(`constraints/iam.disableServiceAccountKeyCreation`) as a security
policy, so this tool supports Application Default Credentials with SA
impersonation as the default path.

> ⚠ **The operator account needs `roles/iam.serviceAccountTokenCreator`
> granted explicitly ON THE SA.** Project Owner alone is NOT enough —
> the first real API call will 403 with `Permission
> 'iam.serviceAccounts.getAccessToken' denied on resource`. Grant with:
>
> ```bash
> gcloud iam service-accounts add-iam-policy-binding \
>   <sa-name>@<project>.iam.gserviceaccount.com \
>   --member="user:<your-account>" \
>   --role="roles/iam.serviceAccountTokenCreator"
> ```

Then log in with impersonation:

```bash
gcloud auth application-default login \
    --impersonate-service-account=<sa-name>@<project>.iam.gserviceaccount.com
```

That writes credentials to
`~/.config/gcloud/application_default_credentials.json`. Move them next
to the captions tool so the CLI picks them up without extra env vars:

```bash
mv ~/.config/gcloud/application_default_credentials.json ./.gcp-adc.json
chmod 600 .gcp-adc.json
```

`.gcp-adc.json` is gitignored — never check it in. The file holds an
OAuth refresh token tied to the operator account; anyone with it can
impersonate the SA. If it leaks, revoke immediately with:

```bash
gcloud auth application-default revoke
```

### 5. Bucket env var

```bash
export GCS_BUCKET=<your-gcs-bucket>
```

(or pass `--bucket <your-gcs-bucket>` on every invocation). The CLI
auto-detects the GCP project ID from `gcloud config get-value project`
or the credentials file, so you don't normally need `--project`.

### Alternative: SA key file (only if org allows)

If your org doesn't block SA key creation, you can use the simpler
key-file path instead of ADC:

```bash
gcloud iam service-accounts keys create ~/.gcp/<sa-name>.json \
    --iam-account=<sa-name>@<project>.iam.gserviceaccount.com
export GOOGLE_APPLICATION_CREDENTIALS=~/.gcp/<sa-name>.json
```

The CLI prefers `GOOGLE_APPLICATION_CREDENTIALS` over `.gcp-adc.json`
when both are present.

---

## Reprocessing your first VOD

Pick a past YouTube broadcast. You can pass any of these forms:

- the bare ID (`dQw4w9WgXcQ`)
- a `youtu.be/…` short URL
- a `youtube.com/watch?v=…` URL (with or without extra query params)

### Path A — Operator UI (recommended)

1. Make sure the live captions tool is running:
   ```bash
   uv run python live_captions.py
   ```
2. Open `http://<host>:8765/` in a browser (substitute the host's
   hostname or IP). The React UI loads.
3. Click **Reprocess** in the left sidebar.
4. Paste the YouTube URL into the input bar at the top → click
   **Reprocess**. The job appears in the history list, status
   `queued`, then `running`.
5. Watch the stages tick through (Download video → Extract audio →
   Upload to cloud → Transcribe → …). The STT step is the long pole
   — expect ~30–50 min for a 3 h VOD.
6. When the job flips to `done`, the right pane shows an embedded
   video player with your reprocessed subtitles overlaid via an
   HTML5 `<track>`. Scrub through to verify timing and substitutions.
7. Click **Download SRT** and upload it via YouTube Studio (steps in
   "Uploading to YouTube Studio" below).

The Reprocess tab is fully driven by WebSocket — you can close the
browser tab and the job keeps running in the background. Re-open the
UI later and the history list shows the completed job.

### Path B — CLI

From the repo root:

```bash
uv run python -m tools.reprocess_vod --video <YT-URL>
```

### What to expect at each stage

| Stage | Time (for a 3 h VOD) | Notes |
|------|---|---|
| MP4 download | 2–5 min | ~1–1.5 GB to disk |
| FLAC extract | 30–60 s | ~70 MB to disk |
| GCS upload   | 30–60 s | one-time per VOD |
| GCP STT v2   | **30–50 min** | the long pole. chirp_2 runs at ~¼× real-time. |
| Translate v3 | 30–60 s | batched 100 cues per request |
| Rules + SRT  | <1 s | |
| HTML preview | <1 s | |

So plan ~45 min – 1 h end-to-end per 3-hour VOD. The CLI prints progress
at each stage. **Don't ⌃C** during the STT wait — the operation is
running server-side and can't be cancelled cleanly. If you must abort,
the long-running operation will still finish on Google's side; rerun
with `--skip-download` later and it'll reuse the GCS audio.

### Output

Everything lands at `results/vod-<id>/`:

```
vod-<id>.mp4              ← downloaded video, kept for the HTML preview
vod-<id>.flac             ← audio fed to GCP STT (kept for re-runs)
vod-<id>-words.json       ← raw word offsets from GCP STT (debug/cache)
vod-<id>-<target>.srt     ← upload this to YouTube Studio
vod-<id>-preview.html     ← open in a browser to verify
```

---

## Verifying before you upload

```bash
open results/vod-<id>/vod-<id>-preview.html
```

In the browser:

1. The video plays with the new subtitles overlaid via the `<track>`
   element.
2. **Scrub through the timeline** — especially the first 30 s, a couple
   of mid-session spots, and the last 30 s. Confirm:
   - Cue timing matches the speech (not drifting late)
   - Any domain-specific substitutions look right
   - No bizarre transcription errors (proper nouns may be off — that's
     usually a chirp_2 limitation, not a bug)
3. Expand the **Cue audit** panel at the bottom. Every cue where a rule
   fired shows the pre-rules text struck through and the post-rules
   version highlighted — quick way to confirm the substitutions are
   meaningful and not over-firing.

If a rule misfires, the cleanest fix is:

1. Stop the live tool (or just leave it — the rules edit is online)
2. Open the operator UI → Rules tab → disable or edit the offending
   rule
3. Rerun the CLI with `--skip-download --skip-stt` — it'll only re-run
   the translation + rules pass, which takes <1 min:

```bash
uv run python -m tools.reprocess_vod --video <id> --skip-download --skip-stt
```

---

## Uploading to YouTube Studio

1. Open the VOD in YouTube Studio (Content → click the video)
2. Left rail → **Subtitles**
3. Find the existing track (the live-ingested one). Click the **⋮** menu
   and either:
   - **Replace** (if you want to overwrite) — pick **Upload file →
     With timing** → choose the `.srt`
   - Or **Delete** first, then **Add language → … → Upload file →
     With timing** → choose the `.srt`
4. Save. The new track replaces the live-ingested one for all future
   playback. Existing viewers' captions update on their next page load.

> **Important**: do NOT click "Auto-translate" or pick "Without timing"
> — both will overwrite the timestamps you carefully synced.

---

## Subsequent VODs

After the first run everything's cached, so:

```bash
uv run python -m tools.reprocess_vod --video <next-yt-url>
```

is all you need. The output dir is per-VOD so different videos don't
collide.

### Useful flags for re-runs

| Flag | Effect |
|---|---|
| `--skip-download` | Reuse existing MP4/FLAC in the output dir |
| `--skip-stt` | Reuse `vod-<id>-words.json` (skips the ~45 min GCP STT) |
| `--source-lang gu-IN` | Source language (defaults match the live tool config) |
| `--target-lang en` | Target language for the SRT |
| `--out <dir>` | Override default `results/vod-<id>/` |
| `--bucket <name>` | Override `GCS_BUCKET` env var |
| `--project <id>` | Override the auto-detected GCP project |

`--skip-stt` is the big one — once you've got the words file, you can
iterate on cue grouping / rules / translation in <60 s instead of 45
min.

---

## Troubleshooting

**`GOOGLE_APPLICATION_CREDENTIALS env var is unset`**
You haven't exported the service-account JSON path. Re-read step 4
above.

**`yt-dlp not found on PATH`**
`brew install yt-dlp`.

**`Could not detect GCP project`**
Either pass `--project <id>` or set `GCP_PROJECT` env var. Normally the
project is auto-read from the service account JSON's `project_id` field.

**`PermissionDenied: ... does not have permission ...`**
Service account is missing a role. Check it has all three:
`speech.client`, `cloudtranslate.user`, `storage.objectAdmin`.

**`INVALID_ARGUMENT: ... model chirp_2 ...`**
Bucket is not in `us-central1`. chirp_2 is region-locked. Create a new
bucket there and re-run.

**`GCP STT returned no words`**
Either the audio is silent, the language code is wrong (use `gu-IN`
not `gu`), or the FLAC extraction failed. Look at the file size — a
3 h session should be ~70 MB. If it's <1 MB, delete `vod-<id>.flac`
and re-run without `--skip-download`.

**Captions feel out of sync on the HTML preview**
The cue start/end timestamps are anchored to the original audio
timeline, so they should be sync-accurate against the MP4. If they
drift, it's almost always because the MP4 was downloaded at a different
bitrate from the FLAC source — delete both files and re-run. If they're
slightly late on YouTube Studio's preview after upload, that's YouTube
Studio quirks; the file itself is fine.

**It took longer than 1 hour and the CLI hung**
The default STT timeout is 2 hours. If a single VOD is taking longer,
abort, rerun with `--skip-stt` later (after the long-running operation
completes server-side) — or contact GCP support if the op stays pending
forever.
