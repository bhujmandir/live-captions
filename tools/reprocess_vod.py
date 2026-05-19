#!/usr/bin/env python3
"""
Thin CLI wrapper around `tools.vod_pipeline.VodPipeline`.

Usage:
    cd captions
    uv run python -m tools.reprocess_vod --video <YT-URL>

Most of the work lives in `tools/vod_pipeline.py` — this file just parses
args, sets up logging, drives the pipeline with a print-based progress
callback, and prints the final paths.

For the operator UI version of the same pipeline see the Reprocess tab
in the React app served by `live_captions.py` at `/`.
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from tools.vod_pipeline import (
    VodPipeline, Stage, StageEvent, STAGE_LABELS,
    parse_video_id, check_prereqs, detect_project,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("reprocess_vod")


def _print_progress(ev: StageEvent) -> None:
    """Compact one-line-per-stage update: ★ STAGE  detail (XXs)."""
    bar = "★"
    pct = f"{ev.pct*100:5.1f}%" if ev.pct is not None else "  ···"
    el  = f"{ev.elapsed_s:6.1f}s" if ev.elapsed_s else "       "
    print(f"  {bar} {ev.stage.value:<16} {pct} {el}  {ev.detail}", flush=True)


async def _run(args):
    check_prereqs()
    video_id = parse_video_id(args.video)
    if not args.bucket:
        sys.exit("--bucket required (or set GCS_BUCKET env var)")
    project = args.project or detect_project()
    if not project:
        sys.exit("Could not detect GCP project — pass --project or set GCP_PROJECT")

    if args.out:
        results_dir = Path(args.out).parent
    else:
        results_dir = Path(__file__).parent.parent / "results"
    rules_path = Path(__file__).parent.parent / "rules.json"

    log.info(f"Project: {project}  Bucket: {args.bucket}  Video: {video_id}")
    log.info(f"Output:  {results_dir / f'vod-{video_id}'}")

    pipeline = VodPipeline(
        video_id      = video_id,
        bucket        = args.bucket,
        project       = project,
        results_dir   = results_dir,
        rules_path    = rules_path,
        source_lang   = args.source_lang,
        target_lang   = args.target_lang,
        skip_download = args.skip_download,
        skip_stt      = args.skip_stt,
        on_progress   = _print_progress,
    )
    result = await pipeline.run()

    print()
    print("─" * 60)
    print(f"  Reprocessed VOD {result.video_id}")
    print(f"  Cues:        {result.cue_count}")
    print(f"  Rules fired: {result.rules_fired_count} cue(s)")
    print(f"  SRT:         {result.srt_path}")
    print(f"  Preview:     open {result.html_path}")
    print("─" * 60)


def main():
    p = argparse.ArgumentParser(
        prog="reprocess_vod",
        description="Reprocess a YouTube VOD's captions with GCP STT v2 + Translate v3 + rules.json",
    )
    p.add_argument("--video", required=True, help="YouTube URL or 11-char video ID")
    p.add_argument("--bucket", default=os.environ.get("GCS_BUCKET"),
                   help="GCS bucket (us-central1) for the audio upload (or GCS_BUCKET env var)")
    p.add_argument("--project", default=None,
                   help="GCP project id (defaults to project_id from creds / gcloud config)")
    p.add_argument("--source-lang", default="gu-IN", help="Spoken language code (default gu-IN)")
    p.add_argument("--target-lang", default="en",     help="Caption language code (default en)")
    p.add_argument("--out", default=None,
                   help="Output directory (default: ../results/vod-<id>)")
    p.add_argument("--skip-download", action="store_true",
                   help="Reuse existing MP4/FLAC in the output dir (no yt-dlp call)")
    p.add_argument("--skip-stt", action="store_true",
                   help="Reuse existing vod-<id>-words.json (no GCP STT call)")
    args = p.parse_args()
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        sys.exit(130)
    except RuntimeError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
