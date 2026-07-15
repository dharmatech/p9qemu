"""Strict ready-image manifests and local immutable-cache installation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
from typing import Any
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from p9qemu.errors import P9QemuError
from p9qemu.media import sha256_file
from p9qemu.portable_path import portable_relative_path
from p9qemu.release_candidate import (
    extract_release_archive,
    load_json_object,
    verify_extracted_bundle,
)


Progress = Callable[[str], None]

IMAGE_MANIFEST_SCHEMA = 1
IMAGE_MANIFEST_KIND = "p9qemu-ready-image"
SUPPORTED_PACKAGING = "tar-gzip"
MAX_ARCHIVE_BYTES = 8 * 1024**3
MAX_EXTRACTED_BYTES = 64 * 1024**3
MAX_ARCHIVE_MEMBERS = 4096
MAX_MANIFEST_BYTES = 64 * 1024

_IDENTIFIER = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class GuestMetadata:
    distribution: str
    release: str
    architecture: str


@dataclass(frozen=True)
class ArchiveArtifact:
    packaging: str
    url: str
    filename: str
    size: int
    sha256: str


@dataclass(frozen=True)
class BundleMetadata:
    root: str
    manifest_path: str
    manifest_sha256: str
    member_count: int
    file_count: int
    extracted_size: int


@dataclass(frozen=True)
class ImageMetadata:
    path: str
    format: str
    stored_size: int
    virtual_size: int
    sha256: str


@dataclass(frozen=True)
class RuntimeMetadata:
    profile: str
    capabilities: tuple[str, ...]


@dataclass(frozen=True)
class ReadyImageManifest:
    schema: int
    kind: str
    image_id: str
    title: str
    variant: str
    guest: GuestMetadata
    artifact: ArchiveArtifact
    bundle: BundleMetadata
    image: ImageMetadata
    runtime: RuntimeMetadata


@dataclass(frozen=True)
class CachedReadyImage:
    manifest: ReadyImageManifest
    entry: Path
    bundle: Path
    image: Path


def _object(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise P9QemuError(f"image manifest requires an object at {label}")
    return value


def _exact_keys(document: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(document)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise P9QemuError(
            f"image manifest fields differ at {label}; "
            f"missing={missing}, unknown={unknown}"
        )


def _text(value: object, label: str, *, maximum: int = 200) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise P9QemuError(f"image manifest requires non-empty text at {label}")
    if any(ord(character) < 32 for character in value):
        raise P9QemuError(
            f"image manifest text contains a control character at {label}"
        )
    return value


def _identifier(value: object, label: str) -> str:
    text = _text(value, label, maximum=100)
    if not _IDENTIFIER.fullmatch(text) or text == "latest":
        raise P9QemuError(
            f"image manifest requires an immutable identifier at {label}: {text!r}"
        )
    return text


def _positive_int(value: object, label: str, *, maximum: int | None = None) -> int:
    if type(value) is not int or value <= 0:
        raise P9QemuError(f"image manifest requires a positive integer at {label}")
    if maximum is not None and value > maximum:
        raise P9QemuError(
            f"image manifest value exceeds the supported limit at {label}"
        )
    return value


def _sha256(value: object, label: str) -> str:
    text = _text(value, label, maximum=64)
    if not _SHA256.fullmatch(text):
        raise P9QemuError(
            f"image manifest requires 64 lowercase hexadecimal characters at {label}"
        )
    return text


def _https_url(value: object, label: str, *, filename: str) -> str:
    text = _text(value, label, maximum=2000)
    if "\\" in text:
        raise P9QemuError(f"image manifest URL is invalid at {label}")
    try:
        parsed = urlsplit(text)
        parsed.port
    except ValueError as error:
        raise P9QemuError(
            f"image manifest URL is invalid at {label}: {error}"
        ) from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise P9QemuError(
            f"image manifest requires an HTTPS URL without credentials or a "
            f"fragment at {label}"
        )
    if unquote(PurePosixPath(parsed.path).name) != filename:
        raise P9QemuError(
            "image manifest artifact filename does not match its URL path"
        )
    return text


def parse_ready_image_manifest(document: Mapping[str, Any]) -> ReadyImageManifest:
    """Strictly parse the first external ready-image manifest schema."""

    _exact_keys(
        document,
        {
            "schema",
            "kind",
            "id",
            "title",
            "variant",
            "guest",
            "artifact",
            "bundle",
            "image",
            "runtime",
        },
        "root",
    )
    if (
        type(document.get("schema")) is not int
        or document.get("schema") != IMAGE_MANIFEST_SCHEMA
    ):
        raise P9QemuError("unsupported ready-image manifest schema")
    if document.get("kind") != IMAGE_MANIFEST_KIND:
        raise P9QemuError("unsupported ready-image manifest kind")

    image_id = _identifier(document.get("id"), "id")
    title = _text(document.get("title"), "title")
    variant = _identifier(document.get("variant"), "variant")

    guest_document = _object(document.get("guest"), "guest")
    _exact_keys(guest_document, {"distribution", "release", "architecture"}, "guest")
    guest = GuestMetadata(
        distribution=_identifier(
            guest_document.get("distribution"), "guest.distribution"
        ),
        release=_identifier(guest_document.get("release"), "guest.release"),
        architecture=_identifier(
            guest_document.get("architecture"), "guest.architecture"
        ),
    )

    artifact_document = _object(document.get("artifact"), "artifact")
    _exact_keys(
        artifact_document,
        {"packaging", "url", "filename", "size", "sha256"},
        "artifact",
    )
    packaging = _text(artifact_document.get("packaging"), "artifact.packaging")
    if packaging != SUPPORTED_PACKAGING:
        raise P9QemuError(f"unsupported ready-image packaging: {packaging!r}")
    filename = _text(artifact_document.get("filename"), "artifact.filename")
    filename_path = portable_relative_path(filename, "artifact.filename")
    if len(filename_path.parts) != 1 or not filename.endswith(".tar.gz"):
        raise P9QemuError("ready-image artifact must be one .tar.gz filename")
    artifact = ArchiveArtifact(
        packaging=packaging,
        url=_https_url(artifact_document.get("url"), "artifact.url", filename=filename),
        filename=filename,
        size=_positive_int(
            artifact_document.get("size"),
            "artifact.size",
            maximum=MAX_ARCHIVE_BYTES,
        ),
        sha256=_sha256(artifact_document.get("sha256"), "artifact.sha256"),
    )

    bundle_document = _object(document.get("bundle"), "bundle")
    _exact_keys(
        bundle_document,
        {
            "root",
            "manifest_path",
            "manifest_sha256",
            "member_count",
            "file_count",
            "extracted_size",
        },
        "bundle",
    )
    root = _identifier(bundle_document.get("root"), "bundle.root")
    if root != image_id:
        raise P9QemuError("ready-image ID must equal the archive bundle root")
    manifest_path = _text(bundle_document.get("manifest_path"), "bundle.manifest_path")
    portable_relative_path(manifest_path, "bundle.manifest_path")
    if manifest_path != "manifest.json":
        raise P9QemuError("schema 1 requires bundle.manifest_path='manifest.json'")
    member_count = _positive_int(
        bundle_document.get("member_count"),
        "bundle.member_count",
        maximum=MAX_ARCHIVE_MEMBERS,
    )
    file_count = _positive_int(
        bundle_document.get("file_count"),
        "bundle.file_count",
        maximum=MAX_ARCHIVE_MEMBERS,
    )
    if file_count > member_count:
        raise P9QemuError("bundle.file_count cannot exceed bundle.member_count")
    bundle = BundleMetadata(
        root=root,
        manifest_path=manifest_path,
        manifest_sha256=_sha256(
            bundle_document.get("manifest_sha256"), "bundle.manifest_sha256"
        ),
        member_count=member_count,
        file_count=file_count,
        extracted_size=_positive_int(
            bundle_document.get("extracted_size"),
            "bundle.extracted_size",
            maximum=MAX_EXTRACTED_BYTES,
        ),
    )

    image_document = _object(document.get("image"), "image")
    _exact_keys(
        image_document,
        {"path", "format", "stored_size", "virtual_size", "sha256"},
        "image",
    )
    image_path = _text(image_document.get("path"), "image.path")
    portable_relative_path(image_path, "image.path")
    image_format = _text(image_document.get("format"), "image.format")
    if image_format != "qcow2" or not image_path.endswith(".qcow2"):
        raise P9QemuError("schema 1 ready images must contain a QCOW2 image")
    stored_size = _positive_int(image_document.get("stored_size"), "image.stored_size")
    virtual_size = _positive_int(
        image_document.get("virtual_size"), "image.virtual_size"
    )
    if stored_size > bundle.extracted_size:
        raise P9QemuError("image.stored_size cannot exceed bundle.extracted_size")
    if virtual_size < stored_size:
        raise P9QemuError("image.virtual_size cannot be smaller than image.stored_size")
    image = ImageMetadata(
        path=image_path,
        format=image_format,
        stored_size=stored_size,
        virtual_size=virtual_size,
        sha256=_sha256(image_document.get("sha256"), "image.sha256"),
    )

    runtime_document = _object(document.get("runtime"), "runtime")
    _exact_keys(runtime_document, {"profile", "capabilities"}, "runtime")
    capabilities_document = runtime_document.get("capabilities")
    if not isinstance(capabilities_document, list) or not capabilities_document:
        raise P9QemuError(
            "image manifest requires a non-empty runtime.capabilities list"
        )
    capabilities = tuple(
        _identifier(value, f"runtime.capabilities[{index}]")
        for index, value in enumerate(capabilities_document)
    )
    if len(capabilities) != len(set(capabilities)):
        raise P9QemuError("runtime.capabilities contains a duplicate value")
    runtime = RuntimeMetadata(
        profile=_identifier(runtime_document.get("profile"), "runtime.profile"),
        capabilities=capabilities,
    )

    return ReadyImageManifest(
        schema=IMAGE_MANIFEST_SCHEMA,
        kind=IMAGE_MANIFEST_KIND,
        image_id=image_id,
        title=title,
        variant=variant,
        guest=guest,
        artifact=artifact,
        bundle=bundle,
        image=image,
        runtime=runtime,
    )


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise P9QemuError(f"image manifest contains a duplicate field: {key}")
        result[key] = value
    return result


def parse_ready_image_manifest_bytes(
    content: bytes, *, source: str
) -> ReadyImageManifest:
    """Strictly parse bounded UTF-8 manifest bytes from one named source."""

    if len(content) > MAX_MANIFEST_BYTES:
        raise P9QemuError(
            f"image manifest exceeds the supported size limit: {source}"
        )
    try:
        document = json.loads(
            content.decode("utf-8"), object_pairs_hook=_unique_json_object
        )
    except (UnicodeError, json.JSONDecodeError) as error:
        raise P9QemuError(f"could not parse image manifest {source}: {error}") from error
    if not isinstance(document, dict):
        raise P9QemuError(f"image manifest must contain a JSON object: {source}")
    return parse_ready_image_manifest(document)


def _load_ready_image_manifest(
    path: Path,
) -> tuple[ReadyImageManifest, bytes]:
    try:
        content = path.read_bytes()
    except OSError as error:
        raise P9QemuError(f"could not read image manifest {path}: {error}") from error
    return parse_ready_image_manifest_bytes(content, source=str(path)), content


def load_ready_image_manifest(path: Path) -> ReadyImageManifest:
    """Load and strictly validate one UTF-8 external image manifest."""

    return _load_ready_image_manifest(path)[0]


def _cache_paths(
    cache_dir: Path, manifest: ReadyImageManifest
) -> tuple[Path, Path, Path]:
    entry = cache_dir / "images" / manifest.image.sha256
    bundle = entry / "bundle" / manifest.bundle.root
    image_relative = portable_relative_path(manifest.image.path, "image.path")
    return entry, bundle, bundle.joinpath(*image_relative.parts)


def _is_read_only(path: Path) -> bool:
    write_bits = stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH
    return not bool(path.stat().st_mode & write_bits)


def _verify_bundle_against_external(bundle: Path, manifest: ReadyImageManifest) -> Path:
    verification = verify_extracted_bundle(bundle)
    if verification["manifest_sha256"] != manifest.bundle.manifest_sha256:
        raise P9QemuError("internal bundle manifest checksum does not match image.json")
    if verification["image_sha256"] != manifest.image.sha256:
        raise P9QemuError("bundle image checksum does not match image.json")

    internal = load_json_object(
        bundle / manifest.bundle.manifest_path, "release manifest"
    )
    identity = _object(internal.get("identity"), "internal identity")
    if identity.get("bundle_name") != manifest.bundle.root:
        raise P9QemuError("internal bundle identity does not match image.json")
    source = _object(internal.get("source"), "internal source")
    if source.get("runtime_profile") != manifest.runtime.profile:
        raise P9QemuError("internal runtime profile does not match image.json")
    image = _object(internal.get("image"), "internal image")
    expected = {
        "path": manifest.image.path,
        "format": manifest.image.format,
        "stored_size": manifest.image.stored_size,
        "virtual_size": manifest.image.virtual_size,
        "sha256": manifest.image.sha256,
    }
    if any(image.get(key) != value for key, value in expected.items()):
        raise P9QemuError("internal image metadata does not match image.json")
    image_relative = portable_relative_path(manifest.image.path, "image.path")
    image_path = bundle.joinpath(*image_relative.parts)
    if image_path.stat().st_size != manifest.image.stored_size:
        raise P9QemuError("extracted image size does not match image.json")
    return image_path


def _verify_cached_entry(entry: Path, manifest: ReadyImageManifest) -> CachedReadyImage:
    if not entry.is_dir():
        raise P9QemuError(f"ready-image cache entry is not a directory: {entry}")
    cached_manifest = load_ready_image_manifest(entry / "image.json")
    if cached_manifest != manifest:
        raise P9QemuError("cached ready-image identity does not match image.json")
    bundle = entry / "bundle" / manifest.bundle.root
    image = _verify_bundle_against_external(bundle, manifest)
    if not _is_read_only(image):
        raise P9QemuError(f"cached immutable image is writable: {image}")
    return CachedReadyImage(manifest, entry, bundle, image)


def load_cached_ready_image(entry: Path) -> CachedReadyImage:
    """Load and fully reverify one content-addressed ready-image cache entry."""

    manifest = load_ready_image_manifest(entry / "image.json")
    return _verify_cached_entry(entry, manifest)


def verify_cached_ready_image(cached: CachedReadyImage) -> CachedReadyImage:
    """Reverify a cached-image handle and reject mismatched stored paths."""

    verified = _verify_cached_entry(cached.entry, cached.manifest)
    if (
        verified.bundle.resolve() != cached.bundle.resolve()
        or verified.image.resolve() != cached.image.resolve()
    ):
        raise P9QemuError("cached ready-image handle does not match its cache entry")
    return verified


def _prepare_cache_parent(cache_dir: Path) -> Path:
    if cache_dir.exists() and not cache_dir.is_dir():
        raise P9QemuError(f"ready-image cache path is not a directory: {cache_dir}")
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        images = cache_dir / "images"
        images.mkdir(exist_ok=True)
    except OSError as error:
        raise P9QemuError(f"could not create ready-image cache: {error}") from error
    if not images.is_dir():
        raise P9QemuError(f"ready-image cache path is not a directory: {images}")
    return images


def _remove_tree_best_effort(path: Path) -> None:
    def make_writable_and_retry(
        function: Callable[[str], object], filename: str, _error: object
    ) -> None:
        os.chmod(filename, stat.S_IWRITE)
        function(filename)

    try:
        shutil.rmtree(path, onerror=make_writable_and_retry)
    except OSError:
        pass


def install_local_ready_image(
    manifest_path: Path,
    archive: Path,
    cache_dir: Path,
    *,
    progress: Progress,
) -> CachedReadyImage:
    """Verify a local bundle and atomically cache its immutable QCOW2 base."""

    manifest, manifest_bytes = _load_ready_image_manifest(manifest_path)
    images = _prepare_cache_parent(cache_dir)
    entry, _bundle, _image = _cache_paths(cache_dir, manifest)
    if entry.exists():
        progress(f"Using cached ready image: {entry}")
        return _verify_cached_entry(entry, manifest)
    if not archive.is_file():
        raise P9QemuError(f"ready-image archive is not a file: {archive}")
    if archive.name != manifest.artifact.filename:
        raise P9QemuError("local archive filename does not match image.json")
    if archive.stat().st_size != manifest.artifact.size:
        raise P9QemuError("local archive size does not match image.json")
    if sha256_file(archive) != manifest.artifact.sha256:
        raise P9QemuError("local archive checksum does not match image.json")
    if shutil.disk_usage(images).free < manifest.bundle.extracted_size:
        raise P9QemuError("not enough free space to extract the ready image")

    temporary = images / f".{manifest.image.sha256}.p9qemu-{uuid4().hex}.part"
    progress(f"Verifying and caching ready image: {manifest.image_id}")
    try:
        temporary.mkdir()
        bundle = extract_release_archive(
            archive,
            temporary / "bundle",
            manifest.bundle.root,
            expected_member_count=manifest.bundle.member_count,
            expected_file_count=manifest.bundle.file_count,
            expected_extracted_size=manifest.bundle.extracted_size,
        )
        image = _verify_bundle_against_external(bundle, manifest)
        with (temporary / "image.json").open("xb") as output:
            output.write(manifest_bytes)
        image.chmod(
            image.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
        )
        try:
            os.replace(temporary, entry)
        except OSError as error:
            if entry.is_dir():
                return _verify_cached_entry(entry, manifest)
            raise P9QemuError(
                f"could not publish ready-image cache entry: {error}"
            ) from error
    except P9QemuError:
        raise
    except OSError as error:
        raise P9QemuError(f"could not cache ready image: {error}") from error
    finally:
        _remove_tree_best_effort(temporary)
    return _verify_cached_entry(entry, manifest)
