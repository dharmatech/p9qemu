from pathlib import Path

import pytest

from p9qemu import cli
from p9qemu.host import Acceleration, HostInfo, QemuExecutables


def configure_fake_windows(monkeypatch: pytest.MonkeyPatch, cache: Path) -> None:
    monkeypatch.setattr(cli, "current_host", lambda: HostInfo("Windows"))
    monkeypatch.setattr(
        cli,
        "discover_qemu",
        lambda _host: QemuExecutables(
            system=r"C:\Program Files\qemu\qemu-system-x86_64.exe",
            image=r"C:\Program Files\qemu\qemu-img.exe",
        ),
    )
    monkeypatch.setattr(
        cli,
        "resolve_acceleration",
        lambda _requested, _host: Acceleration(
            "TCG software emulation", ("-accel", "tcg")
        ),
    )
    monkeypatch.setattr(cli, "user_cache_dir", lambda _host: cache)


def test_parser_defaults() -> None:
    install = cli.build_parser().parse_args(["install"])
    assert install.disk == Path("9front.qcow2.img")
    assert install.disk_size == "30G"
    assert install.memory == 1024
    assert install.accel == "auto"

    start = cli.build_parser().parse_args(["start"])
    assert start.disk == Path("9front.qcow2.img")
    assert start.memory == 2048


def test_install_dry_run_has_no_side_effects_and_prints_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cache = tmp_path / "cache"
    instance = tmp_path / "instance"
    instance.mkdir()
    monkeypatch.chdir(instance)
    configure_fake_windows(monkeypatch, cache)

    result = cli.run(["install", "--dry-run", "--accel", "tcg"])

    assert result == 0
    assert not cache.exists()
    assert not (instance / "9front.qcow2.img").exists()
    output = capsys.readouterr().out
    assert "Would download " in output
    assert "Would create 30G QCOW2 disk image" in output
    assert "Would start QEMU:" in output
    assert "'-drive'" not in output
    assert "format=raw" in output


def test_start_dry_run_prints_runtime_forwards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    disk = tmp_path / "9front.qcow2.img"
    disk.write_bytes(b"disk")
    configure_fake_windows(monkeypatch, tmp_path / "cache")
    result = cli.run(["start", "--disk", str(disk), "--dry-run", "--accel", "tcg"])
    assert result == 0
    output = capsys.readouterr().out
    assert "Would start QEMU:" in output
    assert "hostfwd=tcp:127.0.0.1:17564-:564" in output


def test_start_requires_an_existing_disk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_fake_windows(monkeypatch, tmp_path / "cache")
    result = cli.run(["start", "--disk", str(tmp_path / "missing.qcow2")])
    assert result == 1
    assert "Run p9qemu install" in capsys.readouterr().err


def test_quiet_dry_run_has_no_routine_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    disk = tmp_path / "9front.qcow2.img"
    disk.write_bytes(b"disk")
    configure_fake_windows(monkeypatch, tmp_path / "cache")
    result = cli.run(
        ["start", "--disk", str(disk), "--dry-run", "--accel", "tcg", "--quiet"]
    )
    assert result == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_explicit_whpx_queries_qemu_and_prints_required_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    disk = tmp_path / "9front.qcow2.img"
    disk.write_bytes(b"disk")
    executable = r"C:\Program Files\qemu\qemu-system-x86_64.exe"
    monkeypatch.setattr(cli, "current_host", lambda: HostInfo("Windows"))
    monkeypatch.setattr(
        cli,
        "discover_qemu",
        lambda _host: QemuExecutables(
            system=executable,
            image=r"C:\Program Files\qemu\qemu-img.exe",
        ),
    )
    queried: list[str] = []

    def query(path: str) -> frozenset[str]:
        queried.append(path)
        return frozenset({"tcg", "whpx"})

    monkeypatch.setattr(cli, "query_qemu_accelerators", query)
    result = cli.run(["start", "--disk", str(disk), "--dry-run", "--accel", "whpx"])
    assert result == 0
    assert queried == [executable]
    output = capsys.readouterr().out
    assert "Acceleration: WHPX with userspace irqchip and SDL (no fallback)" in output
    assert "-accel whpx,kernel-irqchip=off -display sdl" in output
    assert "-smp" not in output


def test_explicit_whpx_fails_early_when_qemu_does_not_advertise_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    disk = tmp_path / "9front.qcow2.img"
    disk.write_bytes(b"disk")
    monkeypatch.setattr(cli, "current_host", lambda: HostInfo("Windows"))
    monkeypatch.setattr(
        cli,
        "discover_qemu",
        lambda _host: QemuExecutables("qemu-system-x86_64", "qemu-img"),
    )
    monkeypatch.setattr(
        cli,
        "query_qemu_accelerators",
        lambda _executable: frozenset({"tcg"}),
    )
    result = cli.run(["start", "--disk", str(disk), "--dry-run", "--accel", "whpx"])
    assert result == 1
    assert "does not advertise WHPX" in capsys.readouterr().err


def test_install_whpx_capability_failure_has_no_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = tmp_path / "instance"
    cache = tmp_path / "cache"
    instance.mkdir()
    monkeypatch.chdir(instance)
    monkeypatch.setattr(cli, "current_host", lambda: HostInfo("Windows"))
    monkeypatch.setattr(
        cli,
        "discover_qemu",
        lambda _host: QemuExecutables("qemu-system-x86_64", "qemu-img"),
    )
    monkeypatch.setattr(cli, "user_cache_dir", lambda _host: cache)
    monkeypatch.setattr(
        cli,
        "query_qemu_accelerators",
        lambda _executable: frozenset({"tcg"}),
    )
    result = cli.run(["install", "--accel", "whpx"])
    assert result == 1
    assert not cache.exists()
    assert not (instance / "9front.qcow2.img").exists()
