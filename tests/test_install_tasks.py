"""The install registers the two background jobs a katha runs on.

In katha week the server ran from a logon task whose console window, once
closed, ended the captions for good (23 Oct), and the overlay-button gate
ran from a task registered by hand, from a folder outside the checkout. A
new mandir had neither.

PowerShell and Task Scheduler cannot run here, so these tests read the two
scripts as text and check the properties the hall depended on: which tasks
exist, what they run, that they restart, that nothing redirects output to a
fixed file, and that the autostart check names both. Whether Windows accepts
the registration is the dry run's job (WINDOWS.md), not this file's.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REGISTER = (ROOT / "register-startup-task.ps1").read_text(encoding="utf-8")
DEPLOY = (ROOT / "deploy.ps1").read_text(encoding="utf-8")
WINDOWS_DOC = (ROOT / "WINDOWS.md").read_text(encoding="utf-8")

SERVER_TASK = "LiveCaptions"
GATE_TASK = "LiveCaptionsOverlayGate"


def _code(ps1: str) -> str:
    """The script without its comments, so a mention is not a use."""
    ps1 = re.sub(r"<#.*?#>", "", ps1, flags=re.S)
    return "\n".join(ln for ln in ps1.splitlines() if not ln.lstrip().startswith("#"))


REGISTER_CODE = _code(REGISTER)
DEPLOY_CODE = _code(DEPLOY)


def test_register_script_names_both_tasks():
    assert f'"{SERVER_TASK}"' in REGISTER_CODE
    assert f'"{GATE_TASK}"' in REGISTER_CODE


def test_the_gate_runs_from_inside_the_checkout_without_a_window():
    # pythonw has no console to close; the gate is the repo's own module.
    assert r".venv\Scripts\pythonw.exe" in REGISTER_CODE
    assert r"tools\vmix\overlay_gate.py" in REGISTER_CODE
    assert (ROOT / "tools" / "vmix" / "overlay_gate.py").is_file()
    assert "captions-tools" not in REGISTER


def test_the_server_runs_hidden_not_through_the_console_bat():
    assert "-WindowStyle Hidden" in REGISTER_CODE
    assert "deploy.ps1" in REGISTER_CODE and "-StartOnly" in REGISTER_CODE
    assert not re.search(r"(Execute|Argument)\s*=[^\n]*start-captions", REGISTER_CODE)


def test_both_tasks_restart_on_failure_without_a_time_limit():
    # Task Scheduler's ceiling for the count is 999: effectively unlimited.
    assert "$RestartCount   = 999" in REGISTER_CODE
    assert "-RestartCount $RestartCount " in REGISTER_CODE
    assert re.search(r"-RestartInterval\s+\(New-TimeSpan -Minutes 1\)", REGISTER_CODE)
    assert "-ExecutionTimeLimit ([TimeSpan]::Zero)" in REGISTER_CODE
    # One settings object serves both tasks: they cannot drift apart.
    assert REGISTER_CODE.count("New-ScheduledTaskSettingsSet") == 1


def test_the_server_task_never_opens_a_browser():
    # A restart opens nothing over vMix mid-katha (the desk opens the page).
    assert re.search(r'Argument\s*=\s*"[^\n]*-StartOnly -NoBrowser', REGISTER_CODE)


def _guarded_by_no_browser(code: str, position: int) -> bool:
    """Whether `position` lies inside an `if (-not $NoBrowser) { ... }` block."""
    for m in re.finditer(r"if \(-not \$NoBrowser\)\s*\{", code):
        depth, i = 1, m.end()
        while depth and i < len(code):
            depth += {"{": 1, "}": -1}.get(code[i], 0)
            i += 1
        if m.end() <= position < i:
            return True
    return False


def test_deploy_opens_the_browser_only_when_not_told_not_to():
    assert re.search(r"\[switch\]\$NoBrowser", DEPLOY_CODE)
    opens = [m.start() for m in re.finditer(r"Start-Process", DEPLOY_CODE)]
    assert len(opens) == 2  # the already-running path and the poll job
    for at in opens:
        assert _guarded_by_no_browser(DEPLOY_CODE, at)


def test_the_read_back_compares_the_whole_action_argument():
    assert "Actions[0].Arguments" in REGISTER_CODE


def test_both_tasks_start_at_logon_for_the_current_user():
    assert "New-ScheduledTaskTrigger -AtLogOn -User" in REGISTER_CODE


def test_no_task_redirects_output_to_a_fixed_name_file():
    # Each process writes its own rolling log, so no start overwrites the last one's.
    for pattern in (r"\s>\s", r"\s>>\s", r"\*>", r"Out-File", r"RedirectStandard", r"cmd(\.exe)?\s+/c"):
        assert not re.search(pattern, REGISTER_CODE), pattern


def test_nothing_in_the_scripts_is_a_personal_path():
    for text in (REGISTER, DEPLOY):
        assert not re.search(r"C:\\Users\\|sksst", text)


def test_registering_twice_is_safe_because_an_existing_task_is_replaced():
    # An old instance (the hand-registered gate) is stopped, not left running
    # beside the new one: two gates would both start captures.
    assert "Stop-ScheduledTask" in REGISTER_CODE
    assert "Unregister-ScheduledTask" in REGISTER_CODE
    assert REGISTER_CODE.index("Stop-ScheduledTask") < re.search(r"(?<!Un)Register-ScheduledTask\s", REGISTER_CODE).start()


def test_both_tasks_are_read_back_and_a_missing_one_fails_loudly():
    assert REGISTER_CODE.count("Get-ScheduledTask") >= 2
    assert "Settings.RestartCount" in REGISTER_CODE
    assert "Actions[0].Execute" in REGISTER_CODE
    assert "exit 1" in REGISTER_CODE
    assert "will NOT start" in REGISTER


def test_the_gate_task_is_not_registered_without_its_interpreter():
    # uv sync creates .venv; registering a task that cannot start is silent.
    assert "Test-Path" in REGISTER_CODE and "pythonw.exe" in REGISTER_CODE


def test_one_command_removes_both_tasks():
    assert re.search(r"\[switch\]\$Remove", REGISTER_CODE)
    assert "-Remove" in REGISTER  # documented in the help block too
    assert "Unregister-ScheduledTask" in REGISTER_CODE


def test_the_script_says_it_is_unverified_on_a_real_pc():
    assert "UNVERIFIED" in REGISTER


def test_powershell_delimiters_balance():
    for name, code in (("register", REGISTER_CODE), ("deploy", DEPLOY_CODE)):
        stripped = re.sub(r'"(?:`.|[^"`])*"|\'[^\']*\'', '""', code)
        for a, b in ("{}", "()"):
            assert stripped.count(a) == stripped.count(b), (name, a)


# ── the autostart check in deploy.ps1 ───────────────────────────────────────

def test_autostart_check_names_both_tasks():
    assert f'"{SERVER_TASK}"' in DEPLOY_CODE
    assert f'"{GATE_TASK}"' in DEPLOY_CODE


def test_autostart_check_says_which_is_missing_disabled_or_not_restarting():
    assert "is NOT registered" in DEPLOY
    assert "DISABLED" in DEPLOY
    assert "RestartCount" in DEPLOY_CODE


def test_autostart_check_still_flags_the_adhoc_task():
    assert "LiveCaptionsAdhoc" in DEPLOY_CODE


def test_server_exit_code_reaches_task_scheduler():
    # Without it a crashed server ends the wrapper with 0 and is not restarted.
    assert re.search(r"uv run python live_captions\.py[^\n]*\n\s*exit \$LASTEXITCODE", DEPLOY_CODE)


# ── the server restarts itself ──────────────────────────────────────────────
# Task Scheduler's restart on failure is widely reported to fire only when a
# task fails to LAUNCH, not when the program exits non-zero. It stays as a
# backup, but a crashed server must not wait on it: deploy.ps1 supervises.

def _supervise_block() -> str:
    """The body of deploy.ps1's `if ($Supervise) { ... }` block."""
    m = re.search(r"if \(\$Supervise\)\s*\{", DEPLOY_CODE)
    assert m, "deploy.ps1 has no supervise branch"
    depth, i = 1, m.end()
    while depth and i < len(DEPLOY_CODE):
        depth += {"{": 1, "}": -1}.get(DEPLOY_CODE[i], 0)
        i += 1
    return DEPLOY_CODE[m.end():i]


def test_the_server_task_asks_deploy_to_supervise_the_server():
    assert re.search(r"\[switch\]\$Supervise", DEPLOY_CODE)
    assert re.search(r'Argument\s*=\s*"[^\n]*-StartOnly -NoBrowser -Supervise"', REGISTER_CODE)


def test_supervision_relaunches_a_server_that_exits_non_zero_and_never_gives_up():
    block = _supervise_block()
    assert "while ($true)" in block
    assert "uv run python live_captions.py" in block
    assert re.search(r"Start-Sleep -Seconds \$RestartDelaySec", block)
    assert re.search(r"\$RestartDelaySec\s*=\s*10\b", DEPLOY_CODE)
    assert "restarting in" in block and "server exited" in block
    # No counter: a katha is not over because the server crashed N times.
    assert not re.search(r"\$(attempts?|tries|crashes|count)\b", block, re.I)


def test_a_deliberate_stop_ends_supervision():
    block = _supervise_block()
    assert re.search(r"-eq 0\)\s*\{[^}]*exit 0", block, re.S)


def test_the_port_is_checked_before_every_launch_so_a_live_server_is_not_doubled():
    block = _supervise_block()
    loop = block[block.index("while ($true)"):]
    assert loop.index("Get-PortState") < loop.index("uv run python live_captions.py")
    # A live server is watched, not restarted and not a reason to stop.
    assert '"ours"' in loop and '"free"' in loop
    assert "not the captions server: waiting" in loop


def test_supervision_never_opens_a_browser():
    assert "Start-Process" not in _supervise_block()


def test_supervisor_writes_its_own_dated_log_with_a_fortnight_kept():
    assert re.search(r"supervisor-\$\(Get-Date -Format 'yyyy-MM-dd'\)\.log", DEPLOY_CODE)
    assert "AddDays(-$SupervisorLogDays)" in DEPLOY_CODE
    assert re.search(r"\$SupervisorLogDays\s*=\s*14\b", DEPLOY_CODE)


def test_a_one_off_start_still_works_without_supervise():
    # start-captions.bat and a hand run keep their one-shot behaviour.
    assert "-Supervise" not in (ROOT / "start-captions.bat").read_text(encoding="utf-8")


# ── nothing published names a coding assistant ────────────────────────────────────

def test_published_files_name_no_coding_assistant():
    # The repo is public. AGENTS.md (a file name) is the one allowed mention.
    # The words are assembled so this file does not itself contain them.
    banned = re.compile("(?i)" + "|".join(["cl" + "aude", "anti" + "gravity", r"\b" + "ag" + "ents?" + r"\b"]))
    files = [ROOT / n for n in ("WINDOWS.md", "CHANGELOG.md", "README.md", "DESK-CHECKLIST.md", "SETUP.md",
                                "deploy.ps1", "register-startup-task.ps1", "start-captions.bat")]
    files += sorted((ROOT / "docs").rglob("*.md"))
    for f in files:
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if "AGENTS.md" in line:
                continue
            assert not banned.search(line), f"{f.name}:{n}: {line}"


def _tracked_text_files():
    """Every tracked file that is text, as (name, text). Lockfiles and binaries are skipped."""
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True,
                             check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout, so the tracked files are unknown")
    for name in out.decode("utf-8").split("\0"):
        if not name or Path(name).name in ("uv.lock", "pnpm-lock.yaml"):
            continue
        data = (ROOT / name).read_bytes()
        if b"\0" in data:
            continue
        try:
            yield name, data.decode("utf-8")
        except UnicodeDecodeError:
            continue


def test_published_files_carry_no_development_trail():
    # The repo is public. A reason stays in the file it explains; the trail of how it was
    # reached does not: tracker numbers resolve nowhere a reader can follow, and a name or a
    # note of who said what is working-out, not design. A CSS colour (`color:#888`,
    # `color: #888`, `solid #333`) is not a tracker number. Assembled from fragments, as above.
    tracker = r"(?<![\w&/:])(?<!: )(?<!solid )#\d{1,4}\b"
    words = [r"\b" + "man" + "ish" + r"\b", r"\bthe " + "own" + r"er\b",
             "vajra" + "tech", "runs-" + "on:", "mandir" + "-pc"]
    banned = re.compile("(?i)" + "|".join([tracker] + words))
    hits = [f"{name}:{n}: {line.strip()}"
            for name, text in _tracked_text_files()
            for n, line in enumerate(text.splitlines(), 1) if banned.search(line)]
    assert not hits, "\n".join(hits)


# ── the docs ────────────────────────────────────────────────────────────────

def test_windows_doc_says_who_restarts_what():
    assert "-Supervise" in WINDOWS_DOC
    assert "Task Scheduler" in WINDOWS_DOC and "backup" in WINDOWS_DOC


def test_windows_doc_gives_the_register_and_remove_commands():
    assert "register-startup-task.ps1" in WINDOWS_DOC
    assert "register-startup-task.ps1 -Remove" in WINDOWS_DOC
    assert GATE_TASK in WINDOWS_DOC
