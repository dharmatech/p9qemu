"""Local, streaming generation of external ready-image manifests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import tarfile
from typing import Any, BinaryIO

from p9qemu.errors import P9QemuError
from p9qemu.portable_path import portable_relative_path
from p9qemu.provenance import write_json_new
from p9qemu.ready_image import (
    IMAGE_MANIFEST_KIND,
    IMAGE_MANIFEST_SCHEMA,
    MAX_ARCHIVE_BYTES,
    MAX_MANIFEST_BYTES,
    SUPPORTED_PACKAGING,
    ImageMetadata,
    ReadyImageManifest,
    parse_ready_image_manifest,
)
from p9qemu.release_candidate import inspect_release_archive_headers


_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class ReadyImageManifestInputs:
    archive: Path
    output: Path
    asset_url: str
    title: str
    variant: str
    distribution: str
    release: str
    architecture: str
    capabilities: tuple[str, ...]


@dataclass(frozen=True)
class ReadyImageArchiveInspection:
    bundle_name: str
    archive_size: int
    archive_sha256: str
    member_count: int
    file_count: int
    extracted_size: int
    manifest_sha256: str
    image: ImageMetadata
    runtime_profile: str


@dataclass(frozen=True)
class ReadyImageManifestBuild:
    inputs: ReadyImageManifestInputs
    inspection: ReadyImageArchiveInspection
    document: dict[str, object]
    manifest: ReadyImageManifest


@dataclass(frozen=True)
class _FileSignature:
    device: int
    inode: int
    size: int
    modified_ns: int
    changed_ns: int


@dataclass(frozen=True)
class _ArchivedFile:
    size: int
    sha256: str


def _file_signature(source: BinaryIO) -> _FileSignature:
    status = os.fstat(source.fileno())
    return _FileSignature(
        device=status.st_dev,
        inode=status.st_ino,
        size=status.st_size,
        modified_ns=status.st_mtime_ns,
        changed_ns=status.st_ctime_ns,
    )


def _sha256_stream(
    source: BinaryIO, *, capture: bool = False
) -> tuple[str, int, bytes]:
    digest = hashlib.sha256()
    size = 0
    captured = bytearray()
    while chunk := source.read(_CHUNK_SIZE):
        digest.update(chunk)
        size += len(chunk)
        if capture:
            if size > MAX_MANIFEST_BYTES:
                raise P9QemuError(
                    "internal release manifest exceeds the supported size limit"
                )
            captured.extend(chunk)
    return digest.hexdigest(), size, bytes(captured)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise P9QemuError(
                f"internal release manifest contains a duplicate field: {key}"
            )
        result[key] = value
    return result


def _load_internal_manifest(content: bytes) -> dict[str, object]:
    try:
        document = json.loads(
            content.decode("utf-8"), object_pairs_hook=_unique_json_object
        )
    except (UnicodeError, json.JSONDecodeError) as error:
        raise P9QemuError(f"internal release manifest is invalid: {error}") from error
    if not isinstance(document, dict):
        raise P9QemuError("internal release manifest must contain a JSON object")
    return document


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise P9QemuError(f"internal release manifest requires an object at {label}")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise P9QemuError(f"internal release manifest requires text at {label}")
    return value


def _positive_int(value: object, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise P9QemuError(
            f"internal release manifest requires a positive integer at {label}"
        )
    return value


def _require_candidate_state(document: Mapping[str, Any]) -> None:
    if (
        type(document.get("schema")) is not int
        or document.get("schema") != 1
        or document.get("kind") != "p9qemu-image-release-candidate"
    ):
        raise P9QemuError("unsupported internal release-candidate manifest")
    if document.get("stage") != "local-only":
        raise P9QemuError("ready-image generation requires a local-only candidate")
    for section_name in ("installation", "preparation", "validation"):
        section = _mapping(document.get(section_name), section_name)
        if section.get("status") != "passed":
            raise P9QemuError(
                f"ready-image generation requires {section_name} status 'passed'"
            )
    hygiene = _mapping(document.get("hygiene"), "hygiene")
    if hygiene.get("image_contents_review_confirmed") is not True:
        raise P9QemuError("ready-image generation requires image hygiene review")
    if hygiene.get("public_text_scan") != "passed":
        raise P9QemuError("ready-image generation requires a passed public text scan")
    publication = _mapping(document.get("publication"), "publication")
    if publication.get("uploaded") is not False:
        raise P9QemuError("ready-image generation requires an unpublished candidate")
    if publication.get("asset_replacement_permitted") is not False:
        raise P9QemuError("release candidates must prohibit asset replacement")


def _verify_internal_inventory(
    document: Mapping[str, Any],
    files: Mapping[str, _ArchivedFile],
) -> tuple[ImageMetadata, str]:
    artifacts = _mapping(document.get("artifacts"), "artifacts")
    expected_paths = {"manifest.json"}
    for label, value in artifacts.items():
        record = _mapping(value, f"artifacts.{label}")
        relative_text = _text(record.get("path"), f"artifacts.{label}.path")
        relative = portable_relative_path(
            relative_text, f"internal artifact path at artifacts.{label}.path"
        )
        normalized = relative.as_posix()
        if normalized in expected_paths:
            raise P9QemuError(
                f"internal release manifest repeats an artifact path: {normalized}"
            )
        expected_paths.add(normalized)
        archived = files.get(normalized)
        if archived is None:
            raise P9QemuError(f"release archive is missing artifact: {normalized}")
        expected_size = record.get("size")
        if type(expected_size) is not int or expected_size != archived.size:
            raise P9QemuError(f"release artifact size mismatch: {normalized}")
        if record.get("sha256") != archived.sha256:
            raise P9QemuError(f"release artifact checksum mismatch: {normalized}")
    actual_paths = set(files)
    if actual_paths != expected_paths:
        extras = sorted(actual_paths - expected_paths)
        missing = sorted(expected_paths - actual_paths)
        raise P9QemuError(
            f"release archive file inventory mismatch; extras={extras}, missing={missing}"
        )

    identity = _mapping(document.get("identity"), "identity")
    _text(identity.get("bundle_name"), "identity.bundle_name")
    source = _mapping(document.get("source"), "source")
    runtime_profile = _text(source.get("runtime_profile"), "source.runtime_profile")
    image_document = _mapping(document.get("image"), "image")
    image_path = portable_relative_path(
        _text(image_document.get("path"), "image.path"), "internal image path"
    ).as_posix()
    image_format = _text(image_document.get("format"), "image.format")
    if image_format != "qcow2" or not image_path.endswith(".qcow2"):
        raise P9QemuError("internal release image must be QCOW2")
    archived_image = files.get(image_path)
    if archived_image is None:
        raise P9QemuError("internal release image is missing from the archive")
    stored_size = _positive_int(image_document.get("stored_size"), "image.stored_size")
    virtual_size = _positive_int(
        image_document.get("virtual_size"), "image.virtual_size"
    )
    image_sha256 = _text(image_document.get("sha256"), "image.sha256")
    if stored_size != archived_image.size:
        raise P9QemuError("internal image stored size does not match the archive")
    if image_sha256 != archived_image.sha256:
        raise P9QemuError("internal image checksum does not match the archive")
    if virtual_size < stored_size:
        raise P9QemuError("internal image virtual size is smaller than its stored size")
    qcow2_paths = sorted(path for path in files if path.endswith(".qcow2"))
    if qcow2_paths != [image_path]:
        raise P9QemuError(
            f"release archive must contain exactly one declared QCOW2: {qcow2_paths}"
        )
    return (
        ImageMetadata(
            path=image_path,
            format=image_format,
            stored_size=stored_size,
            virtual_size=virtual_size,
            sha256=image_sha256,
        ),
        runtime_profile,
    )


def inspect_ready_image_archive(archive: Path) -> ReadyImageArchiveInspection:
    """Stream and verify one candidate archive without extracting its QCOW2."""

    archive_name = portable_relative_path(archive.name, "release archive filename")
    if len(archive_name.parts) != 1 or not archive.name.endswith(".tar.gz"):
        raise P9QemuError("ready-image archive must be one .tar.gz file")
    if archive.is_symlink() or not archive.is_file():
        raise P9QemuError(f"ready-image archive is not a regular file: {archive}")
    try:
        with archive.open("rb") as raw:
            before = _file_signature(raw)
            if before.size <= 0 or before.size > MAX_ARCHIVE_BYTES:
                raise P9QemuError(
                    "ready-image archive size is outside the supported limits"
                )
            archive_sha256, archive_size, _captured = _sha256_stream(raw)
            if archive_size != before.size:
                raise P9QemuError("ready-image archive changed while it was hashed")
            raw.seek(0)
            with tarfile.open(fileobj=raw, mode="r:gz") as tar:
                inventory = inspect_release_archive_headers(tar)
                files: dict[str, _ArchivedFile] = {}
                manifest_content: bytes | None = None
                for member, path in inventory.members:
                    if not member.isfile():
                        continue
                    relative = path.relative_to(inventory.bundle_name).as_posix()
                    source = tar.extractfile(member)
                    if source is None:
                        raise P9QemuError(
                            f"could not read release archive member: {path.as_posix()}"
                        )
                    with source:
                        digest, size, captured = _sha256_stream(
                            source, capture=relative == "manifest.json"
                        )
                    if size != member.size:
                        raise P9QemuError(
                            f"release archive member size changed while reading: {relative}"
                        )
                    files[relative] = _ArchivedFile(size=size, sha256=digest)
                    if relative == "manifest.json":
                        manifest_content = captured
            after = _file_signature(raw)
    except P9QemuError:
        raise
    except (OSError, tarfile.TarError) as error:
        raise P9QemuError(f"could not inspect ready-image archive: {error}") from error

    if before != after:
        raise P9QemuError("ready-image archive changed during inspection")
    if manifest_content is None:
        raise P9QemuError("release archive does not contain manifest.json")
    internal = _load_internal_manifest(manifest_content)
    _require_candidate_state(internal)
    identity = _mapping(internal.get("identity"), "identity")
    if identity.get("bundle_name") != inventory.bundle_name:
        raise P9QemuError("internal bundle identity does not match the archive root")
    image, runtime_profile = _verify_internal_inventory(internal, files)
    manifest_file = files["manifest.json"]
    return ReadyImageArchiveInspection(
        bundle_name=inventory.bundle_name,
        archive_size=archive_size,
        archive_sha256=archive_sha256,
        member_count=inventory.member_count,
        file_count=inventory.file_count,
        extracted_size=inventory.extracted_size,
        manifest_sha256=manifest_file.sha256,
        image=image,
        runtime_profile=runtime_profile,
    )


def build_ready_image_manifest(
    inputs: ReadyImageManifestInputs,
) -> ReadyImageManifestBuild:
    """Derive and strictly validate a deterministic external image manifest."""

    inspection = inspect_ready_image_archive(inputs.archive)
    document: dict[str, object] = {
        "schema": IMAGE_MANIFEST_SCHEMA,
        "kind": IMAGE_MANIFEST_KIND,
        "id": inspection.bundle_name,
        "title": inputs.title,
        "variant": inputs.variant,
        "guest": {
            "distribution": inputs.distribution,
            "release": inputs.release,
            "architecture": inputs.architecture,
        },
        "artifact": {
            "packaging": SUPPORTED_PACKAGING,
            "url": inputs.asset_url,
            "filename": inputs.archive.name,
            "size": inspection.archive_size,
            "sha256": inspection.archive_sha256,
        },
        "bundle": {
            "root": inspection.bundle_name,
            "manifest_path": "manifest.json",
            "manifest_sha256": inspection.manifest_sha256,
            "member_count": inspection.member_count,
            "file_count": inspection.file_count,
            "extracted_size": inspection.extracted_size,
        },
        "image": asdict(inspection.image),
        "runtime": {
            "profile": inspection.runtime_profile,
            "capabilities": list(inputs.capabilities),
        },
    }
    manifest = parse_ready_image_manifest(document)
    return ReadyImageManifestBuild(
        inputs=inputs,
        inspection=inspection,
        document=document,
        manifest=manifest,
    )


def write_ready_image_manifest(
    inputs: ReadyImageManifestInputs,
) -> ReadyImageManifestBuild:
    """Verify an archive and atomically create its canonical external manifest."""

    if inputs.output.exists():
        raise P9QemuError(f"refusing to replace ready-image manifest: {inputs.output}")
    if not inputs.output.parent.is_dir():
        raise P9QemuError(
            f"ready-image manifest parent directory does not exist: {inputs.output.parent}"
        )
    result = build_ready_image_manifest(inputs)
    write_json_new(inputs.output, result.document)
    return result
