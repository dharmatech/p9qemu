from pathlib import Path
from types import SimpleNamespace

import pytest

from p9qemu.errors import P9QemuError
from p9qemu.instance import inspect_disk, prepare_disk, prepare_validation_overlay


def test_existing_disk_is_never_recreated(tmp_path: Path) -> None:
    disk = tmp_path / "9front.qcow2.img"
    disk.write_bytes(b"existing")

    def runner(*_args, **_kwargs):
        pytest.fail("qemu-img must not run for an existing disk")

    prepare_disk("qemu-img", disk, "30G", progress=lambda _message: None, runner=runner)
    assert disk.read_bytes() == b"existing"


def test_new_disk_is_created_via_temporary_file(tmp_path: Path) -> None:
    disk = tmp_path / "9front.qcow2.img"
    commands: list[list[str]] = []
    messages: list[str] = []

    def runner(command: list[str], **_kwargs):
        commands.append(command)
        Path(command[-2]).write_bytes(b"qcow2")
        return SimpleNamespace(returncode=0)

    prepare_disk("qemu-img", disk, "30G", progress=messages.append, runner=runner)
    assert disk.read_bytes() == b"qcow2"
    assert commands[0][:4] == ["qemu-img", "create", "-f", "qcow2"]
    assert commands[0][-1] == "30G"
    assert list(tmp_path.glob("*.part")) == []
    assert messages == [f"Creating 30G QCOW2 disk image: {disk}"]


def test_failed_disk_creation_leaves_no_partial(tmp_path: Path) -> None:
    disk = tmp_path / "9front.qcow2.img"

    def runner(command: list[str], **_kwargs):
        Path(command[-2]).write_bytes(b"partial")
        return SimpleNamespace(returncode=1)

    with pytest.raises(P9QemuError, match="status 1"):
        prepare_disk(
            "qemu-img", disk, "30G", progress=lambda _message: None, runner=runner
        )
    assert not disk.exists()
    assert list(tmp_path.glob("*.part")) == []


@pytest.mark.parametrize("size", ["", "0", "-1G", "thirty", "30 GB"])
def test_invalid_disk_size_is_rejected(tmp_path: Path, size: str) -> None:
    with pytest.raises(P9QemuError, match="invalid disk size"):
        inspect_disk(tmp_path / "disk.qcow2", size, progress=lambda _message: None)


def test_validation_overlay_is_created_with_explicit_qcow2_base(tmp_path: Path) -> None:
    base = tmp_path / "base.qcow2"
    overlay = tmp_path / "validation.qcow2"
    base.write_bytes(b"base")
    commands: list[list[str]] = []

    def runner(command: list[str], **_kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(b"overlay")
        return SimpleNamespace(returncode=0)

    prepare_validation_overlay(
        "qemu-img",
        base,
        overlay,
        progress=lambda _message: None,
        runner=runner,
    )
    assert overlay.read_bytes() == b"overlay"
    assert commands[0][:-1] == [
        "qemu-img",
        "create",
        "-f",
        "qcow2",
        "-F",
        "qcow2",
        "-b",
        str(base.resolve()),
    ]
    assert Path(commands[0][-1]).parent == tmp_path
    assert Path(commands[0][-1]).name.startswith(".validation.qcow2.p9qemu-")
    assert Path(commands[0][-1]).name.endswith(".part")
    assert list(tmp_path.glob("*.part")) == []


def test_validation_overlay_never_replaces_existing_file(tmp_path: Path) -> None:
    base = tmp_path / "base.qcow2"
    overlay = tmp_path / "validation.qcow2"
    base.write_bytes(b"base")
    overlay.write_bytes(b"existing")

    with pytest.raises(P9QemuError, match="refusing to replace validation overlay"):
        prepare_validation_overlay(
            "qemu-img", base, overlay, progress=lambda _message: None
        )
    assert overlay.read_bytes() == b"existing"


def test_failed_validation_overlay_creation_leaves_no_partial(tmp_path: Path) -> None:
    base = tmp_path / "base.qcow2"
    overlay = tmp_path / "validation.qcow2"
    base.write_bytes(b"base")

    def runner(command: list[str], **_kwargs):
        Path(command[-1]).write_bytes(b"partial")
        return SimpleNamespace(returncode=1)

    with pytest.raises(P9QemuError, match="overlay creation exited with status 1"):
        prepare_validation_overlay(
            "qemu-img",
            base,
            overlay,
            progress=lambda _message: None,
            runner=runner,
        )
    assert not overlay.exists()
    assert list(tmp_path.glob("*.part")) == []
