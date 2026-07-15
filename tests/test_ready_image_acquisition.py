from __future__ import annotations

from collections.abc import Mapping
import hashlib
import io
import json
from pathlib import Path

import pytest

from p9qemu.errors import P9QemuError
from p9qemu.ready_image import MAX_MANIFEST_BYTES, parse_ready_image_manifest
from p9qemu.ready_image_acquisition import (
    HTTPResponse,
    acquire_ready_image,
    acquire_ready_image_archive,
    fetch_ready_image_manifest,
)


EXAMPLE_MANIFEST = (
    Path(__file__).parents[1]
    / "images"
    / "manifests"
    / "p9qemu-9front-11554-amd64-hjfs-gmt-002.example.json"
)


class FakeResponse:
    def __init__(
        self,
        payload: bytes,
        *,
        status: int = 200,
        headers: Mapping[str, str] | None = None,
        url: str = "https://objects.example/download",
        fail_after: int | None = None,
    ) -> None:
        self.status = status
        self.headers = dict(headers or {})
        self.url = url
        self._source = io.BytesIO(payload)
        self._fail_after = fail_after
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        if self._fail_after is not None:
            position = self._source.tell()
            if position >= self._fail_after:
                raise OSError("synthetic interrupted connection")
            if size < 0:
                size = self._fail_after - position
            else:
                size = min(size, self._fail_after - position)
        return self._source.read(size)

    def close(self) -> None:
        self.closed = True


class FakeTransport:
    def __init__(self, *responses: HTTPResponse) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, dict[str, str], int]] = []

    def open(
        self, url: str, *, headers: Mapping[str, str], timeout: int
    ) -> HTTPResponse:
        self.requests.append((url, dict(headers), timeout))
        if not self.responses:
            raise AssertionError("unexpected HTTPS request")
        return self.responses.pop(0)


def _manifest_document(payload: bytes) -> dict[str, object]:
    document = json.loads(EXAMPLE_MANIFEST.read_text(encoding="utf-8"))
    artifact = document["artifact"]
    assert isinstance(artifact, dict)
    artifact.update(
        {
            "url": "https://downloads.example/ready.tar.gz",
            "filename": "ready.tar.gz",
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
    )
    return document


def _manifest_bytes(payload: bytes) -> bytes:
    return (
        json.dumps(_manifest_document(payload), indent=2, sort_keys=True) + "\n"
    ).encode()


def _manifest(payload: bytes):
    return parse_ready_image_manifest(_manifest_document(payload))


def _partial_paths(cache: Path, payload: bytes) -> tuple[Path, Path, Path]:
    digest = hashlib.sha256(payload).hexdigest()
    directory = cache / "downloads" / digest
    return (
        directory / "ready.tar.gz.part",
        directory / "ready.tar.gz.part.json",
        directory / "ready.tar.gz",
    )


def _interrupt_download(cache: Path, payload: bytes, offset: int = 4) -> None:
    response = FakeResponse(
        payload,
        headers={"Content-Length": str(len(payload)), "ETag": '"object-v1"'},
        fail_after=offset,
    )
    with pytest.raises(P9QemuError, match="HTTPS request failed"):
        acquire_ready_image_archive(
            _manifest(payload),
            cache,
            progress=lambda _message: None,
            transport=FakeTransport(response),
        )


def test_manifest_is_fetched_strictly_and_cached_by_digest(tmp_path: Path) -> None:
    payload = b"archive"
    content = _manifest_bytes(payload)
    requested = "https://catalog.example/image.json?channel=secret"
    response = FakeResponse(
        content,
        headers={"Content-Length": str(len(content))},
        url="https://objects.example/opaque?signature=secret",
    )
    messages: list[str] = []
    transport = FakeTransport(response)

    acquired = fetch_ready_image_manifest(
        requested, tmp_path, progress=messages.append, transport=transport
    )

    digest = hashlib.sha256(content).hexdigest()
    assert acquired.sha256 == digest
    assert acquired.path == tmp_path / "manifests" / digest / "image.json"
    assert acquired.path.read_bytes() == content
    assert acquired.manifest.artifact.sha256 == hashlib.sha256(payload).hexdigest()
    assert transport.requests[0][1]["Accept-Encoding"] == "identity"
    assert all("secret" not in message for message in messages)


def test_manifest_download_enforces_the_byte_limit(tmp_path: Path) -> None:
    content = b"x" * (MAX_MANIFEST_BYTES + 1)
    response = FakeResponse(content)

    with pytest.raises(P9QemuError, match="size limit"):
        fetch_ready_image_manifest(
            "https://catalog.example/image.json",
            tmp_path,
            progress=lambda _message: None,
            transport=FakeTransport(response),
        )

    assert not (tmp_path / "manifests").exists()


def test_final_response_cannot_downgrade_from_https(tmp_path: Path) -> None:
    response = FakeResponse(b"{}", url="http://objects.example/image.json")

    with pytest.raises(P9QemuError, match="must use HTTPS"):
        fetch_ready_image_manifest(
            "https://catalog.example/image.json",
            tmp_path,
            progress=lambda _message: None,
            transport=FakeTransport(response),
        )
    assert response.closed


def test_combined_acquisition_uses_one_transport_for_manifest_and_archive(
    tmp_path: Path,
) -> None:
    payload = b"abcdefghij"
    content = _manifest_bytes(payload)
    manifest_response = FakeResponse(
        content,
        headers={"Content-Length": str(len(content))},
        url="https://catalog.example/image.json",
    )
    archive_response = FakeResponse(
        payload,
        headers={"Content-Length": "10", "ETag": '"object-v1"'},
    )
    transport = FakeTransport(manifest_response, archive_response)

    acquired = acquire_ready_image(
        "https://catalog.example/image.json",
        tmp_path,
        progress=lambda _message: None,
        transport=transport,
    )

    assert acquired.manifest.path.read_bytes() == content
    assert acquired.archive.path.read_bytes() == payload
    assert [request[0] for request in transport.requests] == [
        "https://catalog.example/image.json",
        "https://downloads.example/ready.tar.gz",
    ]


def test_fresh_archive_download_is_verified_and_published(tmp_path: Path) -> None:
    payload = b"abcdefghij"
    response = FakeResponse(
        payload,
        headers={"Content-Length": str(len(payload)), "ETag": '"object-v1"'},
    )
    transport = FakeTransport(response)

    acquired = acquire_ready_image_archive(
        _manifest(payload),
        tmp_path,
        progress=lambda _message: None,
        transport=transport,
    )

    partial, metadata, destination = _partial_paths(tmp_path, payload)
    assert acquired.path == destination
    assert destination.read_bytes() == payload
    assert not partial.exists()
    assert not metadata.exists()
    assert transport.requests[0][1]["Accept-Encoding"] == "identity"
    assert not (destination.parent / ".acquire.lock").exists()


def test_interrupted_download_resumes_with_range_and_strong_etag(
    tmp_path: Path,
) -> None:
    payload = b"abcdefghij"
    _interrupt_download(tmp_path, payload)
    partial, metadata, destination = _partial_paths(tmp_path, payload)
    assert partial.read_bytes() == payload[:4]
    assert metadata.is_file()

    response = FakeResponse(
        payload[4:],
        status=206,
        headers={
            "Content-Length": "6",
            "Content-Range": "bytes 4-9/10",
            "ETag": '"object-v1"',
        },
    )
    transport = FakeTransport(response)
    acquired = acquire_ready_image_archive(
        _manifest(payload),
        tmp_path,
        progress=lambda _message: None,
        transport=transport,
    )

    assert acquired.resumed_from == 4
    assert destination.read_bytes() == payload
    assert transport.requests[0][1]["Range"] == "bytes=4-"
    assert transport.requests[0][1]["If-Range"] == '"object-v1"'


def test_ignored_range_restarts_from_the_returned_full_response(
    tmp_path: Path,
) -> None:
    payload = b"abcdefghij"
    _interrupt_download(tmp_path, payload)
    response = FakeResponse(
        payload,
        status=200,
        headers={"Content-Length": "10", "ETag": '"object-v2"'},
    )
    transport = FakeTransport(response)

    acquired = acquire_ready_image_archive(
        _manifest(payload),
        tmp_path,
        progress=lambda _message: None,
        transport=transport,
    )

    assert acquired.resumed_from == 0
    assert acquired.path.read_bytes() == payload
    assert len(transport.requests) == 1


def test_inconsistent_content_range_is_discarded_before_one_fresh_request(
    tmp_path: Path,
) -> None:
    payload = b"abcdefghij"
    _interrupt_download(tmp_path, payload)
    inconsistent = FakeResponse(
        payload[4:],
        status=206,
        headers={
            "Content-Length": "6",
            "Content-Range": "bytes 3-8/10",
            "ETag": '"object-v1"',
        },
    )
    fresh = FakeResponse(
        payload,
        headers={"Content-Length": "10", "ETag": '"object-v2"'},
    )
    transport = FakeTransport(inconsistent, fresh)

    acquired = acquire_ready_image_archive(
        _manifest(payload),
        tmp_path,
        progress=lambda _message: None,
        transport=transport,
    )

    assert acquired.resumed_from == 0
    assert acquired.path.read_bytes() == payload
    assert len(transport.requests) == 2
    assert "Range" in transport.requests[0][1]
    assert "Range" not in transport.requests[1][1]


def test_transient_resume_http_failure_preserves_valid_partial(
    tmp_path: Path,
) -> None:
    payload = b"abcdefghij"
    _interrupt_download(tmp_path, payload)
    unavailable = FakeResponse(b"", status=503)

    with pytest.raises(P9QemuError, match="HTTP 503"):
        acquire_ready_image_archive(
            _manifest(payload),
            tmp_path,
            progress=lambda _message: None,
            transport=FakeTransport(unavailable),
        )

    partial, metadata, destination = _partial_paths(tmp_path, payload)
    assert partial.read_bytes() == payload[:4]
    assert metadata.is_file()
    assert not destination.exists()


def test_weak_etag_falls_back_to_last_modified_for_resume(tmp_path: Path) -> None:
    payload = b"abcdefghij"
    modified = "Wed, 15 Jul 2026 12:00:00 GMT"
    interrupted = FakeResponse(
        payload,
        headers={
            "Content-Length": "10",
            "ETag": 'W/"weak"',
            "Last-Modified": modified,
        },
        fail_after=4,
    )
    with pytest.raises(P9QemuError, match="HTTPS request failed"):
        acquire_ready_image_archive(
            _manifest(payload),
            tmp_path,
            progress=lambda _message: None,
            transport=FakeTransport(interrupted),
        )
    resumed = FakeResponse(
        payload[4:],
        status=206,
        headers={
            "Content-Length": "6",
            "Content-Range": "bytes 4-9/10",
            "Last-Modified": modified,
        },
    )
    transport = FakeTransport(resumed)

    acquired = acquire_ready_image_archive(
        _manifest(payload),
        tmp_path,
        progress=lambda _message: None,
        transport=transport,
    )

    assert acquired.path.read_bytes() == payload
    assert transport.requests[0][1]["If-Range"] == modified


def test_checksum_failure_removes_partial_state(tmp_path: Path) -> None:
    expected = b"abcdefghij"
    corrupt = b"0123456789"
    response = FakeResponse(
        corrupt,
        headers={"Content-Length": "10", "ETag": '"bad-object"'},
    )

    with pytest.raises(P9QemuError, match="checksum"):
        acquire_ready_image_archive(
            _manifest(expected),
            tmp_path,
            progress=lambda _message: None,
            transport=FakeTransport(response),
        )

    partial, metadata, destination = _partial_paths(tmp_path, expected)
    assert not partial.exists()
    assert not metadata.exists()
    assert not destination.exists()


def test_response_larger_than_manifest_discards_partial_state(tmp_path: Path) -> None:
    expected = b"abcdefghij"
    response = FakeResponse(
        expected + b"x",
        headers={"ETag": '"oversized-object"'},
    )

    with pytest.raises(P9QemuError, match="exceeds"):
        acquire_ready_image_archive(
            _manifest(expected),
            tmp_path,
            progress=lambda _message: None,
            transport=FakeTransport(response),
        )

    partial, metadata, destination = _partial_paths(tmp_path, expected)
    assert not partial.exists()
    assert not metadata.exists()
    assert not destination.exists()


def test_interruption_without_a_validator_is_not_resumable(tmp_path: Path) -> None:
    payload = b"abcdefghij"
    response = FakeResponse(
        payload,
        headers={"Content-Length": "10"},
        fail_after=4,
    )

    with pytest.raises(P9QemuError, match="HTTPS request failed"):
        acquire_ready_image_archive(
            _manifest(payload),
            tmp_path,
            progress=lambda _message: None,
            transport=FakeTransport(response),
        )

    partial, metadata, destination = _partial_paths(tmp_path, payload)
    assert not partial.exists()
    assert not metadata.exists()
    assert not destination.exists()


def test_completed_archive_cache_hit_is_rehashed_without_network(
    tmp_path: Path,
) -> None:
    payload = b"abcdefghij"
    first = FakeResponse(
        payload,
        headers={"Content-Length": "10", "ETag": '"object-v1"'},
    )
    manifest = _manifest(payload)
    acquire_ready_image_archive(
        manifest,
        tmp_path,
        progress=lambda _message: None,
        transport=FakeTransport(first),
    )
    messages: list[str] = []
    transport = FakeTransport()

    acquired = acquire_ready_image_archive(
        manifest, tmp_path, progress=messages.append, transport=transport
    )

    assert acquired.path.read_bytes() == payload
    assert not transport.requests
    assert messages == [f"Using verified ready-image archive: {acquired.path}"]


def test_concurrent_writer_lock_is_refused(tmp_path: Path) -> None:
    payload = b"abcdefghij"
    manifest = _manifest(payload)
    directory = tmp_path / "downloads" / manifest.artifact.sha256
    (directory / ".acquire.lock").mkdir(parents=True)

    with pytest.raises(P9QemuError, match="another process"):
        acquire_ready_image_archive(
            manifest,
            tmp_path,
            progress=lambda _message: None,
            transport=FakeTransport(),
        )
