"""The log has to outlive the window that started the server.

Written against the 3 September outage (issue #57). The server ran for
98 minutes doing nothing and left no log at all, because every line went
to a console window's stderr and died with it — the cause had to be
reconstructed afterwards from `explorer.exe` start times and Task
Scheduler. Two outages later the same night were diagnosed in seconds,
but only because someone had redirected stdout by hand that once.

So the properties under test are not "logging works". They are the ones
that failed in the hall:

  * a line is ON DISK the instant it is logged, not when the process
    exits cleanly — nothing about that night exited cleanly;
  * a second call does not double every line, because `main()` and a
    test and a reload can all reach the configuration;
  * a directory we cannot write to costs us the file, never the server.
    A katha with captions and no log beats no katha.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

import pytest

import live_captions


@pytest.fixture(autouse=True)
def _restore_root_handlers():
    """Put the root logger back exactly as it was.

    `configure_logging` mutates global logging state, and a test that
    leaves a file handler attached writes every later test's output into
    a temp directory that has already been deleted.
    """
    root = logging.getLogger()
    before = list(root.handlers)
    yield
    for h in root.handlers:
        if h not in before:
            h.close()
    root.handlers[:] = before


def _configure(tmp_path: Path, **kw) -> Path | None:
    return live_captions.configure_logging(log_dir=str(tmp_path), **kw)


class TestTheLineReachesTheDisk:
    def test_a_logged_line_is_readable_before_the_process_ends(self, tmp_path):
        path = _configure(tmp_path)

        logging.getLogger("captions").info("katha started")

        # No flush, no close, no shutdown — this is the read someone does
        # from another window while the server is still up, and the read
        # they get after the console is closed without warning.
        assert path is not None
        assert "katha started" in path.read_text(encoding="utf-8")

    def test_the_file_lands_where_the_caller_was_told_it_would(self, tmp_path):
        path = _configure(tmp_path)

        assert path == tmp_path / live_captions.LOG_FILENAME
        assert path.exists()

    def test_the_level_and_time_are_in_the_line(self, tmp_path):
        # The reconstruction after outage 1 turned on *when* things
        # happened. A line without a timestamp would not have helped.
        path = _configure(tmp_path)

        logging.getLogger("captions").warning("port already held")

        line = path.read_text(encoding="utf-8").strip().splitlines()[-1]
        assert "WARNING" in line
        assert "port already held" in line
        assert line[:2].isdigit()          # starts with a year, not the message


class TestCallingItTwice:
    def test_a_second_call_does_not_double_the_lines(self, tmp_path):
        _configure(tmp_path)
        path = _configure(tmp_path)

        logging.getLogger("captions").info("only once please")

        assert path is not None
        assert path.read_text(encoding="utf-8").count("only once please") == 1

    def test_a_second_call_to_a_new_directory_moves_the_log(self, tmp_path):
        # The operator restarts on a different port / directory. The old
        # handler must go, or the first file keeps growing invisibly.
        first  = _configure(tmp_path / "one")
        second = _configure(tmp_path / "two")

        logging.getLogger("captions").info("after the move")

        assert first is not None and second is not None
        assert "after the move" in second.read_text(encoding="utf-8")
        assert "after the move" not in first.read_text(encoding="utf-8")


class TestWhenTheDiskSaysNo:
    def test_an_unwritable_directory_costs_the_file_not_the_server(self, tmp_path, monkeypatch):
        def explode(*a, **kw):
            raise PermissionError("Access is denied")

        monkeypatch.setattr(live_captions.Path, "mkdir", explode)

        # No exception, and the caller can tell there is no file to read.
        assert live_captions.configure_logging(log_dir=str(tmp_path / "nope")) is None

    def test_the_server_still_logs_to_the_console_with_no_file(self, tmp_path, monkeypatch, caplog):
        monkeypatch.setattr(live_captions.Path, "mkdir",
                            lambda *a, **kw: (_ for _ in ()).throw(OSError("read-only")))
        live_captions.configure_logging(log_dir=str(tmp_path / "nope"))

        with caplog.at_level(logging.INFO):
            logging.getLogger("captions").info("still talking")

        assert "still talking" in caplog.text


class TestRotation:
    def test_the_log_rotates_daily_and_keeps_a_fortnight(self, tmp_path):
        # A katha PC is never tidied up by hand. Unbounded is a disk-full
        # outage a year from now; one day per file is what makes "what
        # happened on the 3rd" a question you can answer.
        _configure(tmp_path)

        handler = next(h for h in logging.getLogger().handlers
                       if isinstance(h, logging.handlers.TimedRotatingFileHandler))
        assert handler.when == "MIDNIGHT"
        assert handler.backupCount == live_captions.LOG_RETAIN_DAYS
        assert live_captions.LOG_RETAIN_DAYS >= 14


class TestWhereTheLogGoes:
    def test_the_environment_can_move_it(self, tmp_path, monkeypatch):
        # The mandir PC's install directory may be read-only under a
        # future lockdown; the operator needs one lever that does not
        # require editing a script.
        monkeypatch.setenv(live_captions.LOG_DIR_ENV, str(tmp_path / "fromenv"))

        path = live_captions.configure_logging()

        assert path == tmp_path / "fromenv" / live_captions.LOG_FILENAME

    def test_an_explicit_directory_beats_the_environment(self, tmp_path, monkeypatch):
        monkeypatch.setenv(live_captions.LOG_DIR_ENV, str(tmp_path / "fromenv"))

        path = live_captions.configure_logging(log_dir=str(tmp_path / "explicit"))

        assert path == tmp_path / "explicit" / live_captions.LOG_FILENAME

    def test_the_default_sits_next_to_the_code_that_is_running(self, monkeypatch):
        # Whoever is diagnosing has the checkout in front of them. Making
        # them hunt for %LOCALAPPDATA% is how the log stays unread.
        #
        # Asserts the CANDIDATE rather than calling configure_logging(),
        # which would open a real handler and leave logs/live-captions.log
        # in the checkout — gitignored, so invisible rather than harmless,
        # and it fails outright on a read-only checkout.
        monkeypatch.delenv(live_captions.LOG_DIR_ENV, raising=False)

        first = live_captions._log_dir_candidates(None)[0]

        assert first == Path(live_captions.__file__).resolve().parent / "logs"

    def test_it_falls_back_before_giving_up(self, monkeypatch):
        # The order matters to the troubleshooting table in docs/how-it-works.md: a
        # missing logs/ does NOT mean the server never ran.
        monkeypatch.delenv(live_captions.LOG_DIR_ENV, raising=False)
        monkeypatch.setenv("LOCALAPPDATA", str(Path("/nonexistent-localappdata")))

        candidates = live_captions._log_dir_candidates(None)

        assert len(candidates) == 3
        assert candidates[1] == Path("/nonexistent-localappdata") / "live-captions" / "logs"


class TestTheExitIsRecorded:
    """Reconstructing outage 1 meant reading Task Scheduler for the times
    these lines would have printed for free — and, worse, guessing whether
    the server had crashed or been shut. The log now answers both."""

    def _run_entry(self, tmp_path, monkeypatch, run):
        monkeypatch.setattr(
            "sys.argv",
            ["live_captions.py", "--port", "8799", "--log-dir", str(tmp_path)],
        )
        monkeypatch.setattr(live_captions.asyncio, "run", run)
        # A crash exits non-zero on purpose, so Task Scheduler's
        # LastTaskResult agrees with the log. Swallow it here; the exit
        # code itself is asserted separately.
        try:
            live_captions.entry()
        except SystemExit as e:
            self.exit_code = e.code
        else:
            self.exit_code = 0
        return (tmp_path / live_captions.LOG_FILENAME).read_text(encoding="utf-8")

    def test_the_start_banner_names_the_pid_and_port(self, tmp_path, monkeypatch):
        text = self._run_entry(tmp_path, monkeypatch, lambda coro: coro.close())

        assert f"pid {os.getpid()}" in text
        assert "port 8799" in text
        assert str(tmp_path / live_captions.LOG_FILENAME) in text

    def test_a_ctrl_c_or_a_closed_console_says_so(self, tmp_path, monkeypatch):
        def interrupted(coro):
            coro.close()
            raise KeyboardInterrupt

        text = self._run_entry(tmp_path, monkeypatch, interrupted)

        assert "KeyboardInterrupt" in text
        assert "Ctrl+C or a closed console" in text
        assert "Live captions exiting" in text

    def test_a_loop_that_returns_reads_differently_from_a_ctrl_c(self, tmp_path, monkeypatch):
        text = self._run_entry(tmp_path, monkeypatch, lambda coro: coro.close())

        assert "the server loop returned" in text
        assert "KeyboardInterrupt" not in text

    def test_a_crash_leaves_its_traceback_in_the_log(self, tmp_path, monkeypatch):
        """The failure the file handler was meant to end, and nearly did not.

        `entry()` used to catch only KeyboardInterrupt, so an uncaught
        exception's traceback went to stderr — which the new handler is
        not attached to, and which under the logon task's hidden window
        goes nowhere at all. The `finally` line then printed on the way
        out, so a crash log ended looking exactly like a clean shutdown.
        The hand-typed `2> ...err.log` redirect this replaces did catch
        tracebacks; replacing it with something worse for the crash case
        is not a fix.
        """
        def crashed(coro):
            coro.close()
            raise RuntimeError("sarvam socket exploded")

        text = self._run_entry(tmp_path, monkeypatch, crashed)

        assert "sarvam socket exploded" in text
        assert "RuntimeError" in text
        assert "Traceback" in text
        assert "CRASHED" in text

    def test_a_crash_does_not_sign_off_as_a_clean_stop(self, tmp_path, monkeypatch):
        def crashed(coro):
            coro.close()
            raise RuntimeError("boom")

        text = self._run_entry(tmp_path, monkeypatch, crashed)

        assert "the server loop returned" not in text
        assert "Ctrl+C" not in text

    def test_a_crash_exits_non_zero_so_task_scheduler_agrees(self, tmp_path, monkeypatch):
        def crashed(coro):
            coro.close()
            raise RuntimeError("boom")

        self._run_entry(tmp_path, monkeypatch, crashed)
        assert self.exit_code == 1

    def test_a_clean_stop_exits_zero(self, tmp_path, monkeypatch):
        self._run_entry(tmp_path, monkeypatch, lambda coro: coro.close())
        assert self.exit_code == 0
