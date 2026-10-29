"""The vMix setup in the install docs says what the code expects.

A volunteer sets vMix up from WINDOWS.md alone, so if the page and the gate
disagree about the input's title, the overlay number or the URL, the desk's
button starts nothing and nobody can tell why. These tests read the docs as
text and hold them to the gate's own defaults and settings file.
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

from tools.vmix import overlay_gate

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = (ROOT / "WINDOWS.md").read_text(encoding="utf-8")
DESK = (ROOT / "DESK-CHECKLIST.md").read_text(encoding="utf-8")
SETUP = (ROOT / "SETUP.md").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
CHANGELOG = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
HOW_IT_WORKS = (ROOT / "docs" / "how-it-works.md").read_text(encoding="utf-8")
EXAMPLE = json.loads((ROOT / "overlay-gate.example.json").read_text(encoding="utf-8"))
DEFAULTS = overlay_gate.DEFAULTS


def _section(doc: str, heading: str) -> str:
    """The text under `## heading` up to the next `## `."""
    start = doc.index(f"\n## {heading}")
    end = doc.find("\n## ", start + 1)
    return doc[start:end if end != -1 else len(doc)]


def test_example_settings_file_is_the_gates_defaults():
    # The docs say "copy the example; a key you leave out takes the default".
    for field in dataclasses.fields(overlay_gate.Settings):
        assert EXAMPLE[field.name] == getattr(DEFAULTS, field.name), field.name


VMIX_SETUP = _section(WINDOWS, "Setting up vMix")


def test_vmix_setup_names_the_input_the_gate_looks_for():
    assert DEFAULTS.input_title == "Captions Overlay"
    assert f"`{DEFAULTS.input_title}`" in VMIX_SETUP


def test_vmix_setup_gives_the_url_the_gate_navigates_to():
    assert DEFAULTS.overlay_url in VMIX_SETUP
    assert "," not in DEFAULTS.overlay_url   # vMix refuses a comma in a browser URL


def test_vmix_setup_names_the_overlay_and_the_web_controller_port():
    assert f"overlay {DEFAULTS.overlay}" in VMIX_SETUP
    assert DEFAULTS.vmix_api in VMIX_SETUP   # the address the volunteer is told to check


def test_every_gate_setting_is_explained_in_the_windows_doc():
    gate = _section(WINDOWS, "The overlay gate and its settings")
    for field in dataclasses.fields(overlay_gate.Settings):
        assert f"`{field.name}`" in gate, field.name


BOLTON_INPUT = "Line In (Realtek(R) Audio)"


def test_the_gate_names_no_hall_s_device_by_default():
    # docs/how-it-works.md, Conventions: no org's values in code. The Bolton
    # input is an example in the docs, never a default.
    assert DEFAULTS.device_name == ""
    assert DEFAULTS.device_host_api == ""
    assert BOLTON_INPUT not in (ROOT / "overlay-gate.example.json").read_text(encoding="utf-8")


def test_the_install_docs_tell_a_hall_to_name_its_input():
    gate = _section(WINDOWS, "The overlay gate and its settings")
    assert "server's default input" in gate
    assert f"`{BOLTON_INPUT}`" in gate         # as an example
    assert "device_name" in _section(WINDOWS, "One-time setup")
    assert "device_name" in VMIX_SETUP


def test_the_update_notes_tell_the_bolton_pc_to_set_its_input():
    # The Bolton PC ran on the old default. Without its own file it would start
    # on the server's default input after the update.
    updating = CHANGELOG[CHANGELOG.index("### Updating"):CHANGELOG.index("### Still not fixed")]
    assert "overlay-gate.json" in updating
    assert f'"device_name": "{BOLTON_INPUT}"' in updating


def test_desk_checklist_uses_the_names_the_desk_sees():
    assert DEFAULTS.input_title in DESK
    assert f"overlay {DEFAULTS.overlay}" in DESK
    assert f"{DEFAULTS.stop_grace_sec:g} seconds" in DESK
    # One page: about sixty lines of 100 characters at the most.
    assert len(DESK.splitlines()) <= 60
    assert max(len(line) for line in DESK.splitlines()) <= 100


def test_the_dry_run_covers_every_recovery_the_spec_names():
    dry = _section(WINDOWS, "Dry run")
    for needle in (
        "pythonw.exe",                        # the gate task needs it in the venv
        "RestartCount",                       # restart on failure is what was asked for
        "LiveCaptionsAdhoc",                  # the old scaffolding is gone
        "stop-captions.bat",                  # a deliberate stop is not restarted
        "Stop-ScheduledTask",                 # the whole process tree ends
        "Microsoft Sound Mapper",             # the default input is Line In under MME
        "BrowserNavigate",                    # vMix 26 takes Input=<key> and %2C
        "%2C",
        "unplug",                             # a start with the device missing is refused
        "midnight",                           # the logs roll over
        "rules.json",                         # the update on a PC with a local edit
        "vMix opened before the server",
    ):
        assert needle in dry, needle


def test_the_state_files_list_names_the_gate_settings_and_the_rules():
    state = re.search(r"\*\*State files are gitignored\.\*\*.*?(?=\n- \*\*)", HOW_IT_WORKS, re.S).group(0)
    assert "overlay-gate.json" in state
    assert "rules.json" in state


def test_gate_settings_and_live_rules_are_ignored_by_git():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "overlay-gate.json" in ignored
    assert "rules.json" in ignored


def test_the_gitignore_does_not_call_the_public_repo_private():
    note = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "both repos went private" not in note


def test_docs_point_at_each_other():
    assert "DESK-CHECKLIST.md" in README
    assert "DESK-CHECKLIST.md" in WINDOWS
    assert "DESK-CHECKLIST.md" in CHANGELOG
    assert "WINDOWS.md" in DESK
    # The old 'never run in anger' line about the batch file is gone, not duplicated.
    assert "start-captions.bat` and `register-startup-task.ps1` have never been run" not in README


# ── SETUP.md: the runbook for a new hall PC ──────────────────────────────────

def _setup_steps() -> list[str]:
    """Each `## Step N: ...` section of SETUP.md, heading included."""
    parts = re.split(r"(?m)^(?=## Step \d+:)", SETUP)
    return [p for p in parts if p.startswith("## Step ")]


def test_setup_runbook_has_steps_and_each_ends_on_a_done_when_line():
    steps = _setup_steps()
    assert len(steps) >= 9
    for step in steps:
        assert "**Done when:**" in step, step.splitlines()[0]


def test_setup_runbook_names_what_the_gate_expects():
    assert DEFAULTS.input_title in SETUP
    assert f"overlay {DEFAULTS.overlay}" in SETUP
    assert "OverlayInput2In" in SETUP and "OverlayInput2Out" in SETUP   # the overlay number above
    # The URL it adds to vMix is read from the gate's own example file, not typed
    # twice; the file it reads must be the one that holds the default.
    assert "overlay-gate.example.json" in SETUP
    assert EXAMPLE["overlay_url"] == DEFAULTS.overlay_url
    assert DEFAULTS.vmix_api in SETUP


def test_setup_runbook_covers_the_pieces_a_hall_pc_needs():
    # In the steps' own words, not just somewhere: each piece is in the step that needs it.
    def step(n: int) -> str:
        return next(s for s in _setup_steps() if s.startswith(f"## Step {n}:"))
    assert "gh auth login" in step(0) and "gh issue create" in step(0) + step(1)
    assert "git clone https://github.com/bhujmandir/live-captions" in step(1)
    assert "SARVAM_API_KEY" in step(2) and "GEMINI_API_KEY" in step(2)
    assert "deploy.ps1" in step(3)
    assert "/api/shutdown" in step(4)
    assert "register-startup-task.ps1" in step(5)
    assert "/api/devices" in step(6) and "deviceName" in step(6) and "hostApi" in step(6)
    assert "Translator:" in step(6)                       # the keys are proved at the first start
    assert "overlay-gate.json" in step(7) and "device_name" in step(7) and "device_host_api" in step(7)
    assert "overlay gate armed" in step(7)
    for fn in ("AddInput", "SetInputName", "BrowserNavigate", "OverlayInput2In",
               "OverlayInput2Out", "SavePreset"):
        assert fn in step(8), fn
    assert "Dry run" in step(9)
    assert "dry-run results" in step(10)
    assert "Report what you did not prove" in SETUP


def test_setup_runbook_files_problems_as_issues_on_the_bhuj_repo():
    assert "gh issue create -R bhujmandir/live-captions" in SETUP
    assert "git remote get-url" not in SETUP               # the repository is named, not derived
    assert "logs\\issue-" in SETUP                          # nothing lost when gh is missing
    assert "never** contain an API key" in SETUP           # the repository is public


def test_setup_runbook_walks_a_person_through_both_keys():
    assert "https://dashboard.sarvam.ai" in SETUP
    assert "https://aistudio.google.com/apikey" in SETUP
    for name in ("SARVAM_API_KEY", "GEMINI_API_KEY"):
        assert name in SETUP
    assert "Set up billing" in SETUP
    assert ".env.template" in SETUP


def test_setup_runbook_carries_no_tracker_references_or_names():
    assert not re.search(r"(?<![\w&])#\d+\b", SETUP)
    names = "|".join(["man" + "ish", "the " + "owner"])      # assembled: this file names no one either
    assert not re.search(rf"(?i)\b({names})\b", SETUP)


def test_setup_runbook_marks_what_is_unverified_and_gives_the_by_hand_fallback():
    # AddInput with a browser URL is not in vMix's function table: it must say so
    # in the step that uses it, and give the by-hand way.
    add = SETUP[SETUP.index("**8.2 Add the browser input."):SETUP.index("**8.3")]
    assert "[UNVERIFIED]" in add
    assert 'WINDOWS.md`](WINDOWS.md), "Setting up vMix"' in add


def test_setup_runbook_never_puts_a_key_value_in_a_snippet():
    # The only place a key name is followed by `=` is where the file says what to fill in,
    # and nothing follows the `=`.
    for line in SETUP.splitlines():
        for m in re.finditer(r"(SARVAM|GEMINI)_API_KEY=(\S*)", line):
            assert m.group(2) in ("", "`", "`,"), line


def test_setup_runbook_is_pointed_at_from_the_readme_and_agents():
    assert "[`SETUP.md`](SETUP.md)" in README
    assert "[`SETUP.md`](SETUP.md)" in (ROOT / "AGENTS.md").read_text(encoding="utf-8")
