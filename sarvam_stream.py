#!/usr/bin/env python3
"""Live Gujarati→English captions via Sarvam Saaras v3 streaming.

Usage:
    # from a wav file (real-time paced):
    uv run python sarvam_stream.py samples/sample1.wav

    # live from QU-SB USB:
    uv run python sarvam_stream.py --live

    # with seconds cap for quick testing:
    uv run python sarvam_stream.py samples/sample1.wav --seconds 30

    # save JSONL for viewer:
    uv run python sarvam_stream.py samples/sample1.wav --out results/sarvam.jsonl

Protocol: WebSocket, mode="translate" → Sarvam returns English directly.
high_vad_sensitivity=True → speech_end triggers after 0.5 s silence.
"""

import argparse
import asyncio
import base64
import json
import os
import sys
import time

from dotenv import load_dotenv

# How long to keep listening after the last audio frame, so trailing finals
# still arrive before the tool exits rather than being cut off.
FINAL_GRACE_SEC = 8.0
load_dotenv()


async def stream_from_file(path: str, max_seconds: float | None, chunk_ms: int = 500):
    """Yield (pcm_bytes, received_at) chunks from a wav file, real-time paced."""
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly
    from math import gcd

    data, sr = sf.read(path, dtype="int16", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1).astype(np.int16)
    if max_seconds:
        data = data[: int(sr * max_seconds)]
    target_sr = 16000
    if sr != target_sr:
        g = gcd(sr, target_sr)
        data = resample_poly(data.astype(np.float32), target_sr // g, sr // g)
        data = np.clip(data, -32768, 32767).astype(np.int16)
        sr = target_sr

    samples_per_chunk = int(sr * chunk_ms / 1000)
    start = time.time()
    for i in range(0, len(data), samples_per_chunk):
        yield data[i : i + samples_per_chunk].tobytes(), time.time()
        next_emit = start + (i + samples_per_chunk) / sr
        await asyncio.sleep(max(0.0, next_emit - time.time()))


async def stream_from_mic(device=None, chunk_ms: int = 500):
    """Yield (pcm_bytes, received_at) from a live input device."""
    import numpy as np
    import sounddevice as sd

    sr = 16000
    samples = int(sr * chunk_ms / 1000)
    q: asyncio.Queue = asyncio.Queue(maxsize=32)
    loop = asyncio.get_running_loop()

    def cb(indata, frames, t, status):
        pcm = (indata[:, 0] * 32767).clip(-32768, 32767).astype(np.int16).tobytes()
        loop.call_soon_threadsafe(q.put_nowait, (pcm, time.time()))

    with sd.InputStream(device=device, samplerate=sr, channels=1, blocksize=samples, dtype="float32", callback=cb):
        while True:
            yield await q.get()


async def run(audio_gen, out_file=None):
    from sarvamai import AsyncSarvamAI

    api_key = os.environ.get("SARVAM_API_KEY")
    if not api_key:
        sys.exit("SARVAM_API_KEY not set in .env")

    client = AsyncSarvamAI(api_subscription_key=api_key)
    audio_start = time.time()
    fh = open(out_file, "w") if out_file else None

    def emit(text, is_final, received_at, extra=None):
        latency = time.time() - received_at
        audio_offset = received_at - audio_start
        tag = "FINAL  " if is_final else "partial"
        print(f"[{audio_offset:6.2f}s +{latency:.2f}s] {tag}: {text}", flush=True)
        if fh:
            fh.write(json.dumps({
                "backend": "sarvam",
                "text": text,
                "is_final": is_final,
                "latency_s": latency,
                "emitted_at": time.time(),
                "source_chunk_received_at": received_at,
                **({"extra": extra} if extra else {}),
            }) + "\n")
            fh.flush()

    try:
        async with client.speech_to_text_streaming.connect(
            model="saaras:v3",
            mode="translate",
            language_code="gu-IN",
            sample_rate=16000,
            input_audio_codec="pcm_s16le",
            high_vad_sensitivity=True,
            vad_signals=True,
        ) as ws:

            async def sender():
                async for pcm, received_at in audio_gen:
                    await ws.transcribe(
                        audio=base64.b64encode(pcm).decode(),
                        encoding="audio/wav",   # pydantic literal; codec set at connect-time
                        sample_rate=16000,
                    )
                try:
                    await ws.flush()
                except Exception:
                    pass

            sender_task = asyncio.create_task(sender())
            last_received = audio_start

            # A finite wav file ends, the service goes quiet, and `async for msg
            # in ws` waits forever. This tool used to hang there, which reads as
            # "no captions" rather than "finished" — the failure that made it
            # dangerous. The receive loop is a task so it can simply be
            # cancelled once the audio is done and the trailing finals have had
            # their grace period. Cancelling is deliberate rather than closing
            # the socket: it does not depend on what the vendor's wrapper
            # exposes, which is what a debug tool needs least.
            async def receive():
                nonlocal last_received
                async for msg in ws:
                    # Unwrap exactly the way the server does. This tool used
                    # to branch on `translation` / `transcript` envelopes and
                    # call .get() on a pydantic object, so nothing ever matched
                    # and it printed silence — while the service was returning
                    # everything. A debug tool that reports "no captions" during
                    # a working session is worse than no debug tool, because it
                    # sends someone chasing an outage that is not happening.
                    if hasattr(msg, "model_dump"):
                        msg = msg.model_dump()
                    elif not isinstance(msg, dict):
                        msg = getattr(msg, "__dict__", {}) or {}
                    t = msg.get("type", "")
                    data = msg.get("data") or {}
                    if hasattr(data, "model_dump"):
                        data = data.model_dump()
                    if not isinstance(data, dict):
                        data = getattr(data, "__dict__", {}) or {}

                    if t == "events":
                        if str(data.get("signal_type", "")).upper() == "START_SPEECH":
                            last_received = time.time()
                    elif t == "data":
                        text = (data.get("transcript") or data.get("text") or "").strip()
                        if text:
                            emit(text, True, last_received)
            recv_task = asyncio.create_task(receive())
            try:
                await sender_task
                await asyncio.sleep(FINAL_GRACE_SEC)
            finally:
                recv_task.cancel()
                sender_task.cancel()
    finally:
        if fh:
            fh.close()



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file", nargs="?", help="Path to wav file")
    parser.add_argument("--live", action="store_true", help="Use live mic input")
    parser.add_argument("--device", help="Audio device name/index for live capture")
    parser.add_argument("--seconds", type=float, help="Cap replay to N seconds")
    parser.add_argument("--out", help="Write JSONL to this path")
    args = parser.parse_args()

    if args.live:
        gen = stream_from_mic(args.device)
    elif args.file:
        gen = stream_from_file(args.file, args.seconds)
    else:
        parser.error("Provide a wav file path or --live")

    asyncio.run(run(gen, args.out))


if __name__ == "__main__":
    main()
