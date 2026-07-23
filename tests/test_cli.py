from pathlib import Path
from types import SimpleNamespace

import pytest

from p9qemu import cli
from p9qemu.errors import P9QemuError
from p9qemu.host import Acceleration, HostInfo, QemuExecutables
from p9qemu.qemu import DEFAULT_PORT_FORWARDS


@pytest.fixture(autouse=True)
def disable_live_forward_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli,
        "require_port_forwards_available",
        lambda _forwards: None,
    )


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
    assert install.iso_url == (
        "https://github.com/dharmatech/p9qemu/releases/download/"
        "media-9front-11554/9front-11554.amd64.iso.gz"
    )

    start = cli.build_parser().parse_args(["start"])
    assert start.disk == Path("9front.qcow2.img")
    assert start.instance is None
    assert start.memory == 2048
    assert start.host_forward_address == "127.0.0.1"
    assert start.serial_console is False
    assert start.serial_log is None

    addressed_start = cli.build_parser().parse_args(
        ["start", "--host-forward-address", "127.0.0.20"]
    )
    assert addressed_start.host_forward_address == "127.0.0.20"

    serial_start = cli.build_parser().parse_args(
        [
            "start",
            "--serial-console",
            "--serial-log",
            "boot.raw.log",
        ]
    )
    assert serial_start.serial_console is True
    assert serial_start.serial_log == Path("boot.raw.log")

    image_create = cli.build_parser().parse_args(
        [
            "image",
            "create",
            "https://example.test/image.json",
            "my-instance",
        ]
    )
    assert image_create.manifest_url == "https://example.test/image.json"
    assert image_create.instance_dir == Path("my-instance")
    assert image_create.dry_run is False


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
    assert (
        "Would download https://github.com/dharmatech/p9qemu/releases/download/"
        "media-9front-11554/9front-11554.amd64.iso.gz" in output
    )
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
    assert (
        f"Using disk image:     {disk}\n"
        "Acceleration:         TCG software emulation\n"
        "Host-forward address: 127.0.0.1\n"
    ) in output
    assert "Would start QEMU:" in output
    assert "hostfwd=tcp:127.0.0.1:17564-:564" in output


def test_start_dry_run_shows_graphical_terminal_serial_and_does_not_create_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    disk = tmp_path / "9front.qcow2.img"
    serial_log = tmp_path / "boot.raw.log"
    disk.write_bytes(b"disk")
    configure_fake_windows(monkeypatch, tmp_path / "cache")

    result = cli.run(
        [
            "start",
            "--disk",
            str(disk),
            "--serial-console",
            "--serial-log",
            str(serial_log),
            "--dry-run",
            "--accel",
            "tcg",
        ]
    )

    assert result == 0
    assert not serial_log.exists()
    output = capsys.readouterr().out
    assert "Serial console:       terminal (interactive)" in output
    assert f"Serial log:           {serial_log}" in output
    assert "-monitor none" in output
    assert f"-chardev stdio,id=serial0,logfile={serial_log},logappend=on" in output
    assert "-serial chardev:serial0" in output
    assert "-nographic" not in output


def test_start_serial_log_reserves_a_new_file_before_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disk = tmp_path / "9front.qcow2.img"
    serial_log = tmp_path / "boot.raw.log"
    disk.write_bytes(b"disk")
    configure_fake_windows(monkeypatch, tmp_path / "cache")
    observed: list[tuple[list[str], bool]] = []

    def record_launch(
        command: list[str], *, system: str, dry_run: bool, quiet: bool
    ) -> int:
        assert system == "Windows"
        assert dry_run is False
        assert quiet is True
        observed.append((command, serial_log.exists()))
        return 0

    monkeypatch.setattr(cli, "_run_qemu", record_launch)

    result = cli.run(
        [
            "start",
            "--disk",
            str(disk),
            "--serial-log",
            str(serial_log),
            "--accel",
            "tcg",
            "--quiet",
        ]
    )

    assert result == 0
    assert observed and observed[0][1] is True
    assert serial_log.read_bytes() == b""
    assert any(
        argument.startswith("vc,id=serial0,logfile=") for argument in observed[0][0]
    )


def test_start_serial_log_refuses_an_existing_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    disk = tmp_path / "9front.qcow2.img"
    serial_log = tmp_path / "boot.raw.log"
    disk.write_bytes(b"disk")
    serial_log.write_bytes(b"preserve me")
    configure_fake_windows(monkeypatch, tmp_path / "cache")

    result = cli.run(
        [
            "start",
            "--disk",
            str(disk),
            "--serial-log",
            str(serial_log),
            "--dry-run",
            "--accel",
            "tcg",
        ]
    )

    assert result == 1
    assert serial_log.read_bytes() == b"preserve me"
    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"refusing to replace serial log: {serial_log}" in captured.err


def test_start_serial_log_reservation_closes_the_preflight_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    disk = tmp_path / "9front.qcow2.img"
    serial_log = tmp_path / "boot.raw.log"
    disk.write_bytes(b"disk")
    configure_fake_windows(monkeypatch, tmp_path / "cache")

    def claim_log(_forwards) -> None:
        serial_log.write_bytes(b"claimed during preflight")

    monkeypatch.setattr(cli, "require_port_forwards_available", claim_log)

    result = cli.run(
        [
            "start",
            "--disk",
            str(disk),
            "--serial-log",
            str(serial_log),
            "--accel",
            "tcg",
            "--quiet",
        ]
    )

    assert result == 1
    assert serial_log.read_bytes() == b"claimed during preflight"
    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"refusing to replace serial log: {serial_log}" in captured.err


def test_start_serial_log_requires_an_existing_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    disk = tmp_path / "9front.qcow2.img"
    serial_log = tmp_path / "missing" / "boot.raw.log"
    disk.write_bytes(b"disk")
    configure_fake_windows(monkeypatch, tmp_path / "cache")

    result = cli.run(
        [
            "start",
            "--disk",
            str(disk),
            "--serial-log",
            str(serial_log),
            "--dry-run",
            "--accel",
            "tcg",
        ]
    )

    assert result == 1
    assert not serial_log.parent.exists()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"serial log parent directory does not exist: {serial_log.parent}" in (
        captured.err
    )


def test_start_rewrites_complete_forward_map_to_explicit_loopback_address(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    disk = tmp_path / "9front.qcow2.img"
    disk.write_bytes(b"disk")
    configure_fake_windows(monkeypatch, tmp_path / "cache")
    preflighted: list[tuple] = []
    monkeypatch.setattr(
        cli,
        "require_port_forwards_available",
        lambda forwards: preflighted.append(forwards),
    )

    result = cli.run(
        [
            "start",
            "--disk",
            str(disk),
            "--host-forward-address",
            "127.0.0.20",
            "--dry-run",
            "--accel",
            "tcg",
        ]
    )

    assert result == 0
    assert len(preflighted) == 1
    assert all(forward.host_address == "127.0.0.20" for forward in preflighted[0])
    output = capsys.readouterr().out
    assert "Host-forward address: 127.0.0.20" in output
    for forward in DEFAULT_PORT_FORWARDS:
        assert (
            f"hostfwd={forward.protocol}:127.0.0.20:{forward.host_port}"
            f"-:{forward.guest_port}"
        ) in output
    assert "hostfwd=tcp:127.0.0.1:" not in output


@pytest.mark.parametrize(
    "address",
    (
        "localhost",
        "0.0.0.0",
        "192.0.2.1",
        "::1",
        "127.0.0.020",
    ),
)
def test_start_parser_rejects_noncanonical_or_nonloopback_address(
    address: str,
) -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["start", "--host-forward-address", address])


def test_start_reports_listener_conflict_before_printing_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    disk = tmp_path / "9front.qcow2.img"
    disk.write_bytes(b"disk")
    configure_fake_windows(monkeypatch, tmp_path / "cache")

    def reject(_forwards) -> None:
        raise P9QemuError(
            "TCP host-forward endpoint is unavailable: "
            "127.0.0.20:17019: address already in use"
        )

    monkeypatch.setattr(cli, "require_port_forwards_available", reject)

    result = cli.run(
        [
            "start",
            "--disk",
            str(disk),
            "--host-forward-address",
            "127.0.0.20",
            "--dry-run",
            "--accel",
            "tcg",
        ]
    )

    assert result == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "127.0.0.20:17019" in captured.err
    assert "Would start QEMU" not in captured.err


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
    assert (
        "Acceleration:         WHPX with userspace irqchip and SDL (no fallback)"
        in output
    )
    assert "    -m 2048 -accel whpx,kernel-irqchip=off -display sdl `\n" in output
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


def _ready_manifest() -> SimpleNamespace:
    return SimpleNamespace(
        title="Synthetic 9front ready image",
        image_id="p9qemu-9front-test-stock-001",
        artifact=SimpleNamespace(
            size=123456,
            url="https://downloads.example.test/image.tar.gz?private=secret",
        ),
        image=SimpleNamespace(sha256="a" * 64),
    )


def test_image_create_dry_run_fetches_only_manifest_and_describes_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cache = tmp_path / "cache"
    configure_fake_windows(monkeypatch, cache)
    manifest = _ready_manifest()
    manifest_path = cache / "manifests" / ("b" * 64) / "image.json"
    calls: list[tuple[str, object]] = []

    def fetch(url: str, cache_dir: Path, *, progress):
        calls.append(("fetch", (url, cache_dir)))
        progress("Fetched test manifest")
        return SimpleNamespace(
            manifest=manifest,
            path=manifest_path,
            sha256="b" * 64,
        )

    monkeypatch.setattr(cli, "fetch_ready_image_manifest", fetch)
    monkeypatch.setattr(
        cli,
        "acquire_ready_image_archive",
        lambda *_args, **_kwargs: pytest.fail("archive must not be acquired"),
    )
    monkeypatch.setattr(
        cli,
        "install_local_ready_image",
        lambda *_args, **_kwargs: pytest.fail("image must not be cached"),
    )
    monkeypatch.setattr(
        cli,
        "create_ready_image_instance",
        lambda *_args, **_kwargs: pytest.fail("instance must not be created"),
    )
    destination = tmp_path / "new-instance"

    result = cli.run(
        [
            "image",
            "create",
            "https://example.test/image.json",
            str(destination),
            "--dry-run",
        ]
    )

    assert result == 0
    assert calls == [
        (
            "fetch",
            ("https://example.test/image.json", cache),
        )
    ]
    assert not destination.exists()
    output = capsys.readouterr().out
    assert (
        "Ready image:      Synthetic 9front ready image\n"
        "Image ID:         p9qemu-9front-test-stock-001\n"
        f"Manifest SHA-256: {'b' * 64}\n"
    ) in output
    assert "Would download ready-image archive (123456 bytes)" in output
    assert "private=secret" not in output
    assert f"Would verify and cache immutable image: {'a' * 64}" in output
    assert f"Would create writable ready-image instance: {destination}" in output


def test_image_create_composes_acquisition_cache_and_instance_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cache = tmp_path / "cache"
    configure_fake_windows(monkeypatch, cache)
    manifest = _ready_manifest()
    manifest_path = tmp_path / "downloaded-image.json"
    archive_path = tmp_path / "downloaded-image.tar.gz"
    acquired_manifest = SimpleNamespace(
        manifest=manifest,
        path=manifest_path,
        sha256="b" * 64,
    )
    acquired_archive = SimpleNamespace(path=archive_path)
    cached = object()
    destination = tmp_path / "new-instance"
    disk = destination / "disk.qcow2"
    created = SimpleNamespace(root=destination, disk=disk)
    calls: list[tuple[str, object]] = []

    def fetch(url: str, cache_dir: Path, *, progress):
        calls.append(("fetch", (url, cache_dir)))
        return acquired_manifest

    def acquire(selected, cache_dir: Path, *, progress):
        calls.append(("archive", (selected, cache_dir)))
        return acquired_archive

    def install(manifest_file: Path, archive: Path, cache_dir: Path, *, progress):
        calls.append(("install", (manifest_file, archive, cache_dir)))
        return cached

    def create(qemu_img: str, selected, root: Path, *, progress):
        calls.append(("create", (qemu_img, selected, root)))
        return created

    monkeypatch.setattr(cli, "fetch_ready_image_manifest", fetch)
    monkeypatch.setattr(cli, "acquire_ready_image_archive", acquire)
    monkeypatch.setattr(cli, "install_local_ready_image", install)
    monkeypatch.setattr(cli, "create_ready_image_instance", create)

    result = cli.run(
        [
            "image",
            "create",
            "https://example.test/image.json",
            str(destination),
        ]
    )

    assert result == 0
    assert calls == [
        (
            "fetch",
            ("https://example.test/image.json", cache),
        ),
        ("archive", (manifest, cache)),
        ("install", (manifest_path, archive_path, cache)),
        (
            "create",
            (r"C:\Program Files\qemu\qemu-img.exe", cached, destination),
        ),
    ]
    output = capsys.readouterr().out
    assert (
        f"Ready-image instance created: {destination}\n"
        f"Writable instance disk:       {disk}\n"
    ) in output


def test_start_instance_reverifies_it_and_launches_its_overlay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_fake_windows(monkeypatch, tmp_path / "cache")
    root = tmp_path / "ready-instance"
    disk = root / "disk.qcow2"
    manifest = _ready_manifest()
    verified = SimpleNamespace(
        root=root,
        disk=disk,
        manifest_sha256="b" * 64,
        cached=SimpleNamespace(manifest=manifest),
    )
    calls: list[tuple[str, Path]] = []

    def verify(qemu_img: str, selected_root: Path):
        calls.append((qemu_img, selected_root))
        return verified

    monkeypatch.setattr(cli, "verify_ready_image_instance", verify)

    result = cli.run(
        [
            "start",
            "--instance",
            str(root),
            "--dry-run",
            "--accel",
            "tcg",
        ]
    )

    assert result == 0
    assert calls == [(r"C:\Program Files\qemu\qemu-img.exe", root)]
    output = capsys.readouterr().out
    assert (
        f"Using ready-image instance: {root}\n"
        "Ready image:                Synthetic 9front ready image\n"
        "Image ID:                   p9qemu-9front-test-stock-001\n"
        f"Manifest SHA-256:           {'b' * 64}\n"
        f"Using disk image:           {disk}\n"
        "Acceleration:               TCG software emulation\n"
        "Host-forward address:       127.0.0.1\n"
    ) in output
    assert f"file={disk},format=qcow2" in output
    assert "Would start QEMU:" in output


def test_start_rejects_disk_and_instance_together() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(
            [
                "start",
                "--disk",
                "standalone.qcow2",
                "--instance",
                "ready-instance",
            ]
        )
