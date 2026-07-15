from pathlib import Path
from types import SimpleNamespace

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
    assert install.iso_url == (
        "https://github.com/dharmatech/p9qemu/releases/download/"
        "media-9front-11554/9front-11554.amd64.iso.gz"
    )

    start = cli.build_parser().parse_args(["start"])
    assert start.disk == Path("9front.qcow2.img")
    assert start.instance is None
    assert start.memory == 2048

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
    assert "Ready image: Synthetic 9front ready image" in output
    assert "Image ID: p9qemu-9front-test-stock-001" in output
    assert f"Manifest SHA-256: {'b' * 64}" in output
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
    assert f"Ready-image instance created: {destination}" in output
    assert f"Writable instance disk: {disk}" in output


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
    assert f"Using ready-image instance: {root}" in output
    assert "Ready image: Synthetic 9front ready image" in output
    assert f"Manifest SHA-256: {'b' * 64}" in output
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
