from pathlib import Path

import pytest

from p9qemu.drawterm_postinstall import load_drawterm_postinstall_profile
from p9qemu.errors import P9QemuError
import p9qemu.pexpect_drawterm_validation as adapter
from p9qemu.pexpect_drawterm_validation import (
    _wait_for_drawterm_ports_released,
    _wait_for_shutdown,
)


PROFILE_PATH = (
    Path(__file__).parents[1]
    / "images"
    / "p9qemu-9front-11554-amd64-hjfs-gmt-drawterm-001"
    / "postinstall.json"
)


class FakeChild:
    def __init__(self, indexes: list[int]):
        self.indexes = indexes

    def expect(self, _patterns, timeout: int) -> int:
        assert timeout == 60
        return self.indexes.pop(0)


def write_log(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_shutdown_accepts_done_halting_then_qemu_eof(tmp_path: Path) -> None:
    log = tmp_path / "boot.raw.log"
    write_log(log, "done halting\n")
    assert _wait_for_shutdown(FakeChild([0, 0]), log) == (
        "guest reported done halting before QEMU exited"
    )


def test_cpu_server_shutdown_accepts_hjfs_ending_at_qemu_eof(tmp_path: Path) -> None:
    log = tmp_path / "boot.raw.log"
    write_log(log, "cirno# hjfs: ending\n")
    assert _wait_for_shutdown(FakeChild([1]), log) == (
        "HJFS reported ending before QEMU exited"
    )


def test_qemu_eof_without_filesystem_shutdown_evidence_is_rejected(
    tmp_path: Path,
) -> None:
    log = tmp_path / "boot.raw.log"
    write_log(log, "init: starting /bin/rc\n")
    with pytest.raises(P9QemuError, match="without done halting or HJFS"):
        _wait_for_shutdown(FakeChild([1]), log)


def test_post_shutdown_ports_need_only_stop_accepting_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = load_drawterm_postinstall_profile(PROFILE_PATH)
    monkeypatch.setattr(adapter, "_connectable", lambda _address, _port: False)
    messages: list[str] = []
    _wait_for_drawterm_ports_released(profile, progress=messages.append)
    assert messages == [
        "Confirmed CPU and auth host ports stopped accepting connections."
    ]
