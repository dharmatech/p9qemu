from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path

import pytest

from p9qemu.answers import load_answers
from p9qemu.errors import P9QemuError
from p9qemu.installer import (
    INSTALLER_PROFILE_REVISION_11554,
    INSTALLER_SOURCE_REVISION_11554,
    InstallerStateMachine,
    build_11554_hjfs_profile,
    normalize_console,
    replay_transcript,
)


ROOT = Path(__file__).parents[1]
REFERENCE = ROOT / "images" / "9front-11554-amd64-hjfs-manual-001"
REFERENCE_ANSWERS = REFERENCE / "answers.toml"
REFERENCE_TRANSCRIPT = REFERENCE / "transcripts" / "install.raw.log"
GMT_REFERENCE_ANSWERS = (
    ROOT / "images" / "9front-11554-amd64-hjfs-gmt-reference-001" / "answers.toml"
)


def profile():
    return build_11554_hjfs_profile(load_answers(REFERENCE_ANSWERS))


def test_console_normalization_removes_ansi_backspace_and_cr() -> None:
    text = "\x1b[2Jbootx\b\r\nTask\x00 to do\rnext\x7f"
    assert normalize_console(text) == "boot\nTask to do\nnext"


def test_profile_replays_the_manual_reference_transcript() -> None:
    transcript = REFERENCE_TRANSCRIPT.read_text(encoding="utf-8")
    result = replay_transcript(transcript, profile())
    assert result.states[0] == "boot.interrupt"
    assert result.states[-1] == "finish.rebooting"
    assert len(result.states) == len(profile().steps)

    actions = {action.state: action.response for action in result.actions}
    assert actions["boot.interrupt"] == " "
    assert actions["boot.console"] == "console=0"
    assert actions["shell.start_installer"] == "inst/start"
    assert actions["tzsetup.timezone"] == "US_Pacific"
    assert "finish.completed" not in actions
    assert "finish.rebooting" not in actions


def test_profile_records_its_revision_and_inspected_installer_source() -> None:
    installer_profile = profile()
    assert installer_profile.revision == INSTALLER_PROFILE_REVISION_11554 == 1
    assert installer_profile.source_revision == INSTALLER_SOURCE_REVISION_11554


def test_gmt_profile_reuses_the_certified_11554_state_machine() -> None:
    installer_profile = build_11554_hjfs_profile(load_answers(GMT_REFERENCE_ANSWERS))
    steps = {step.state: step for step in installer_profile.steps}
    assert installer_profile.profile_id == "9front-11554-amd64-hjfs-gmt-v1"
    assert steps["tzsetup.timezone"].response == "GMT"


def test_golden_transcript_has_the_recorded_digest() -> None:
    digest = hashlib.sha256(REFERENCE_TRANSCRIPT.read_bytes()).hexdigest()
    assert digest == "22411d01b01a3bef2c4af0dadee02ebfd0c55e0b8a5281746745dcb7154c8ba0"


def test_menu_tasks_are_selected_explicitly() -> None:
    expected_tasks = {
        "menu.configfs": "configfs",
        "menu.partdisk": "partdisk",
        "menu.prepdisk": "prepdisk",
        "menu.mountfs": "mountfs",
        "menu.confignet": "confignet",
        "menu.mountdist": "mountdist",
        "menu.copydist": "copydist",
        "menu.ndbsetup": "ndbsetup",
        "menu.tzsetup": "tzsetup",
        "menu.bootsetup": "bootsetup",
        "menu.finish": "finish",
    }
    actual = {
        step.state: step.response
        for step in profile().steps
        if step.state.startswith("menu.")
    }
    assert actual == expected_tasks


def test_boot_interrupt_and_answer_responses_use_distinct_send_modes() -> None:
    actions = {
        action.state: action
        for action in replay_transcript(
            REFERENCE_TRANSCRIPT.read_text(encoding="utf-8"), profile()
        ).actions
    }
    assert actions["boot.interrupt"].send_mode == "raw"
    assert actions["boot.console"].send_mode == "line"


def test_destructive_writes_follow_topology_and_layout_checkpoints() -> None:
    states = [step.state for step in profile().steps]
    assert states.index("partdisk.target_disk") < states.index("partdisk.target")
    assert states.index("partdisk.install_media") < states.index("partdisk.target")
    assert states.index("partdisk.layout") < states.index("fdisk.write")
    assert states.index("prepdisk.layout") < states.index("prep.write")


def test_copy_stage_has_a_longer_timeout() -> None:
    steps = {step.state: step for step in profile().steps}
    assert steps["menu.ndbsetup"].timeout_seconds == 1800
    assert steps["menu.configfs"].timeout_seconds == 60


def test_state_machine_rejects_out_of_order_observation() -> None:
    machine = InstallerStateMachine(profile())
    with pytest.raises(
        P9QemuError,
        match="unexpected installer state 'menu.configfs'.*expected 'boot.interrupt'",
    ):
        machine.observe("menu.configfs")
    assert machine.expected is not None
    assert machine.expected.state == "boot.interrupt"


def test_state_machine_rejects_observation_after_completion() -> None:
    machine = InstallerStateMachine(profile())
    for step in profile().steps:
        machine.observe(step.state)
    assert machine.complete
    with pytest.raises(P9QemuError, match="after profile completion"):
        machine.observe("finish.rebooting")


def test_replay_reports_the_first_missing_state() -> None:
    transcript = REFERENCE_TRANSCRIPT.read_text(encoding="utf-8")
    transcript = transcript.replace("Task to do [mountdist]:", "missing prompt")
    with pytest.raises(P9QemuError, match="'menu.mountdist'"):
        replay_transcript(transcript, profile())


def test_profile_builder_rejects_an_uncertified_iso_digest() -> None:
    answers = replace(load_answers(REFERENCE_ANSWERS), iso_sha256="0" * 64)
    with pytest.raises(P9QemuError, match="not certified"):
        build_11554_hjfs_profile(answers)


def test_profile_builder_rejects_an_unknown_profile_id() -> None:
    answers = replace(load_answers(REFERENCE_ANSWERS), installer_profile="latest")
    with pytest.raises(P9QemuError, match="unsupported installer profile 'latest'"):
        build_11554_hjfs_profile(answers)
