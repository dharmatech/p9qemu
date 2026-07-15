from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from p9qemu.errors import P9QemuError
from p9qemu.ready_image import CachedReadyImage, parse_ready_image_manifest
import p9qemu.ready_image_instance as instance_module
from p9qemu.ready_image_instance import (
    INSTANCE_DISK_NAME,
    INSTANCE_METADATA_NAME,
    create_ready_image_instance,
    verify_ready_image_instance,
)


EXAMPLE_MANIFEST = (
    Path(__file__).parents[1]
    / "images"
    / "manifests"
    / "p9qemu-9front-11554-amd64-hjfs-gmt-002.example.json"
)


class FakeQemuImg:
    def __init__(
        self,
        backing: Path,
        virtual_size: int,
        *,
        create_status: int = 0,
        reported_backing: Path | None = None,
        mutate_base: bool = False,
    ) -> None:
        self.backing = backing.resolve()
        self.virtual_size = virtual_size
        self.create_status = create_status
        self.reported_backing = (reported_backing or backing).resolve()
        self.mutate_base = mutate_base
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], **_kwargs):
        self.commands.append(command)
        if command[1] == "create":
            Path(command[-1]).write_bytes(b"writable overlay")
            if self.mutate_base:
                self.backing.chmod(stat.S_IREAD | stat.S_IWRITE)
                self.backing.write_bytes(b"mutated base")
            return SimpleNamespace(returncode=self.create_status, stdout="", stderr="")
        assert command[1:3] == ["info", "--output=json"]
        information = {
            "format": "qcow2",
            "virtual-size": self.virtual_size,
            "backing-filename": str(self.reported_backing),
            "full-backing-filename": str(self.reported_backing),
            "backing-filename-format": "qcow2",
            "dirty-flag": False,
        }
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(information),
            stderr="",
        )


def _cached_fixture(tmp_path: Path) -> CachedReadyImage:
    base_content = b"immutable ready-image base"
    image_sha256 = hashlib.sha256(base_content).hexdigest()
    document = json.loads(EXAMPLE_MANIFEST.read_text(encoding="utf-8"))
    image = document["image"]
    assert isinstance(image, dict)
    image.update(
        {
            "path": "base.qcow2",
            "stored_size": len(base_content),
            "virtual_size": 32 * 1024 * 1024,
            "sha256": image_sha256,
        }
    )
    manifest = parse_ready_image_manifest(document)
    entry = tmp_path / "cache" / "images" / image_sha256
    bundle = entry / "bundle" / manifest.bundle.root
    bundle.mkdir(parents=True)
    base = bundle / "base.qcow2"
    base.write_bytes(base_content)
    base.chmod(base.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    (entry / "image.json").write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return CachedReadyImage(manifest, entry, bundle, base)


def _allow_synthetic_cache(
    monkeypatch: pytest.MonkeyPatch, cached: CachedReadyImage
) -> None:
    monkeypatch.setattr(
        instance_module,
        "verify_cached_ready_image",
        lambda candidate: cached if candidate == cached else None,
    )
    monkeypatch.setattr(
        instance_module,
        "load_cached_ready_image",
        lambda entry: cached if entry == cached.entry.resolve() else None,
    )


def test_instance_is_staged_verified_and_published_with_metadata_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cached = _cached_fixture(tmp_path)
    _allow_synthetic_cache(monkeypatch, cached)
    destination = tmp_path / "instance"
    runner = FakeQemuImg(cached.image, cached.manifest.image.virtual_size)
    messages: list[str] = []

    created = create_ready_image_instance(
        "qemu-img",
        cached,
        destination,
        progress=messages.append,
        runner=runner,
    )

    assert created.root == destination
    assert created.disk.read_bytes() == b"writable overlay"
    assert set(path.name for path in destination.iterdir()) == {
        INSTANCE_DISK_NAME,
        INSTANCE_METADATA_NAME,
    }
    create_command = runner.commands[0]
    assert create_command[:-1] == [
        "qemu-img",
        "create",
        "-f",
        "qcow2",
        "-F",
        "qcow2",
        "-b",
        str(cached.image.resolve()),
    ]
    assert Path(create_command[-1]).name == INSTANCE_DISK_NAME
    assert Path(create_command[-1]).parent.name.startswith(".instance.p9qemu-")
    assert runner.commands[1][1:3] == ["info", "--output=json"]
    metadata = json.loads(created.metadata.read_text(encoding="utf-8"))
    assert metadata["kind"] == "p9qemu-ready-image-instance"
    assert metadata["base"]["cache_entry"] == str(cached.entry.resolve())
    assert metadata["base"]["backing_path"] == str(cached.image.resolve())
    assert metadata["base"]["image_sha256"] == cached.manifest.image.sha256
    assert metadata["runtime"]["profile"] == cached.manifest.runtime.profile
    assert created.manifest_sha256 == hashlib.sha256(
        (cached.entry / "image.json").read_bytes()
    ).hexdigest()
    assert not list(tmp_path.glob(".instance.p9qemu-*.part"))
    assert messages == [
        f"Creating writable ready-image instance: {destination}",
        f"Verified writable overlay backing file: {cached.image.resolve()}",
    ]


def test_instance_verification_rechecks_metadata_base_and_overlay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cached = _cached_fixture(tmp_path)
    _allow_synthetic_cache(monkeypatch, cached)
    destination = tmp_path / "instance"
    create_runner = FakeQemuImg(cached.image, cached.manifest.image.virtual_size)
    created = create_ready_image_instance(
        "qemu-img",
        cached,
        destination,
        progress=lambda _message: None,
        runner=create_runner,
    )
    verify_runner = FakeQemuImg(cached.image, cached.manifest.image.virtual_size)

    verified = verify_ready_image_instance(
        "qemu-img", destination, runner=verify_runner
    )

    assert verified == created
    assert verify_runner.commands == [
        ["qemu-img", "info", "--output=json", str(created.disk)]
    ]


def test_existing_destination_is_refused_before_cache_or_qemu_access(
    tmp_path: Path,
) -> None:
    cached = _cached_fixture(tmp_path)
    destination = tmp_path / "instance"
    destination.mkdir()

    with pytest.raises(P9QemuError, match="refusing to replace"):
        create_ready_image_instance(
            "qemu-img",
            cached,
            destination,
            progress=lambda _message: None,
            runner=lambda *_args, **_kwargs: pytest.fail("qemu-img must not run"),
        )


def test_failed_qemu_img_creation_removes_all_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cached = _cached_fixture(tmp_path)
    _allow_synthetic_cache(monkeypatch, cached)
    destination = tmp_path / "instance"
    runner = FakeQemuImg(
        cached.image,
        cached.manifest.image.virtual_size,
        create_status=1,
    )

    with pytest.raises(P9QemuError, match="status 1"):
        create_ready_image_instance(
            "qemu-img",
            cached,
            destination,
            progress=lambda _message: None,
            runner=runner,
        )

    assert not destination.exists()
    assert not list(tmp_path.glob(".instance.p9qemu-*.part"))


def test_wrong_backing_file_is_rejected_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cached = _cached_fixture(tmp_path)
    _allow_synthetic_cache(monkeypatch, cached)
    wrong = tmp_path / "wrong.qcow2"
    wrong.write_bytes(b"wrong")
    destination = tmp_path / "instance"
    runner = FakeQemuImg(
        cached.image,
        cached.manifest.image.virtual_size,
        reported_backing=wrong,
    )

    with pytest.raises(P9QemuError, match="backing file"):
        create_ready_image_instance(
            "qemu-img",
            cached,
            destination,
            progress=lambda _message: None,
            runner=runner,
        )

    assert not destination.exists()
    assert not list(tmp_path.glob(".instance.p9qemu-*.part"))


def test_base_change_during_creation_is_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cached = _cached_fixture(tmp_path)
    _allow_synthetic_cache(monkeypatch, cached)
    destination = tmp_path / "instance"
    runner = FakeQemuImg(
        cached.image,
        cached.manifest.image.virtual_size,
        mutate_base=True,
    )

    with pytest.raises(P9QemuError, match="base image changed"):
        create_ready_image_instance(
            "qemu-img",
            cached,
            destination,
            progress=lambda _message: None,
            runner=runner,
        )

    assert not destination.exists()
    assert not list(tmp_path.glob(".instance.p9qemu-*.part"))


def test_verification_rejects_tampered_instance_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cached = _cached_fixture(tmp_path)
    _allow_synthetic_cache(monkeypatch, cached)
    destination = tmp_path / "instance"
    created = create_ready_image_instance(
        "qemu-img",
        cached,
        destination,
        progress=lambda _message: None,
        runner=FakeQemuImg(cached.image, cached.manifest.image.virtual_size),
    )
    document = json.loads(created.metadata.read_text(encoding="utf-8"))
    document["base"]["manifest_sha256"] = "0" * 64
    created.metadata.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(P9QemuError, match="manifest checksum"):
        verify_ready_image_instance(
            "qemu-img",
            destination,
            runner=FakeQemuImg(cached.image, cached.manifest.image.virtual_size),
        )


def test_verification_rejects_read_only_overlay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cached = _cached_fixture(tmp_path)
    _allow_synthetic_cache(monkeypatch, cached)
    destination = tmp_path / "instance"
    created = create_ready_image_instance(
        "qemu-img",
        cached,
        destination,
        progress=lambda _message: None,
        runner=FakeQemuImg(cached.image, cached.manifest.image.virtual_size),
    )
    created.disk.chmod(
        created.disk.stat().st_mode
        & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    )

    with pytest.raises(P9QemuError, match="disk is not writable"):
        verify_ready_image_instance(
            "qemu-img",
            destination,
            runner=FakeQemuImg(cached.image, cached.manifest.image.virtual_size),
        )
    created.disk.chmod(stat.S_IREAD | stat.S_IWRITE)


def test_verification_rejects_unknown_metadata_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cached = _cached_fixture(tmp_path)
    _allow_synthetic_cache(monkeypatch, cached)
    destination = tmp_path / "instance"
    created = create_ready_image_instance(
        "qemu-img",
        cached,
        destination,
        progress=lambda _message: None,
        runner=FakeQemuImg(cached.image, cached.manifest.image.virtual_size),
    )
    document = json.loads(created.metadata.read_text(encoding="utf-8"))
    document["unexpected"] = True
    created.metadata.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(P9QemuError, match="fields differ"):
        verify_ready_image_instance(
            "qemu-img",
            destination,
            runner=FakeQemuImg(cached.image, cached.manifest.image.virtual_size),
        )
