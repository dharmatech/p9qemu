from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import stat
import tarfile

import pytest

from p9qemu.errors import P9QemuError
from p9qemu.media import sha256_file
from p9qemu.ready_image import (
    install_local_ready_image,
    load_ready_image_manifest,
    parse_ready_image_manifest,
)
from p9qemu.release_candidate import create_deterministic_tar_gz


ROOT = Path(__file__).parents[1]
EXAMPLE_MANIFEST = (
    ROOT
    / "images"
    / "manifests"
    / "p9qemu-9front-11554-amd64-hjfs-gmt-002.example.json"
)


def _write_json(path: Path, document: object) -> None:
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _ready_image_fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, object]]:
    image_id = "p9qemu-test-stock-001"
    bundle = tmp_path / "source" / image_id
    bundle.mkdir(parents=True)
    image_name = f"{image_id}.qcow2"
    image = bundle / image_name
    image.write_bytes(b"synthetic qcow2 fixture\0" * 64)
    image_sha256 = sha256_file(image)
    internal = {
        "schema": 1,
        "kind": "p9qemu-image-release-candidate",
        "identity": {
            "image_id": "test-stock",
            "build_id": "001",
            "bundle_name": image_id,
        },
        "source": {"runtime_profile": "test-graphical-serial-v1"},
        "image": {
            "path": image_name,
            "format": "qcow2",
            "stored_size": image.stat().st_size,
            "virtual_size": 1024 * 1024,
            "sha256": image_sha256,
        },
        "artifacts": {
            "image": {
                "path": image_name,
                "size": image.stat().st_size,
                "sha256": image_sha256,
            }
        },
    }
    internal_manifest = bundle / "manifest.json"
    _write_json(internal_manifest, internal)

    archive = tmp_path / f"{image_id}.tar.gz"
    create_deterministic_tar_gz(bundle, archive)
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
    file_members = [member for member in members if member.isfile()]
    external = {
        "schema": 1,
        "kind": "p9qemu-ready-image",
        "id": image_id,
        "title": "Synthetic test ready image",
        "variant": "stock",
        "guest": {
            "distribution": "9front",
            "release": "test",
            "architecture": "amd64",
        },
        "artifact": {
            "packaging": "tar-gzip",
            "url": f"https://example.invalid/{archive.name}",
            "filename": archive.name,
            "size": archive.stat().st_size,
            "sha256": sha256_file(archive),
        },
        "bundle": {
            "root": image_id,
            "manifest_path": "manifest.json",
            "manifest_sha256": sha256_file(internal_manifest),
            "member_count": len(members),
            "file_count": len(file_members),
            "extracted_size": sum(member.size for member in file_members),
        },
        "image": internal["image"],
        "runtime": {
            "profile": "test-graphical-serial-v1",
            "capabilities": ["graphical-console", "serial-console"],
        },
    }
    manifest = tmp_path / "image.json"
    _write_json(manifest, external)
    return manifest, archive, external


def test_candidate_002_example_manifest_is_valid() -> None:
    manifest = load_ready_image_manifest(EXAMPLE_MANIFEST)

    assert manifest.image_id == "p9qemu-9front-11554-amd64-hjfs-gmt-002"
    assert manifest.variant == "stock"
    assert manifest.artifact.packaging == "tar-gzip"
    assert manifest.bundle.member_count == 21
    assert manifest.bundle.file_count == 17
    assert manifest.image.sha256 == (
        "1ef80c81a3f2dd09d2f173ff7dfa93d07ecee2ba453fc0f0964190adb6ee44a8"
    )


def test_manifest_loader_rejects_duplicate_json_fields(tmp_path: Path) -> None:
    manifest = tmp_path / "image.json"
    manifest.write_text('{"schema": 1, "schema": 1}\n', encoding="utf-8")

    with pytest.raises(P9QemuError, match="duplicate field"):
        load_ready_image_manifest(manifest)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (("unknown", True), "fields differ"),
        (("id", "latest"), "immutable identifier"),
        (("artifact.url", "http://example.invalid/image.tar.gz"), "HTTPS URL"),
        (("artifact.filename", "folder\\image.tar.gz"), "portable path"),
        (("image.path", "../image.qcow2"), "portable path"),
        (("image.path", "CON.qcow2"), "portable path"),
        (("runtime.capabilities", ["serial-console", "serial-console"]), "duplicate"),
        (("schema", True), "unsupported"),
    ],
)
def test_manifest_parser_rejects_unsafe_or_ambiguous_fields(
    tmp_path: Path, mutation: tuple[str, object], message: str
) -> None:
    _manifest, _archive, source = _ready_image_fixture(tmp_path)
    document = deepcopy(source)
    dotted_key, value = mutation
    if "." not in dotted_key:
        document[dotted_key] = value
    else:
        parent, key = dotted_key.split(".")
        document[parent][key] = value  # type: ignore[index]

    with pytest.raises(P9QemuError, match=message):
        parse_ready_image_manifest(document)


def test_local_archive_is_verified_and_atomically_cached(tmp_path: Path) -> None:
    manifest_path, archive, _document = _ready_image_fixture(tmp_path)
    cache = tmp_path / "cache"
    messages: list[str] = []

    cached = install_local_ready_image(
        manifest_path, archive, cache, progress=messages.append
    )

    assert cached.entry == cache / "images" / cached.manifest.image.sha256
    assert cached.image.read_bytes().startswith(b"synthetic qcow2 fixture")
    assert not cached.image.stat().st_mode & (
        stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH
    )
    assert messages == [
        f"Verifying and caching ready image: {cached.manifest.image_id}"
    ]
    again = install_local_ready_image(
        manifest_path, archive, cache, progress=messages.append
    )
    assert again == cached
    assert messages[-1] == f"Using cached ready image: {cached.entry}"

    cached.image.chmod(stat.S_IREAD | stat.S_IWRITE)


def test_archive_checksum_failure_leaves_no_partial_cache(tmp_path: Path) -> None:
    manifest_path, archive, document = _ready_image_fixture(tmp_path)
    document["artifact"]["sha256"] = "0" * 64  # type: ignore[index]
    _write_json(manifest_path, document)
    cache = tmp_path / "cache"

    with pytest.raises(P9QemuError, match="archive checksum"):
        install_local_ready_image(
            manifest_path, archive, cache, progress=lambda _: None
        )

    assert list((cache / "images").iterdir()) == []


def test_archive_inventory_mismatch_leaves_no_partial_cache(tmp_path: Path) -> None:
    manifest_path, archive, document = _ready_image_fixture(tmp_path)
    document["bundle"]["file_count"] = 1  # type: ignore[index]
    _write_json(manifest_path, document)
    cache = tmp_path / "cache"

    with pytest.raises(P9QemuError, match="file count"):
        install_local_ready_image(
            manifest_path, archive, cache, progress=lambda _: None
        )

    assert list((cache / "images").iterdir()) == []


def test_internal_metadata_mismatch_leaves_no_partial_cache(tmp_path: Path) -> None:
    manifest_path, archive, document = _ready_image_fixture(tmp_path)
    document["runtime"]["profile"] = "other-runtime-v1"  # type: ignore[index]
    _write_json(manifest_path, document)
    cache = tmp_path / "cache"

    with pytest.raises(P9QemuError, match="internal runtime profile"):
        install_local_ready_image(
            manifest_path, archive, cache, progress=lambda _: None
        )

    assert list((cache / "images").iterdir()) == []


def test_cached_base_must_remain_read_only(tmp_path: Path) -> None:
    manifest_path, archive, _document = _ready_image_fixture(tmp_path)
    cache = tmp_path / "cache"
    cached = install_local_ready_image(
        manifest_path, archive, cache, progress=lambda _: None
    )
    cached.image.chmod(stat.S_IREAD | stat.S_IWRITE)

    with pytest.raises(P9QemuError, match="immutable image is writable"):
        install_local_ready_image(
            manifest_path, archive, cache, progress=lambda _: None
        )
