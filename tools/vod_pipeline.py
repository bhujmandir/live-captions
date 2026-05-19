#!/usr/bin/env python3
"""
VOD reprocessing pipeline — stage functions + async orchestrator.

This is the shared engine driven by BOTH the CLI (`tools/reprocess_vod.py`)
and the operator UI (`tools/vod_server.py`). Each pipeline run progresses
through a fixed sequence of stages and emits a typed event after every
stage transition so the caller can render progress.

Sync stage functions live here so the CLI can use them directly with no
event-loop overhead; the async `VodPipeline.run()` wraps them in
executor threads so the server stays responsive (yt-dlp, ffmpeg, GCP
SDK calls all block).

Outputs land under `<results_dir>/vod-<id>/`:

    vod-<id>.mp4              ← downloaded video, used by HTML preview
    vod-<id>.flac             ← 16 kHz mono audio fed to GCP STT
    vod-<id>-words.json       ← raw word offsets (debug / cache)
    vod-<id>-en.srt           ← final SRT, upload to YouTube Studio
    vod-<id>-preview.html     ← open in a browser to verify before upload
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Awaitable, Callable

log = logging.getLogger("vod_pipeline")

# chirp_2 lives only in us-central1 (as of the SDK version we depend on).
# The regional endpoint is mandatory — the global endpoint returns
# INVALID_ARGUMENT for this model.
GCP_REGION       = "us-central1"
GCP_STT_ENDPOINT = f"{GCP_REGION}-speech.googleapis.com"


# ── Stage enum + progress event ─────────────────────────────────────────────

class Stage(str, Enum):
    QUEUED           = "queued"
    DOWNLOAD_VIDEO   = "download_video"
    AWAITING_RANGES  = "awaiting_ranges"   # interactive pause for operator
    EXTRACT_AUDIO    = "extract_audio"
    UPLOAD_GCS       = "upload_gcs"
    STT_BATCH        = "stt_batch"
    CUE_GROUP        = "cue_group"
    TRANSLATE        = "translate"
    APPLY_RULES      = "apply_rules"
    WRITE_OUTPUTS    = "write_outputs"
    DONE             = "done"
    FAILED           = "failed"

# Human-readable stage labels for the UI.
STAGE_LABELS = {
    Stage.QUEUED:           "Queued",
    Stage.DOWNLOAD_VIDEO:   "Downloading video",
    Stage.AWAITING_RANGES:  "Awaiting range selection",
    Stage.EXTRACT_AUDIO:    "Extracting audio",
    Stage.UPLOAD_GCS:       "Uploading to Cloud Storage",
    Stage.STT_BATCH:        "Transcribing (GCP STT)",
    Stage.CUE_GROUP:        "Grouping subtitle cues",
    Stage.TRANSLATE:        "Translating",
    Stage.APPLY_RULES:      "Applying rules",
    Stage.WRITE_OUTPUTS:    "Writing SRT + preview",
    Stage.DONE:             "Done",
    Stage.FAILED:           "Failed",
}

# Stages shown as steps in the UI's progress component (excludes terminal
# states QUEUED / DONE / FAILED — those are reflected in the job's status).
ORDERED_STAGES = [
    Stage.DOWNLOAD_VIDEO, Stage.AWAITING_RANGES, Stage.EXTRACT_AUDIO,
    Stage.UPLOAD_GCS,     Stage.STT_BATCH,       Stage.CUE_GROUP,
    Stage.TRANSLATE,      Stage.APPLY_RULES,     Stage.WRITE_OUTPUTS,
]


# A selected time range in the source video. Operators may submit
# multiple non-contiguous ranges (e.g. katha split by kirtan).
# Lists of these are passed to extract_audio_step + tracked on the job
# so SRT timestamps end up in the ORIGINAL video timeline regardless
# of how the audio gets concatenated for STT.
Range = tuple[float, float]   # (start_seconds, end_seconds)


@dataclass
class StageEvent:
    stage:    Stage
    detail:   str   = ""        # short status string ("downloaded 1.2 GB")
    pct:      float | None = None  # 0..1 within the stage, when known
    elapsed_s: float = 0.0      # wall-clock seconds since stage start
    error:    str = ""          # populated on FAILED


# Optional async / sync callback. The server passes an async function
# that broadcasts a WS frame; the CLI passes a sync printer.
ProgressCallback = Callable[[StageEvent], Awaitable[None] | None]


# ── Video ID parsing ────────────────────────────────────────────────────────

_YT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

def parse_video_id(arg: str) -> str:
    """Accept a bare video ID, a youtu.be/<id> URL, or a youtube.com/watch?v=<id>
    URL. Returns the 11-char ID."""
    if _YT_ID_RE.match(arg):
        return arg
    parsed = urllib.parse.urlparse(arg)
    if parsed.hostname == "youtu.be":
        path_id = parsed.path.lstrip("/")
        if _YT_ID_RE.match(path_id):
            return path_id
    if parsed.hostname and "youtube.com" in parsed.hostname:
        qs = urllib.parse.parse_qs(parsed.query)
        if "v" in qs and _YT_ID_RE.match(qs["v"][0]):
            return qs["v"][0]
    raise ValueError(f"Could not extract YouTube video ID from {arg!r}")


# ── Rule engine (kept in lock-step with live_captions.py) ────────────────────

@dataclass
class _Rule:
    id: str
    pattern: str
    replacement: str
    regex: bool
    enabled: bool
    _compiled: "re.Pattern | None" = None

    def compile_(self) -> None:
        self._compiled = None
        if not self.enabled or not self.pattern:
            return
        try:
            if self.regex:
                self._compiled = re.compile(self.pattern, re.IGNORECASE | re.UNICODE)
            else:
                self._compiled = re.compile(
                    r"\b" + re.escape(self.pattern) + r"\b",
                    re.IGNORECASE | re.UNICODE,
                )
        except re.error as e:
            log.warning(f"Rule {self.id!r}: bad pattern {self.pattern!r}: {e}")


def load_rules(rules_path: Path) -> list[_Rule]:
    if not rules_path.exists():
        log.info(f"No {rules_path.name} found — rules pass disabled")
        return []
    try:
        data = json.loads(rules_path.read_text())
    except Exception as e:
        log.warning(f"Failed to parse {rules_path}: {e!r}")
        return []
    rules: list[_Rule] = []
    for entry in data.get("rules", []):
        r = _Rule(
            id          = entry.get("id") or "",
            pattern     = (entry.get("pattern") or "").strip(),
            replacement = entry.get("replacement") if entry.get("replacement") is not None else "",
            regex       = bool(entry.get("regex", False)),
            enabled     = bool(entry.get("enabled", True)),
        )
        r.compile_()
        rules.append(r)
    return rules


def apply_rules(text: str, rules: list[_Rule]) -> tuple[str, list[str]]:
    if not text or not rules:
        return text, []
    fired: list[str] = []
    ordered = sorted(
        [r for r in rules if r.enabled and r._compiled is not None],
        key=lambda r: (-len(r.pattern), r.id),
    )
    for rule in ordered:
        new_text, n = rule._compiled.subn(rule.replacement, text)
        if n > 0:
            fired.append(rule.id)
            text = new_text
    return text, fired


# ── Prereq + auth helpers ───────────────────────────────────────────────────

def check_prereqs() -> None:
    """Returns silently if everything's OK; raises RuntimeError with a
    user-facing message otherwise. Also resolves the auth file path
    into GOOGLE_APPLICATION_CREDENTIALS if the in-repo .gcp-adc.json
    is present (so subsequent google-auth calls pick it up), AND sets
    GOOGLE_CLOUD_PROJECT from detect_project() so the impersonated-SA
    ADC variant (which doesn't carry a project_id) still works."""
    if not shutil.which("yt-dlp"):
        raise RuntimeError("yt-dlp not found on PATH. Install: brew install yt-dlp")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH. Install: brew install ffmpeg")
    cred = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    in_repo_adc = Path(__file__).parent.parent / ".gcp-adc.json"
    default_adc = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
    if cred:
        if not Path(cred).exists():
            raise RuntimeError(f"GOOGLE_APPLICATION_CREDENTIALS path does not exist: {cred}")
    elif in_repo_adc.exists():
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(in_repo_adc)
    elif not default_adc.exists():
        raise RuntimeError(
            "No GCP credentials found. Run\n"
            "  gcloud auth application-default login "
            "--impersonate-service-account=<sa-email>\n"
            "then move ~/.config/gcloud/application_default_credentials.json "
            "to captions/.gcp-adc.json"
        )
    # Some google-cloud clients (storage, translate) try to auto-detect
    # the project from the credential file. Impersonated-SA ADC creds
    # don't include `project_id`, so detection silently fails and
    # raises OSError("Project was not passed and could not be determined
    # from the environment.") at first use. Forward-fill the env var
    # from gcloud config / SA JSON so every client sees it.
    if not os.environ.get("GOOGLE_CLOUD_PROJECT"):
        proj = detect_project()
        if proj:
            os.environ["GOOGLE_CLOUD_PROJECT"] = proj


def detect_project() -> str | None:
    """Resolve the GCP project. Order: GOOGLE_CLOUD_PROJECT / GCP_PROJECT env,
    SA-key project_id field, `gcloud config get-value project`.
    """
    for k in ("GOOGLE_CLOUD_PROJECT", "GCP_PROJECT"):
        if os.environ.get(k):
            return os.environ[k]
    cred = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if cred and Path(cred).exists():
        try:
            data = json.loads(Path(cred).read_text())
            if "project_id" in data:
                return data["project_id"]
        except Exception:
            pass
    try:
        out = subprocess.run(
            ["gcloud", "config", "get-value", "project"],
            capture_output=True, text=True, timeout=5,
        )
        project = (out.stdout or "").strip()
        if project and project != "(unset)":
            return project
    except Exception:
        pass
    return None


# ── Sync stage functions (used by both CLI and server) ──────────────────────

_YTDLP_PROGRESS_RE = re.compile(
    r"\[download\]\s+([\d.]+)%"
    r"(?:\s+of\s+~?\s*([\d.]+\s?\w+))?"
    r"(?:\s+at\s+([\d.]+\s?\w+/\w+))?"
    r"(?:\s+ETA\s+([\d:-]+))?"
)


def download_video_step(video_id: str, mp4_path: Path,
                         on_pct: "callable | None" = None) -> None:
    """yt-dlp pull, MP4 ≤ 720p, merged audio+video.

    Streams progress to `on_pct(pct: float, detail: str)` as yt-dlp
    emits progress lines. `--newline` makes yt-dlp emit one line per
    progress update instead of carriage-returning over a single line,
    which means we can parse it incrementally without dealing with \\r.

    yt-dlp's two-step download (video then audio for merge) means the
    percentage resets mid-job. We don't try to flatten this — the
    operator sees video pct rising to 100%, then audio pct rising to
    100%, then "Merging…". Clearer than a fake "global" percentage.
    """
    url = f"https://www.youtube.com/watch?v={video_id}"
    cmd = [
        "yt-dlp",
        "--newline",
        "--no-color",
        "-f", "bv*[height<=720]+ba/b[height<=720]/b",
        "--merge-output-format", "mp4",
        "-o", str(mp4_path),
        url,
    ]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    assert proc.stdout is not None
    last_pct = -1.0
    for line in proc.stdout:
        line = line.rstrip()
        if not line:
            continue
        # Quiet logging — yt-dlp output is verbose; we surface only the
        # download progress (which is what the operator cares about).
        m = _YTDLP_PROGRESS_RE.search(line)
        if m and on_pct:
            try:
                pct = float(m.group(1)) / 100.0
            except ValueError:
                continue
            # Throttle — yt-dlp can emit 50 updates per second on a fast
            # connection. Forward only on a >=0.5% delta or at the
            # boundaries so the UI doesn't get hammered.
            if pct - last_pct < 0.005 and pct not in (0.0, 1.0):
                continue
            last_pct = pct
            parts = [f"{m.group(1)}%"]
            if m.group(2): parts.append(f"of {m.group(2)}")
            if m.group(3): parts.append(f"at {m.group(3)}")
            if m.group(4): parts.append(f"ETA {m.group(4)}")
            detail = "  ".join(parts)
            try: on_pct(pct, detail)
            except Exception as e:
                log.debug(f"download on_pct callback raised: {e!r}")
        elif "Merging" in line or "[ffmpeg]" in line:
            if on_pct:
                try: on_pct(1.0, "Merging audio + video")
                except Exception: pass
    proc.wait()
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd)


def extract_audio_step(mp4_path: Path, flac_path: Path,
                        ranges: list[Range] | None = None) -> None:
    """ffmpeg 16 kHz mono 16-bit FLAC extraction. chirp_2 accepts FLAC
    via auto-decoding config, but is documented as expecting 16-bit
    samples — without `-sample_fmt s16` ffmpeg writes 32-bit when the
    decoded source is float (which is what YouTube AAC decodes to),
    and the recognizer sometimes silently returns zero results.

    When `ranges` is provided, only those segments of the source are
    concatenated into the FLAC. Uses ffmpeg's `aselect` filter with a
    union of `between(t, start, end)` clauses, followed by
    `asetpts=N/SR/TB` to reset timestamps so the concatenated output
    runs 0..(sum of durations). Caller is responsible for remapping
    STT word offsets back to the original timeline via
    `remap_concat_to_original`.
    """
    if ranges:
        # Strip degenerate ranges; sort so the concat is in chronological order.
        clean = sorted([(s, e) for s, e in ranges if e > s], key=lambda x: x[0])
        if not clean:
            raise ValueError("ranges supplied but all are empty / invalid")
        select_expr = "+".join(f"between(t,{s:.3f},{e:.3f})" for s, e in clean)
        af = f"aselect='{select_expr}',asetpts=N/SR/TB"
        cmd = [
            "ffmpeg", "-y",
            "-i", str(mp4_path),
            "-af", af,
            "-ac", "1", "-ar", "16000", "-vn",
            "-c:a", "flac",
            "-sample_fmt", "s16",
            str(flac_path),
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(mp4_path),
            "-ac", "1", "-ar", "16000", "-vn",
            "-c:a", "flac",
            "-sample_fmt", "s16",
            str(flac_path),
        ]
    subprocess.run(cmd, check=True, stderr=subprocess.DEVNULL)


def remap_concat_to_original(t_concat: float, ranges: list[Range]) -> float:
    """Given a timestamp in the concatenated audio (e.g. a word's
    start_offset returned by STT), return the corresponding timestamp
    in the ORIGINAL source video. Used to rebuild SRT cues in the
    original timeline so the SRT file uploads cleanly to YouTube.

    If no ranges were used, returns t_concat unchanged.
    """
    if not ranges:
        return t_concat
    cumul = 0.0
    for s, e in ranges:
        dur = e - s
        if t_concat <= cumul + dur:
            return s + (t_concat - cumul)
        cumul += dur
    # Past the end — shouldn't happen for words within concat range,
    # but clamp to the last range's end so we don't return nonsense.
    s_last, e_last = ranges[-1]
    return e_last


def upload_to_gcs(local: Path, bucket_name: str, blob_name: str, project: str | None = None) -> str:
    """Returns gs:// URI. Skips re-upload if blob already exists (object
    name is video-ID-keyed so this is safe across runs).

    `project` is required when ADC is the impersonated-SA variant —
    that credential type doesn't carry a `project_id` field, so
    storage.Client() can't auto-detect.
    """
    from google.cloud import storage
    client = storage.Client(project=project) if project else storage.Client()
    bucket = client.bucket(bucket_name)
    blob   = bucket.blob(blob_name)
    if not blob.exists():
        blob.upload_from_filename(str(local))
    return f"gs://{bucket_name}/{blob_name}"


def batch_recognize(gs_uri: str, project: str, language: str,
                     model: str = "chirp_2") -> list[dict]:
    """Submit GCP STT v2 batchRecognize, wait, return word-level offsets:
        [{"word": "<text>", "start_s": float, "end_s": float}, ...]

    On empty response, logs the full BatchRecognizeFileResult shape so
    the operator can see whether the API surfaced an error, returned
    an empty transcript, or returned alternatives without words. Falls
    back automatically to model="long" if `model="chirp_2"` returns
    nothing — chirp_2 is newer and occasionally returns zero results
    for some language/audio combos, "long" is a battle-tested fallback
    that covers all language codes.
    """
    from google.cloud.speech_v2 import SpeechClient
    from google.cloud.speech_v2.types import (
        BatchRecognizeFileMetadata, BatchRecognizeRequest,
        InlineOutputConfig, RecognitionConfig, RecognitionFeatures,
        AutoDetectDecodingConfig, RecognitionOutputConfig,
    )

    def _run(use_model: str) -> tuple[list[dict], object]:
        client = SpeechClient(client_options={"api_endpoint": GCP_STT_ENDPOINT})
        config = RecognitionConfig(
            auto_decoding_config=AutoDetectDecodingConfig(),
            language_codes=[language],
            model=use_model,
            features=RecognitionFeatures(
                enable_word_time_offsets=True,
                enable_automatic_punctuation=True,
            ),
        )
        files = [BatchRecognizeFileMetadata(uri=gs_uri)]
        request = BatchRecognizeRequest(
            recognizer=f"projects/{project}/locations/{GCP_REGION}/recognizers/_",
            config=config,
            files=files,
            recognition_output_config=RecognitionOutputConfig(
                inline_response_config=InlineOutputConfig(),
            ),
        )
        log.info(f"batchRecognize: model={use_model} lang={language} → {gs_uri}")
        operation = client.batch_recognize(request=request)
        # 2 h cap. chirp_2 on 3 h audio typically finishes in ~45 min.
        response = operation.result(timeout=7200)
        return _extract_words(response, gs_uri), response

    words, response = _run(model)
    if words:
        return words

    # Diagnostic logging — surface whatever the API returned so the
    # operator (or future debugging) can see WHY there are no words.
    _log_empty_response(response, gs_uri, model)

    # Auto-fallback to "long" — only attempt if we tried chirp_2. The
    # long model accepts the same audio + language code and has wider
    # baseline coverage. If it also returns nothing, the issue is the
    # audio (silent, wrong language, etc) not the model.
    if model == "chirp_2":
        log.warning(f"chirp_2 returned no words for {language} — retrying with model=long")
        try:
            words, response = _run("long")
            if words:
                log.info(f"long model recovered {len(words)} words")
                return words
            _log_empty_response(response, gs_uri, "long")
        except Exception as e:
            log.error(f"long-model fallback failed: {e!r}")
    return []


def _extract_words(response, gs_uri: str) -> list[dict]:
    file_result = response.results.get(gs_uri)
    if file_result is None or file_result.transcript is None:
        return []
    words: list[dict] = []
    for r in file_result.transcript.results:
        if not r.alternatives:
            continue
        alt = r.alternatives[0]
        for w in alt.words:
            words.append({
                "word":    w.word,
                "start_s": w.start_offset.total_seconds(),
                "end_s":   w.end_offset.total_seconds(),
            })
    return words


def _log_empty_response(response, gs_uri: str, model: str) -> None:
    """Best-effort dump of the BatchRecognize response so we can see
    whether the API reported an error, returned an empty transcript,
    or returned text without word offsets."""
    file_result = response.results.get(gs_uri)
    if file_result is None:
        log.warning(f"batchRecognize[{model}]: no result entry for {gs_uri}. "
                    f"response.results keys = {list(response.results.keys())}")
        return
    # Errors come back on file_result.error (google.rpc.Status).
    err = getattr(file_result, "error", None)
    if err and (getattr(err, "code", 0) or getattr(err, "message", "")):
        log.error(f"batchRecognize[{model}]: file error code={err.code} "
                  f"message={err.message!r}")
        return
    transcript = getattr(file_result, "transcript", None)
    if transcript is None:
        log.warning(f"batchRecognize[{model}]: file_result.transcript is None. "
                    f"file_result fields = {list(file_result.__dict__.keys()) if hasattr(file_result, '__dict__') else 'opaque'}")
        return
    results = list(getattr(transcript, "results", []) or [])
    if not results:
        log.warning(f"batchRecognize[{model}]: transcript.results is empty — "
                    f"model returned no segments. Common causes: audio silent / "
                    f"wrong language code / model doesn't support this language. "
                    f"Audio duration may still have processed; check Cloud Console "
                    f"recognizer logs for warnings.")
        return
    n_alt = sum(1 for r in results if r.alternatives)
    n_words = sum(len(r.alternatives[0].words) for r in results if r.alternatives)
    sample = next((r.alternatives[0].transcript for r in results
                    if r.alternatives and r.alternatives[0].transcript), "")[:200]
    log.warning(f"batchRecognize[{model}]: got {len(results)} segments, "
                f"{n_alt} with alternatives, {n_words} total words. "
                f"First non-empty transcript: {sample!r}")


_SENTENCE_END = re.compile(r"[.?!।॥]$")

def group_words_into_cues(words: list[dict],
                           target_sec: float = 3.0,
                           max_sec:    float = 6.0,
                           silence_break_sec: float = 0.6) -> list[dict]:
    """Walk word offsets and produce subtitle cues. Break when:
      - cue duration ≥ target_sec AND last word ends a sentence
      - cue duration ≥ max_sec (hard cap)
      - silence gap to next word ≥ silence_break_sec
      - cue character length > 84 (≈ 2 × 42-char SRT lines)
    """
    if not words:
        return []
    cues: list[dict] = []
    cur_words: list[dict] = []
    cur_start = words[0]["start_s"]
    cur_text  = ""

    def flush(end_s: float):
        nonlocal cur_words, cur_text, cur_start
        if not cur_words: return
        cues.append({
            "start_s": cur_start, "end_s": end_s,
            "text":    cur_text.strip(),
            "word_count": len(cur_words),
        })
        cur_words = []
        cur_text  = ""

    for i, w in enumerate(words):
        if not cur_words:
            cur_start = w["start_s"]
        cur_words.append(w)
        cur_text = (cur_text + " " + w["word"]).strip()
        dur = w["end_s"] - cur_start
        should_break = False
        if dur >= max_sec:                                           should_break = True
        elif dur >= target_sec and _SENTENCE_END.search(w["word"]):  should_break = True
        elif len(cur_text) > 84:                                     should_break = True
        elif i + 1 < len(words):
            gap = words[i+1]["start_s"] - w["end_s"]
            if dur >= target_sec * 0.6 and gap >= silence_break_sec: should_break = True
        if should_break:
            flush(w["end_s"])
    if cur_words:
        flush(cur_words[-1]["end_s"])
    return cues


def translate_cues_inplace(cues: list[dict], project: str,
                            source_lang: str, target_lang: str,
                            batch_size: int = 100,
                            on_batch: Callable[[int, int], None] | None = None) -> None:
    """Adds `translated` to each cue. Batched. `on_batch(done, total)`
    fires after each batch so the caller can render a progress bar."""
    from google.cloud import translate_v3 as translate
    client = translate.TranslationServiceClient()
    parent = f"projects/{project}/locations/global"
    src = source_lang.split("-")[0]
    tgt = target_lang.split("-")[0]
    total = len(cues)
    done = 0
    for i in range(0, total, batch_size):
        batch = cues[i:i+batch_size]
        contents = [c["text"] for c in batch]
        response = client.translate_text(
            parent=parent,
            contents=contents,
            source_language_code=src,
            target_language_code=tgt,
            mime_type="text/plain",
        )
        for cue, tr in zip(batch, response.translations):
            cue["translated"] = tr.translated_text
        done += len(batch)
        if on_batch:
            on_batch(done, total)


def _srt_timestamp(seconds: float) -> str:
    if seconds < 0: seconds = 0
    total_ms = int(round(seconds * 1000))
    return f"{total_ms//3600000:02d}:{(total_ms//60000)%60:02d}:{(total_ms//1000)%60:02d},{total_ms%1000:03d}"


def write_srt(cues: list[dict], path: Path, rules: list[_Rule]) -> int:
    """Writes the SRT file. Returns the number of cues that had at least
    one rule fire (useful for the UI summary)."""
    fired_count = 0
    with path.open("w", encoding="utf-8") as f:
        for i, cue in enumerate(cues, start=1):
            raw = (cue.get("translated") or cue.get("text") or "").strip()
            if not raw: continue
            corrected, fired = apply_rules(raw, rules)
            if fired:
                fired_count += 1
                cue["fired"]  = fired
                cue["raw_en"] = raw
            cue["final"] = corrected
            f.write(f"{i}\n")
            f.write(f"{_srt_timestamp(cue['start_s'])} --> {_srt_timestamp(cue['end_s'])}\n")
            f.write(corrected + "\n\n")
    return fired_count


_PREVIEW_HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><title>VOD preview — {video_id}</title>
<style>
body{{margin:0;padding:1rem;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#111;color:#ddd}}
h1{{font-size:1rem;font-weight:500;margin:0 0 0.6rem;color:#aaa}}
video{{width:100%;max-width:1280px;background:#000}}
video::cue{{font-size:1.3rem;background:rgba(0,0,0,0.7)}}
.meta{{font-family:"JetBrains Mono",monospace;font-size:0.78rem;color:#888;margin-top:0.8rem}}
.meta a{{color:#ff8c00}}
details{{margin-top:1.2rem}}
summary{{cursor:pointer;color:#ff8c00;font-size:0.85rem}}
table{{border-collapse:collapse;width:100%;margin-top:0.6rem;font-family:"JetBrains Mono",monospace;font-size:0.72rem}}
td,th{{padding:0.35rem 0.55rem;border-bottom:1px solid #333;vertical-align:top}}
th{{text-align:left;color:#999;font-weight:500}}
.ts{{color:#888;white-space:nowrap}}
.fired td{{background:rgba(255,140,0,0.06)}}
.fired .raw{{color:#888;text-decoration:line-through}}
.fired .final{{color:#ffc88a}}
</style></head><body>
<h1>VOD preview — {video_id} &middot; {n_cues} cues &middot; {n_fired} with rules fired</h1>
<video controls crossorigin="anonymous">
  <source src="{mp4_name}" type="video/mp4">
  <track default kind="subtitles" srclang="en" label="English (reprocessed)" src="{srt_name}">
</video>
<div class="meta">When the timing or substitutions look right, upload <a href="{srt_name}">{srt_name}</a> to YouTube Studio (Subtitles → Add language → Upload file → With timing).</div>
<details><summary>Cue audit ({n_fired} with rule substitutions)</summary>
<table><thead><tr><th>#</th><th>Start</th><th>End</th><th>Caption</th><th>Rules</th></tr></thead>
<tbody>
{rows}
</tbody></table></details></body></html>
"""


def write_html_preview(cues: list[dict], path: Path, video_id: str,
                        mp4_name: str, srt_name: str) -> None:
    n_fired = sum(1 for c in cues if c.get("fired"))
    rows = []
    for i, c in enumerate(cues, start=1):
        ts = (f"<td class='ts'>{_srt_timestamp(c['start_s'])}</td>"
              f"<td class='ts'>{_srt_timestamp(c['end_s'])}</td>")
        final = html.escape(c.get("final") or "")
        if c.get("fired"):
            raw_en = html.escape(c.get("raw_en") or "")
            cell = (f"<div class='raw'>{raw_en}</div>"
                    f"<div class='final'>{final}</div>")
            rows.append(f"<tr class='fired'><td>{i}</td>{ts}<td>{cell}</td>"
                        f"<td>{html.escape(', '.join(c.get('fired') or []))}</td></tr>")
        else:
            rows.append(f"<tr><td>{i}</td>{ts}<td>{final}</td><td></td></tr>")
    body = _PREVIEW_HTML.format(
        video_id=html.escape(video_id),
        mp4_name=html.escape(mp4_name),
        srt_name=html.escape(srt_name),
        n_cues=len(cues), n_fired=n_fired,
        rows="\n".join(rows),
    )
    path.write_text(body, encoding="utf-8")


# ── Async orchestrator ──────────────────────────────────────────────────────

@dataclass
class VodResult:
    video_id:    str
    out_dir:     Path
    mp4_path:    Path
    flac_path:   Path
    srt_path:    Path
    html_path:   Path
    words_path:  Path
    cue_count:   int
    rules_fired_count: int


@dataclass
class VodPipeline:
    """Async wrapper around the sync stage functions. Each `await self._run(...)`
    runs its body in the default executor so the event loop stays responsive.

    Progress events are emitted before AND after each stage (the "before"
    event sets pct=0 and elapsed=0; the "after" sets pct=1 and the actual
    elapsed). This gives the UI a smooth two-step transition per stage
    without having to peek inside the sync work.

    Range selection — after the download stage the pipeline pauses on
    `ranges_event` so an operator can pick non-contiguous segments of
    the video to transcribe (skipping kirtans, bhajans, intermissions,
    etc). Resume by setting `self.ranges` and `.set()`-ing the event.
    Empty ranges or set-without-ranges = transcribe the full video
    (legacy behaviour).
    """
    video_id:    str
    bucket:      str
    project:     str
    results_dir: Path
    rules_path:  Path
    source_lang: str  = "gu-IN"
    target_lang: str  = "en"
    skip_download: bool = False
    skip_stt:      bool = False
    on_progress:   ProgressCallback | None = None
    # Initial ranges to use without pausing — used by the CLI and by
    # re-runs where the operator already picked. When None, the
    # pipeline pauses after download and waits for resume_with_ranges().
    initial_ranges: list[Range] | None = None

    # Filled in during run()
    out_dir:   Path = field(init=False)
    mp4_path:  Path = field(init=False)
    flac_path: Path = field(init=False)
    words_path: Path = field(init=False)
    srt_path:  Path = field(init=False)
    html_path: Path = field(init=False)
    ranges:    list[Range] = field(init=False, default_factory=list)
    ranges_event: asyncio.Event = field(init=False)

    def __post_init__(self):
        self.out_dir    = self.results_dir / f"vod-{self.video_id}"
        self.mp4_path   = self.out_dir / f"vod-{self.video_id}.mp4"
        self.flac_path  = self.out_dir / f"vod-{self.video_id}.flac"
        self.words_path = self.out_dir / f"vod-{self.video_id}-words.json"
        self.srt_path   = self.out_dir / f"vod-{self.video_id}-en.srt"
        self.html_path  = self.out_dir / f"vod-{self.video_id}-preview.html"
        self.ranges = list(self.initial_ranges) if self.initial_ranges else []
        self.ranges_event = asyncio.Event()
        if self.initial_ranges is not None:
            # Caller pre-supplied ranges (CLI, or operator already picked
            # in a previous run); skip the awaiting-ranges pause.
            self.ranges_event.set()

    def resume_with_ranges(self, ranges: list[Range]) -> None:
        """External resume path — used by the job-orchestrator HTTP
        handler to deliver operator-picked ranges to a paused pipeline.
        Empty list = transcribe full video.
        """
        self.ranges = list(ranges) if ranges else []
        self.ranges_event.set()

    async def _emit(self, ev: StageEvent) -> None:
        if self.on_progress is None: return
        try:
            ret = self.on_progress(ev)
            if asyncio.iscoroutine(ret):
                await ret
        except Exception as e:
            log.warning(f"on_progress raised {e!r} — ignoring")

    async def _run_stage(self, stage: Stage, fn, *args, **kwargs):
        """Run `fn(*args, **kwargs)` in an executor with before/after progress
        events. Returns whatever fn returned."""
        await self._emit(StageEvent(stage=stage, pct=0.0, detail=STAGE_LABELS[stage]))
        t0 = time.time()
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, lambda: fn(*args, **kwargs))
        await self._emit(StageEvent(
            stage=stage, pct=1.0, detail=STAGE_LABELS[stage],
            elapsed_s=time.time() - t0,
        ))
        return result

    async def run(self) -> VodResult:
        self.out_dir.mkdir(parents=True, exist_ok=True)

        # 1. MP4 download (skip if cached or --skip-download). Streamed
        # progress: yt-dlp's per-line updates become StageEvent emissions
        # so the React Reprocess tab can render a live percentage bar.
        if not self.skip_download and not self.mp4_path.exists():
            await self._emit(StageEvent(stage=Stage.DOWNLOAD_VIDEO, pct=0.0,
                                         detail="Starting download…"))
            t0 = time.time()
            loop = asyncio.get_running_loop()
            main_loop = loop  # captured for cross-thread schedule

            def _on_dl_pct(pct: float, detail: str):
                ev = StageEvent(stage=Stage.DOWNLOAD_VIDEO, pct=pct, detail=detail)
                if not self.on_progress: return
                try:
                    ret = self.on_progress(ev)
                    if asyncio.iscoroutine(ret):
                        fut = asyncio.run_coroutine_threadsafe(ret, main_loop)
                        # Bounded wait — don't block yt-dlp parsing if the
                        # WS broadcast is slow.
                        try: fut.result(timeout=2)
                        except Exception: pass
                except Exception as e:
                    log.debug(f"download progress forward: {e!r}")

            await loop.run_in_executor(
                None,
                lambda: download_video_step(self.video_id, self.mp4_path, on_pct=_on_dl_pct),
            )
            await self._emit(StageEvent(
                stage=Stage.DOWNLOAD_VIDEO, pct=1.0,
                detail=STAGE_LABELS[Stage.DOWNLOAD_VIDEO],
                elapsed_s=time.time() - t0,
            ))
        else:
            await self._emit(StageEvent(stage=Stage.DOWNLOAD_VIDEO, pct=1.0,
                                         detail="Cached — skipped"))

        # 1b. Pause for the operator to pick which ranges of the video
        # to transcribe. If `initial_ranges` was passed in (CLI / re-run
        # with ranges already known) the event is pre-set in
        # __post_init__ and this `await` is a no-op. Otherwise the
        # job-orchestrator surfaces an "awaiting_ranges" status to the
        # UI and waits for the operator to call resume_with_ranges().
        if not self.ranges_event.is_set():
            await self._emit(StageEvent(
                stage=Stage.AWAITING_RANGES, pct=None,
                detail="Pick the video sections to transcribe in the UI",
            ))
            await self.ranges_event.wait()
            await self._emit(StageEvent(
                stage=Stage.AWAITING_RANGES, pct=1.0,
                detail=(f"Operator selected {len(self.ranges)} range(s)"
                        if self.ranges else "Operator chose full video"),
            ))

        # 2. Audio extract. Cache only valid when the existing FLAC was
        # produced with the same range selection; the FLAC name doesn't
        # encode ranges, so we just rebuild every time ranges differ
        # from a sentinel. Simplest correct behaviour: always re-extract
        # when ranges are non-empty, reuse cache only for full-video.
        need_extract = (
            not self.flac_path.exists()
            or bool(self.ranges)
        )
        if need_extract:
            await self._run_stage(Stage.EXTRACT_AUDIO, extract_audio_step,
                                   self.mp4_path, self.flac_path, self.ranges or None)
        else:
            await self._emit(StageEvent(stage=Stage.EXTRACT_AUDIO, pct=1.0,
                                         detail="Cached — skipped"))

        # 3. STT (skip if cached words file present)
        if self.skip_stt and self.words_path.exists():
            words = json.loads(self.words_path.read_text())
            await self._emit(StageEvent(stage=Stage.STT_BATCH, pct=1.0,
                                         detail=f"Cached — {len(words)} words"))
        else:
            await self._emit(StageEvent(stage=Stage.UPLOAD_GCS, pct=0.0,
                                         detail=STAGE_LABELS[Stage.UPLOAD_GCS]))
            t0 = time.time()
            loop = asyncio.get_running_loop()
            blob_name = f"vod-reprocess/{self.video_id}.flac"
            gs_uri = await loop.run_in_executor(
                None, upload_to_gcs, self.flac_path, self.bucket, blob_name, self.project
            )
            await self._emit(StageEvent(stage=Stage.UPLOAD_GCS, pct=1.0,
                                         detail=gs_uri,
                                         elapsed_s=time.time() - t0))

            await self._emit(StageEvent(stage=Stage.STT_BATCH, pct=0.0,
                                         detail="Running chirp_2 (this is the long part — typically 30–50 min for a 3 h VOD)"))
            t0 = time.time()
            words = await loop.run_in_executor(
                None, batch_recognize, gs_uri, self.project, self.source_lang,
            )
            if not words:
                raise RuntimeError(
                    f"GCP STT returned no words for language={self.source_lang}. "
                    "Tried chirp_2 then long — both empty. Check the server log "
                    "(or the Debug bug icon in the UI) for the BatchRecognize "
                    "response details. Common causes: wrong --source-lang, "
                    "silent audio, or the FLAC wasn't extracted as 16-bit "
                    "(delete the cached vod-*.flac and re-run)."
                )
            self.words_path.write_text(json.dumps(words, ensure_ascii=False))
            await self._emit(StageEvent(stage=Stage.STT_BATCH, pct=1.0,
                                         detail=f"Got {len(words)} words",
                                         elapsed_s=time.time() - t0))

        # 3b. Remap word offsets back to the original video timeline.
        # When ranges were used the STT processed a concatenated audio
        # stream — words have concat-relative timestamps. Remap each
        # word's start/end via the offset table so SRT cues land in
        # original-video coords and play in sync on YouTube.
        if self.ranges:
            words = [
                {
                    "word":    w["word"],
                    "start_s": remap_concat_to_original(w["start_s"], self.ranges),
                    "end_s":   remap_concat_to_original(w["end_s"],   self.ranges),
                } for w in words
            ]

        # 4. Cue grouping (fast)
        cues = await self._run_stage(Stage.CUE_GROUP, group_words_into_cues, words)

        # 5. Translation (batched, can show partial pct)
        await self._emit(StageEvent(stage=Stage.TRANSLATE, pct=0.0,
                                     detail=f"Translating {len(cues)} cues"))
        t0 = time.time()
        loop = asyncio.get_running_loop()
        # The translate fn calls on_batch(done, total) — we need a way to
        # forward that as a progress event from the sync thread. Use a
        # threadsafe coroutine schedule via asyncio.run_coroutine_threadsafe.
        main_loop = asyncio.get_running_loop()

        def _on_batch(done: int, total: int):
            ev = StageEvent(stage=Stage.TRANSLATE, pct=done/total,
                             detail=f"{done}/{total} cues")
            if self.on_progress is None: return
            try:
                ret = self.on_progress(ev)
                if asyncio.iscoroutine(ret):
                    fut = asyncio.run_coroutine_threadsafe(ret, main_loop)
                    fut.result(timeout=5)
            except Exception as e:
                log.warning(f"_on_batch progress fwd: {e!r}")

        await loop.run_in_executor(
            None,
            lambda: translate_cues_inplace(cues, self.project,
                                            self.source_lang, self.target_lang,
                                            on_batch=_on_batch),
        )
        await self._emit(StageEvent(stage=Stage.TRANSLATE, pct=1.0,
                                     detail=f"{len(cues)} cues translated",
                                     elapsed_s=time.time() - t0))

        # 6. Apply rules + write SRT + write preview HTML
        rules = load_rules(self.rules_path)
        await self._emit(StageEvent(stage=Stage.APPLY_RULES, pct=1.0,
                                     detail=f"{sum(1 for r in rules if r.enabled and r._compiled)} active rules"))

        fired_count = await self._run_stage(
            Stage.WRITE_OUTPUTS,
            lambda: (write_srt(cues, self.srt_path, rules),
                     write_html_preview(cues, self.html_path, self.video_id,
                                         self.mp4_path.name, self.srt_path.name))[0]
        )

        return VodResult(
            video_id  = self.video_id,
            out_dir   = self.out_dir,
            mp4_path  = self.mp4_path,
            flac_path = self.flac_path,
            srt_path  = self.srt_path,
            html_path = self.html_path,
            words_path = self.words_path,
            cue_count = len(cues),
            rules_fired_count = fired_count,
        )
