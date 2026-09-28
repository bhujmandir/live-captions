"""The signal path itself, in a real process, with a real signal.

The unit tests next door prove `InterruptPolicy` decides correctly. They do
not prove the decision is ever *reached* — and the thing that failed in the
hall on 3 September was not a decision, it was a signal arriving and the
process ending. A policy nobody wired up would pass every test in
`test_interrupt_policy.py`.

So this spawns the server's interrupt handling in a child process, sends it
real `SIGINT`s, and watches whether the child is still breathing.

⚠️ **This covers POSIX only, and that is an honest gap, not an oversight.**
The outage was on Windows, where a console control event is delivered to
*every* process attached to the console — and on the mandir PC that console
holds a chain, `start-captions.bat` → `powershell -File deploy.ps1 -StartOnly`
→ python. Python refusing the interrupt does not stop `cmd.exe` asking
"Terminate batch job (Y/N)?", and if that chain tears the console down then
Python dies by `CTRL_CLOSE_EVENT`, which cannot be caught at all. Nothing on a
Mac can test that. `WINDOWS.md` records it as unproven; this file proves the
half that can be proven here.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX signals only; the Windows console-control path cannot be "
           "driven from here and is recorded as unproven in WINDOWS.md",
)


HARNESS = """
import asyncio, sys
sys.path.insert(0, {repo!r})
import live_captions as lc

async def main():
    ev = asyncio.Event()
    lc._install_interrupt_policy(asyncio.get_running_loop(), ev)
    print("READY", flush=True)
    await ev.wait()
    print("STOPPED", flush=True)

asyncio.run(main())
"""


def _start_harness(presses: str = "3"):
    env = dict(os.environ)
    env["CAPTION_INTERRUPT_PRESSES"] = presses
    # Keep the child's own logging out of the test output's way.
    env.setdefault("CAPTION_LOG_DIR", "")

    proc = subprocess.Popen(
        [sys.executable, "-c", HARNESS.format(repo=str(REPO_ROOT))],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
        text=True,
    )

    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if line.strip() == "READY":
            return proc
        if proc.poll() is not None:
            raise AssertionError("harness died before it was ready")
    proc.kill()
    raise AssertionError("harness never became ready")


def _still_running(proc, settle: float = 1.0) -> bool:
    time.sleep(settle)
    return proc.poll() is None


def test_one_real_sigint_does_not_end_the_process():
    """The 3 September failure, driven end to end.

    One interrupt arrives at a running server. It must still be running a
    second later. This is the assertion the unit tests cannot make, because it
    is about the handler being installed at all.
    """
    proc = _start_harness()
    try:
        proc.send_signal(signal.SIGINT)
        assert _still_running(proc), "a single SIGINT ended the process"

        proc.send_signal(signal.SIGINT)
        assert _still_running(proc), "a second, separated SIGINT ended the process"
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_a_real_burst_of_sigints_ends_it_cleanly():
    """A developer at a terminal still gets out, and leaves by the front door.

    Three interrupts in quick succession must exit — and must exit through the
    shutdown event, printing STOPPED, rather than by an uncaught
    KeyboardInterrupt tearing out of the loop. That difference is the whole
    reason the handler sets an event instead of raising: raising skips the
    teardown that withdraws the mDNS advert and drains the jobs worker.
    """
    proc = _start_harness()
    try:
        for _ in range(3):
            proc.send_signal(signal.SIGINT)
            time.sleep(0.05)

        stdout, _ = proc.communicate(timeout=15)
        assert proc.returncode == 0, f"expected a clean exit, got {proc.returncode}"
        assert "STOPPED" in stdout, "exited without going through the shutdown event"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


def test_presses_zero_makes_the_process_refuse_every_interrupt():
    """The control-room setting, proven rather than asserted.

    `CAPTION_INTERRUPT_PRESSES=0` is what the mandir PC should run. No number
    of interrupts may end it; `stop-captions` remains the way out.
    """
    proc = _start_harness(presses="0")
    try:
        for _ in range(10):
            proc.send_signal(signal.SIGINT)
            time.sleep(0.02)

        assert _still_running(proc), "CAPTION_INTERRUPT_PRESSES=0 still let it die"
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_a_mistyped_press_count_does_not_stop_the_server_starting():
    """#61: 'it must fail toward STAYING UP.'

    A blank or mistyped line in `.env` must cost the default, never the boot.
    Before this was parsed defensively, `int("")` raised inside `main()` and a
    stray keystroke in a config file would have stopped the server coming up
    at all — a worse outage than the one being fixed.
    """
    proc = _start_harness(presses="three")
    try:
        assert proc.poll() is None, "a bad press count stopped the server starting"
        proc.send_signal(signal.SIGINT)
        assert _still_running(proc), "fell back to a value that does not protect"
    finally:
        proc.kill()
        proc.wait(timeout=10)
