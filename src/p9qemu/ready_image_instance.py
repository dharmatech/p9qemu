"""Atomic writable instances backed by immutable cached ready images."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
from typing import Any
from uuid import uuid4

from p9qemu.errors import P9QemuError
from p9qemu.media import sha256_file
from p9qemu.provenance import qemu_img_info, write_json_new
from p9qemu.ready_image import (
    CachedReadyImage,
    load_cached_ready_image,
    verify_cached_ready_image,
)


Progress = Callable[[str], None]
Runner = Callable[..., subprocess.CompletedProcess[object]]

INSTANCE_SCHEMA = 1
INSTANCE_KIND = "p9qemu-ready-image-instance"
INSTANCE_DISK_NAME = "disk.qcow2"
INSTANCE_METADATA_NAME = "instance.json"
MAX_INSTANCE_METADATA_BYTES = 64 * 1024

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ReadyImageInstance:
    root: Path
    disk: Path
    metadata: Path
    cached: CachedReadyImage
    manifest_sha256: str


@dataclass(frozen=True)
class _InstanceRecord:
    disk: str
    virtual_size: int
    cache_entry: Path
    backing_path: Path
    manifest_sha256: str
    image_id: str
    image_sha256: str
    runtime_profile: str
    runtime_capabilities: tuple[str, ...]


def _object(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise P9QemuError(f"instance metadata requires an object at {label}")
    return value


def _exact_keys(document: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(document)
    if actual != expected:
        raise P9QemuError(
            f"instance metadata fields differ at {label}; "
            f"missing={sorted(expected - actual)}, unknown={sorted(actual - expected)}"
        )


def _text(value: object, label: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise P9QemuError(f"instance metadata requires non-empty text at {label}")
    if any(ord(character) < 32 for character in value):
        raise P9QemuError(f"instance metadata contains a control character at {label}")
    return value


def _sha256(value: object, label: str) -> str:
    text = _text(value, label, maximum=64)
    if not _SHA256.fullmatch(text):
        raise P9QemuError(f"instance metadata requires a SHA-256 at {label}")
    return text


def _positive_int(value: object, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise P9QemuError(f"instance metadata requires a positive integer at {label}")
    return value


def _absolute_path(value: object, label: str) -> Path:
    path = Path(_text(value, label))
    if not path.is_absolute():
        raise P9QemuError(f"instance metadata requires an absolute path at {label}")
    return path


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise P9QemuError(f"instance metadata contains a duplicate field: {key}")
        result[key] = value
    return result


def _load_record(path: Path) -> _InstanceRecord:
    try:
        content = path.read_bytes()
    except OSError as error:
        raise P9QemuError(f"could not read instance metadata {path}: {error}") from error
    if len(content) > MAX_INSTANCE_METADATA_BYTES:
        raise P9QemuError(f"instance metadata exceeds the size limit: {path}")
    try:
        document = json.loads(
            content.decode("utf-8"), object_pairs_hook=_unique_json_object
        )
    except (UnicodeError, json.JSONDecodeError) as error:
        raise P9QemuError(f"could not parse instance metadata {path}: {error}") from error
    root = _object(document, "root")
    _exact_keys(root, {"schema", "kind", "instance", "base", "runtime"}, "root")
    if (
        type(root.get("schema")) is not int
        or root.get("schema") != INSTANCE_SCHEMA
        or root.get("kind") != INSTANCE_KIND
    ):
        raise P9QemuError("unsupported ready-image instance metadata")

    instance = _object(root.get("instance"), "instance")
    _exact_keys(instance, {"disk", "format", "virtual_size"}, "instance")
    disk = _text(instance.get("disk"), "instance.disk")
    if disk != INSTANCE_DISK_NAME or instance.get("format") != "qcow2":
        raise P9QemuError("schema 1 instances require disk.qcow2 in QCOW2 format")

    base = _object(root.get("base"), "base")
    _exact_keys(
        base,
        {
            "cache_entry",
            "backing_path",
            "manifest_sha256",
            "image_id",
            "image_sha256",
        },
        "base",
    )
    runtime = _object(root.get("runtime"), "runtime")
    _exact_keys(runtime, {"profile", "capabilities"}, "runtime")
    capabilities_value = runtime.get("capabilities")
    if not isinstance(capabilities_value, list) or not capabilities_value:
        raise P9QemuError("instance metadata requires runtime capabilities")
    capabilities = tuple(
        _text(value, f"runtime.capabilities[{index}]", maximum=100)
        for index, value in enumerate(capabilities_value)
    )
    if len(capabilities) != len(set(capabilities)):
        raise P9QemuError("instance metadata repeats a runtime capability")
    return _InstanceRecord(
        disk=disk,
        virtual_size=_positive_int(
            instance.get("virtual_size"), "instance.virtual_size"
        ),
        cache_entry=_absolute_path(base.get("cache_entry"), "base.cache_entry"),
        backing_path=_absolute_path(base.get("backing_path"), "base.backing_path"),
        manifest_sha256=_sha256(
            base.get("manifest_sha256"), "base.manifest_sha256"
        ),
        image_id=_text(base.get("image_id"), "base.image_id", maximum=100),
        image_sha256=_sha256(base.get("image_sha256"), "base.image_sha256"),
        runtime_profile=_text(
            runtime.get("profile"), "runtime.profile", maximum=100
        ),
        runtime_capabilities=capabilities,
    )


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(
        os.path.abspath(right)
    )


def _verify_overlay_info(
    information: Mapping[str, object],
    *,
    backing: Path,
    virtual_size: int,
) -> None:
    if information.get("format") != "qcow2":
        raise P9QemuError("instance overlay is not QCOW2")
    reported_size = information.get("virtual-size")
    if type(reported_size) is not int or reported_size != virtual_size:
        raise P9QemuError("instance overlay virtual size does not match its base")
    backing_filename = information.get("backing-filename")
    if not isinstance(backing_filename, str) or not _same_path(
        Path(backing_filename), backing
    ):
        raise P9QemuError("instance overlay backing file does not match metadata")
    full_backing = information.get("full-backing-filename")
    if full_backing is not None and (
        not isinstance(full_backing, str)
        or not _same_path(Path(full_backing), backing)
    ):
        raise P9QemuError("instance overlay resolved backing file is inconsistent")
    backing_format = information.get("backing-filename-format")
    if backing_format is not None and backing_format != "qcow2":
        raise P9QemuError("instance overlay backing format is not QCOW2")
    dirty_flag = information.get("dirty-flag")
    if dirty_flag is not None and dirty_flag is not False:
        raise P9QemuError("instance overlay has a dirty QCOW2 flag")


def _verify_standalone_base_info(
    information: Mapping[str, object], *, virtual_size: int
) -> None:
    if information.get("format") != "qcow2":
        raise P9QemuError("ready-image base is not QCOW2")
    reported_size = information.get("virtual-size")
    if type(reported_size) is not int or reported_size != virtual_size:
        raise P9QemuError("ready-image base virtual size does not match its manifest")
    backing_fields = (
        "backing-filename",
        "full-backing-filename",
        "backing-filename-format",
    )
    if any(information.get(field) is not None for field in backing_fields):
        raise P9QemuError(
            "ready-image release base is not standalone; it has a backing file"
        )
    dirty_flag = information.get("dirty-flag")
    if dirty_flag is not None and dirty_flag is not False:
        raise P9QemuError("ready-image base has a dirty QCOW2 flag")


def _metadata_document(
    cached: CachedReadyImage, manifest_sha256: str
) -> dict[str, object]:
    manifest = cached.manifest
    return {
        "schema": INSTANCE_SCHEMA,
        "kind": INSTANCE_KIND,
        "instance": {
            "disk": INSTANCE_DISK_NAME,
            "format": "qcow2",
            "virtual_size": manifest.image.virtual_size,
        },
        "base": {
            "cache_entry": str(cached.entry.resolve()),
            "backing_path": str(cached.image.resolve()),
            "manifest_sha256": manifest_sha256,
            "image_id": manifest.image_id,
            "image_sha256": manifest.image.sha256,
        },
        "runtime": {
            "profile": manifest.runtime.profile,
            "capabilities": list(manifest.runtime.capabilities),
        },
    }


def _remove_tree_best_effort(path: Path) -> None:
    try:
        shutil.rmtree(path)
    except OSError:
        pass


def _publish_instance(staging: Path, destination: Path) -> None:
    created = False
    try:
        destination.mkdir()
        created = True
        os.link(staging / INSTANCE_DISK_NAME, destination / INSTANCE_DISK_NAME)
        os.link(
            staging / INSTANCE_METADATA_NAME,
            destination / INSTANCE_METADATA_NAME,
        )
    except FileExistsError as error:
        raise P9QemuError(f"refusing to replace ready-image instance: {destination}") from error
    except OSError as error:
        raise P9QemuError(f"could not publish ready-image instance: {error}") from error
    finally:
        if created and not (destination / INSTANCE_METADATA_NAME).is_file():
            _remove_tree_best_effort(destination)


def create_ready_image_instance(
    qemu_img: str,
    cached: CachedReadyImage,
    destination: Path,
    *,
    progress: Progress,
    runner: Runner = subprocess.run,
) -> ReadyImageInstance:
    """Create and verify one new writable overlay instance without launching it."""

    if destination.exists():
        raise P9QemuError(f"refusing to replace ready-image instance: {destination}")
    if not destination.parent.is_dir():
        raise P9QemuError(
            f"ready-image instance parent directory does not exist: {destination.parent}"
        )
    verified = verify_cached_ready_image(cached)
    base = verified.image.resolve()
    manifest_path = verified.entry / "image.json"
    manifest_sha256 = sha256_file(manifest_path)
    base_information = qemu_img_info(qemu_img, base, runner=runner)
    _verify_standalone_base_info(
        base_information,
        virtual_size=verified.manifest.image.virtual_size,
    )
    progress(f"Verified standalone ready-image base: {base}")
    staging = destination.with_name(
        f".{destination.name}.p9qemu-{uuid4().hex}.part"
    )
    disk = staging / INSTANCE_DISK_NAME
    metadata = staging / INSTANCE_METADATA_NAME
    command = [
        qemu_img,
        "create",
        "-f",
        "qcow2",
        "-F",
        "qcow2",
        "-b",
        str(base),
        str(disk),
    ]
    progress(f"Creating writable ready-image instance: {destination}")
    try:
        staging.mkdir()
        result = runner(command, check=False)
        if result.returncode != 0:
            raise P9QemuError(
                f"qemu-img overlay creation exited with status {result.returncode}"
            )
        if not disk.is_file() or disk.is_symlink():
            raise P9QemuError("qemu-img did not create the requested instance overlay")
        information = qemu_img_info(qemu_img, disk, runner=runner)
        _verify_overlay_info(
            information,
            backing=base,
            virtual_size=verified.manifest.image.virtual_size,
        )
        if sha256_file(base) != verified.manifest.image.sha256:
            raise P9QemuError("immutable base image changed during overlay creation")
        write_json_new(metadata, _metadata_document(verified, manifest_sha256))
        _load_record(metadata)
        _publish_instance(staging, destination)
    except P9QemuError:
        raise
    except OSError as error:
        raise P9QemuError(f"could not create ready-image instance: {error}") from error
    finally:
        _remove_tree_best_effort(staging)
    progress(f"Verified writable overlay backing file: {base}")
    return ReadyImageInstance(
        root=destination,
        disk=destination / INSTANCE_DISK_NAME,
        metadata=destination / INSTANCE_METADATA_NAME,
        cached=verified,
        manifest_sha256=manifest_sha256,
    )


def verify_ready_image_instance(
    qemu_img: str,
    root: Path,
    *,
    runner: Runner = subprocess.run,
) -> ReadyImageInstance:
    """Reverify instance metadata, immutable base, and overlay backing relation."""

    if not root.is_dir() or root.is_symlink():
        raise P9QemuError(f"ready-image instance is not a directory: {root}")
    metadata = root / INSTANCE_METADATA_NAME
    if not metadata.is_file() or metadata.is_symlink():
        raise P9QemuError(f"ready-image instance metadata is not a file: {metadata}")
    record = _load_record(metadata)
    disk = root / record.disk
    if not disk.is_file() or disk.is_symlink():
        raise P9QemuError(f"ready-image instance disk is not a file: {disk}")
    write_bits = stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH
    if not disk.stat().st_mode & write_bits:
        raise P9QemuError(f"ready-image instance disk is not writable: {disk}")

    cached = load_cached_ready_image(record.cache_entry)
    manifest_path = cached.entry / "image.json"
    if sha256_file(manifest_path) != record.manifest_sha256:
        raise P9QemuError("instance source manifest checksum does not match metadata")
    manifest = cached.manifest
    if (
        manifest.image_id != record.image_id
        or manifest.image.sha256 != record.image_sha256
        or manifest.image.virtual_size != record.virtual_size
        or manifest.runtime.profile != record.runtime_profile
        or manifest.runtime.capabilities != record.runtime_capabilities
    ):
        raise P9QemuError("instance metadata does not match its cached ready image")
    if not _same_path(cached.image, record.backing_path):
        raise P9QemuError("instance metadata backing path does not match its cache entry")
    base_information = qemu_img_info(qemu_img, cached.image, runner=runner)
    _verify_standalone_base_info(
        base_information,
        virtual_size=record.virtual_size,
    )
    information = qemu_img_info(qemu_img, disk, runner=runner)
    _verify_overlay_info(
        information,
        backing=cached.image,
        virtual_size=record.virtual_size,
    )
    return ReadyImageInstance(
        root=root,
        disk=disk,
        metadata=metadata,
        cached=cached,
        manifest_sha256=record.manifest_sha256,
    )
