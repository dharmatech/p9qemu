from __future__ import annotations

import gzip
import hashlib
from io import BytesIO
from pathlib import Path
from urllib.error import URLError

import pytest

from p9qemu.errors import P9QemuError
from p9qemu.media import MediaSpec, inspect_media, prepare_media


def spec_for(payload: bytes) -> tuple[MediaSpec, bytes]:
    archive = gzip.compress(payload)
    return (
        MediaSpec(
            url="https://example.invalid/9front.iso.gz",
            archive_name="9front.iso.gz",
            iso_name="9front.iso",
            archive_sha256=hashlib.sha256(archive).hexdigest(),
        ),
        archive,
    )


def test_cached_iso_is_reused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec, _archive = spec_for(b"iso data")
    iso = tmp_path / spec.iso_name
    iso.write_bytes(b"iso data")
    monkeypatch.setattr(
        "p9qemu.media.urlopen",
        lambda _request, **_kwargs: pytest.fail(
            "network must not be used for a cache hit"
        ),
    )
    messages: list[str] = []
    result = prepare_media(tmp_path, spec, progress=messages.append)
    assert result.iso == iso
    assert iso.read_bytes() == b"iso data"
    assert messages == [f"Using cached installation ISO: {iso}"]


def test_download_is_verified_and_decompressed_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"synthetic ISO contents"
    spec, archive = spec_for(payload)
    monkeypatch.setattr(
        "p9qemu.media.urlopen", lambda _request, **_kwargs: BytesIO(archive)
    )
    result = prepare_media(tmp_path, spec, progress=lambda _message: None)
    assert result.archive.read_bytes() == archive
    assert result.iso.read_bytes() == payload
    assert list(tmp_path.glob("*.part")) == []


def test_checksum_mismatch_removes_partial_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec, archive = spec_for(b"good")
    bad = archive + b"bad"
    monkeypatch.setattr(
        "p9qemu.media.urlopen", lambda _request, **_kwargs: BytesIO(bad)
    )
    with pytest.raises(P9QemuError, match="checksum mismatch"):
        prepare_media(tmp_path, spec, progress=lambda _message: None)
    assert not (tmp_path / spec.archive_name).exists()
    assert not (tmp_path / f"{spec.archive_name}.part").exists()


class InterruptedResponse(BytesIO):
    def read(self, size: int = -1) -> bytes:
        if self.tell() > 0:
            raise URLError("interrupted")
        return super().read(2 if size != -1 else size)


def test_interrupted_download_is_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec, archive = spec_for(b"payload")
    monkeypatch.setattr(
        "p9qemu.media.urlopen",
        lambda _request, **_kwargs: InterruptedResponse(archive),
    )
    with pytest.raises(P9QemuError, match="could not download"):
        prepare_media(tmp_path, spec, progress=lambda _message: None)
    assert not (tmp_path / spec.archive_name).exists()
    assert not (tmp_path / f"{spec.archive_name}.part").exists()


def test_cached_archive_is_verified_before_decompression(tmp_path: Path) -> None:
    payload = b"payload"
    spec, archive = spec_for(payload)
    (tmp_path / spec.archive_name).write_bytes(archive)
    result = prepare_media(tmp_path, spec, progress=lambda _message: None)
    assert result.iso.read_bytes() == payload


def test_dry_run_does_not_create_cache(tmp_path: Path) -> None:
    cache = tmp_path / "missing-cache"
    spec, _archive = spec_for(b"payload")
    messages: list[str] = []
    paths = inspect_media(cache, spec, progress=messages.append)
    assert not cache.exists()
    assert paths.iso == cache / spec.iso_name
    assert messages[0].startswith("Would download ")
    assert messages[1].startswith("Would unpack ")


def test_dry_run_rejects_cache_path_that_is_a_file(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.write_text("not a directory", encoding="utf-8")
    spec, _archive = spec_for(b"payload")
    with pytest.raises(P9QemuError, match="cache path is not a directory"):
        inspect_media(cache, spec, progress=lambda _message: None)
