"""Stream a recording through the real caption pipeline, offline.

The point is fidelity, not convenience: this drives `sarvam_loop` itself, so
what comes out is what the hall would have seen — the same speech service,
the same silence gate, the same sentence assembly, the same translator
ladder. A harness that reimplemented any of that would measure the harness.

    uv run python tools/replay_audio.py --wav swami.wav --out run.jsonl \
        [--start 600 --seconds 300] [--speed 1.0]

Existing recordings are the only way to tune against a speaker before the
event, and a recording can be replayed as many times as a change needs. A
live katha happens once.
"""
from __future__ import annotations

import argparse, asyncio, json, os, sys, time, wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import live_captions as lc

FRAME_MS = 500
RATE = 16000


class RecordingBroadcaster:
    """Captures what the surfaces would have shown."""

    def __init__(self, sink):
        self.sink = sink
        self.finals: list[dict] = []
        self.started = time.time()

    async def send(self, msg: dict) -> None:
        if msg.get("type") != "final":
            return
        row = {
            "elapsed_s": round(time.time() - self.started, 2),
            "english": msg.get("text"),
            "raw": msg.get("raw"),
        }
        self.finals.append(row)
        self.sink.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.sink.flush()
        print(f"[{row['elapsed_s']:7.1f}s] {row['english']}", flush=True)


async def wav_frames(path: str, *, start: float, seconds: float, speed: float,
                     stop_event: asyncio.Event):
    """Yield (pcm, timestamp) like the capture path does.

    Paced at real time by default. Speeding it up is tempting and mostly
    wrong: the service's voice-activity detection reads pause LENGTH, so a
    2x replay halves every silence and changes where utterances are cut —
    the very thing being measured.
    """
    with wave.open(path, "rb") as w:
        assert w.getframerate() == RATE, f"expected {RATE} Hz, got {w.getframerate()}"
        assert w.getnchannels() == 1, "expected mono"
        if start:
            w.setpos(int(start * RATE))
        n = int(RATE * FRAME_MS / 1000)
        budget = seconds if seconds else float("inf")
        sent = 0.0
        gap = (FRAME_MS / 1000) / max(speed, 0.01)
        while sent < budget and not stop_event.is_set():
            pcm = w.readframes(n)
            if len(pcm) < n * 2:
                break
            yield pcm, time.time()
            sent += FRAME_MS / 1000
            await asyncio.sleep(gap)
    stop_event.set()


async def main(argv: "list[str] | None" = None):
    # argv is a parameter so an importer can drive this without
    # sys.argv. The __main__ guard alone only made the module
    # importable; a main() welded to argv is still unusable from a
    # test or a scoring harness, which is the thing this is for.
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--seconds", type=float, default=0.0, help="0 = whole file")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--source", default="gu-IN")
    ap.add_argument("--target", default="en-IN")
    args = ap.parse_args(argv)

    if not os.environ.get("SARVAM_API_KEY"):
        sys.exit("SARVAM_API_KEY is not set")

    state = {"source": args.source, "target": args.target}
    pipe = lc.derive_pipeline(args.source, args.target)
    print(f"pipeline: {pipe['sarvam_mode']} · source recorded as "
          f"{pipe['saaras_output_lang']} · separate translate step: "
          f"{pipe['needs_translation']}")
    print(f"translator: {lc.TRANSLATOR}"
          + (f" ({lc.GEMINI_MODEL})" if lc.TRANSLATOR != "sarvam" else ""))
    print(f"assembly: quiet {lc.SENTENCE_QUIET_SEC}s · wait "
          f"{lc.SENTENCE_MAX_WAIT_SEC}s · floor {lc.SENTENCE_MIN_WORDS} words\n")

    stop_event = asyncio.Event()
    with open(args.out, "w", encoding="utf-8") as sink:
        bc = RecordingBroadcaster(sink)
        await lc.sarvam_loop(
            wav_frames(args.wav, start=args.start, seconds=args.seconds,
                       speed=args.speed, stop_event=stop_event),
            bc, False, stop_event, state)
        print(f"\n{len(bc.finals)} captions -> {args.out}")

if __name__ == "__main__":
    # Without this guard the module cannot be imported without RUNNING, which
    # is why the replay -- the instrument this project trusts over a live
    # katha -- could never be driven by a test or scored automatically.
    # reprocess_vod.py has always had it; these two were missed.
    asyncio.run(main())
