"""Bounded HTTPS acquisition for external ready-image manifests and archives."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import (
    HTTPRedirectHandler,
    Request,
    build_opener,
)
from uuid import uuid4

from p9qemu import __version__
from p9qemu.errors import P9QemuError
from p9qemu.media import sha256_file
from p9qemu.ready_image import (
    MAX_MANIFEST_BYTES,
    ReadyImageManifest,
    parse_ready_image_manifest_bytes,
)


Progress = Callable[[str], None]

DOWNLOAD_METADATA_SCHEMA = 1
DOWNLOAD_METADATA_KIND = "p9qemu-ready-image-download"
MAX_REDIRECTS = 8
HTTP_TIMEOUT_SECONDS = 60
_BLOCK_SIZE = 1024 * 1024
_CONTENT_RANGE = re.compile(r"^bytes ([0-9]+)-([0-9]+)/([0-9]+)$")


class HTTPResponse(Protocol):
    """Small response surface used by the acquisition state machine."""

    status: int
    headers: Mapping[str, str]
    url: str

    def read(self, size: int = -1) -> bytes: ...

    def close(self) -> None: ...


class HTTPTransport(Protocol):
    """Injectable HTTPS transport; tests can provide responses without a network."""

    def open(
        self, url: str, *, headers: Mapping[str, str], timeout: int
    ) -> HTTPResponse: ...


@dataclass(frozen=True)
class AcquiredManifest:
    source_url: str
    sha256: str
    path: Path
    manifest: ReadyImageManifest


@dataclass(frozen=True)
class AcquiredArchive:
    manifest: ReadyImageManifest
    path: Path
    resumed_from: int


@dataclass(frozen=True)
class AcquiredReadyImage:
    manifest: AcquiredManifest
    archive: AcquiredArchive


@dataclass(frozen=True)
class _Validator:
    kind: str
    value: str


class _UrllibResponse:
    def __init__(self, response: Any) -> None:
        self._response = response
        self.status = int(response.status)
        self.headers = response.headers
        self.url = response.geturl()

    def read(self, size: int = -1) -> bytes:
        return self._response.read(size)

    def close(self) -> None:
        self._response.close()


class _StrictHTTPSRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        request: Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Mapping[str, str],
        new_url: str,
    ) -> Request | None:
        count = int(getattr(request, "_p9qemu_redirect_count", 0)) + 1
        if count > MAX_REDIRECTS:
            raise HTTPError(
                request.full_url, code, "too many HTTPS redirects", headers, file_pointer
            )
        _validate_https_url(new_url, "redirect URL")
        redirected = super().redirect_request(
            request, file_pointer, code, message, headers, new_url
        )
        if redirected is not None:
            setattr(redirected, "_p9qemu_redirect_count", count)
        return redirected


class UrllibHTTPTransport:
    """Default stdlib transport with normal certificate verification."""

    def __init__(self) -> None:
        self._opener = build_opener(_StrictHTTPSRedirectHandler())

    def open(
        self, url: str, *, headers: Mapping[str, str], timeout: int
    ) -> HTTPResponse:
        request = Request(url, headers=dict(headers), method="GET")
        return _UrllibResponse(self._opener.open(request, timeout=timeout))


def _validate_https_url(url: str, label: str) -> None:
    if (
        not url
        or len(url) > 8192
        or "\\" in url
        or any(ord(character) < 32 for character in url)
    ):
        raise P9QemuError(f"{label} is not a valid HTTPS URL")
    try:
        parsed = urlsplit(url)
        parsed.port
    except ValueError as error:
        raise P9QemuError(f"{label} is not a valid HTTPS URL: {error}") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise P9QemuError(
            f"{label} must use HTTPS without credentials or a fragment"
        )


def redact_url(url: str) -> str:
    """Return a report-safe URL without query parameters or credentials."""

    try:
        parsed = urlsplit(url)
    except ValueError:
        return "<invalid URL>"
    host = parsed.hostname or "<invalid host>"
    try:
        port = parsed.port
    except ValueError:
        port = None
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    authority = f"{host}:{port}" if port is not None else host
    return urlunsplit((parsed.scheme, authority, parsed.path, "", ""))


def _headers() -> dict[str, str]:
    return {
        "Accept-Encoding": "identity",
        "User-Agent": f"p9qemu/{__version__}",
    }


def _header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.casefold()
    for key, value in headers.items():
        if key.casefold() == wanted:
            return str(value).strip()
    return None


def _content_length(headers: Mapping[str, str]) -> int | None:
    value = _header(headers, "Content-Length")
    if value is None:
        return None
    if not value.isascii() or not value.isdecimal():
        raise P9QemuError("HTTPS response has an invalid Content-Length")
    return int(value)


def _require_identity_encoding(headers: Mapping[str, str]) -> None:
    value = _header(headers, "Content-Encoding")
    if value is not None and value.casefold() != "identity":
        raise P9QemuError(
            f"HTTPS response used unsupported content encoding: {value!r}"
        )


def _response_validator(headers: Mapping[str, str]) -> _Validator | None:
    etag = _header(headers, "ETag")
    if (
        etag is not None
        and not etag.startswith("W/")
        and len(etag) >= 2
        and etag.startswith('"')
        and etag.endswith('"')
        and not any(ord(character) < 32 for character in etag)
    ):
        return _Validator("etag", etag)
    modified = _header(headers, "Last-Modified")
    if modified and not any(ord(character) < 32 for character in modified):
        return _Validator("last-modified", modified)
    return None


def _validator_matches(
    headers: Mapping[str, str], expected: _Validator
) -> bool:
    name = "ETag" if expected.kind == "etag" else "Last-Modified"
    return _header(headers, name) == expected.value


def _open(
    transport: HTTPTransport,
    url: str,
    *,
    headers: Mapping[str, str],
) -> HTTPResponse:
    try:
        response = transport.open(url, headers=headers, timeout=HTTP_TIMEOUT_SECONDS)
    except HTTPError as error:
        raise P9QemuError(
            f"HTTPS request returned HTTP {error.code} for {redact_url(url)}"
        ) from error
    except (URLError, OSError) as error:
        raise P9QemuError(
            f"HTTPS request failed for {redact_url(url)}"
        ) from error
    try:
        _validate_https_url(response.url, "final response URL")
    except P9QemuError:
        response.close()
        raise
    return response


def _read_manifest_response(response: HTTPResponse) -> bytes:
    if response.status != 200:
        raise P9QemuError(
            f"manifest HTTPS request returned HTTP {response.status}"
        )
    _require_identity_encoding(response.headers)
    length = _content_length(response.headers)
    if length is not None and length > MAX_MANIFEST_BYTES:
        raise P9QemuError("image manifest exceeds the supported size limit")
    content = bytearray()
    while True:
        block = response.read(min(_BLOCK_SIZE, MAX_MANIFEST_BYTES + 1 - len(content)))
        if not block:
            break
        content.extend(block)
        if len(content) > MAX_MANIFEST_BYTES:
            raise P9QemuError("image manifest exceeds the supported size limit")
    if length is not None and len(content) != length:
        raise P9QemuError("manifest HTTPS response ended before Content-Length")
    return bytes(content)


def _prepare_cache_directory(path: Path, label: str) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise P9QemuError(f"could not create {label}: {error}") from error
    if not path.is_dir():
        raise P9QemuError(f"{label} is not a directory: {path}")


def _write_content_addressed(path: Path, content: bytes, label: str) -> None:
    if path.exists():
        if not path.is_file():
            raise P9QemuError(f"cached {label} is not a file: {path}")
        try:
            existing = path.read_bytes()
        except OSError as error:
            raise P9QemuError(f"could not read cached {label}: {error}") from error
        if existing != content:
            raise P9QemuError(f"content-addressed {label} cache is inconsistent")
        return
    temporary = path.with_name(f".{path.name}.p9qemu-{uuid4().hex}.part")
    try:
        with temporary.open("xb") as output:
            output.write(content)
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != content:
                raise P9QemuError(
                    f"content-addressed {label} cache is inconsistent"
                )
    except P9QemuError:
        raise
    except OSError as error:
        raise P9QemuError(f"could not cache {label}: {error}") from error
    finally:
        temporary.unlink(missing_ok=True)


def fetch_ready_image_manifest(
    url: str,
    cache_dir: Path,
    *,
    progress: Progress,
    transport: HTTPTransport | None = None,
) -> AcquiredManifest:
    """Fetch, strictly parse, and content-address one selected image manifest."""

    _validate_https_url(url, "manifest URL")
    selected_transport = transport or UrllibHTTPTransport()
    progress(f"Fetching ready-image manifest: {redact_url(url)}")
    response = _open(selected_transport, url, headers=_headers())
    try:
        try:
            content = _read_manifest_response(response)
        except (URLError, OSError) as error:
            raise P9QemuError(
                f"HTTPS request failed for {redact_url(url)}"
            ) from error
    finally:
        response.close()
    manifest = parse_ready_image_manifest_bytes(
        content, source=redact_url(url)
    )
    digest = hashlib.sha256(content).hexdigest()
    entry = cache_dir / "manifests" / digest
    _prepare_cache_directory(entry, "ready-image manifest cache")
    path = entry / "image.json"
    _write_content_addressed(path, content, "ready-image manifest")
    progress(f"Verified ready-image manifest SHA-256: {digest}")
    return AcquiredManifest(url, digest, path, manifest)


def _metadata_document(
    manifest: ReadyImageManifest, validator: _Validator
) -> dict[str, object]:
    return {
        "schema": DOWNLOAD_METADATA_SCHEMA,
        "kind": DOWNLOAD_METADATA_KIND,
        "url": manifest.artifact.url,
        "filename": manifest.artifact.filename,
        "size": manifest.artifact.size,
        "sha256": manifest.artifact.sha256,
        "validator": {"kind": validator.kind, "value": validator.value},
    }


def _write_metadata(path: Path, document: Mapping[str, object]) -> None:
    content = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()
    temporary = path.with_name(f".{path.name}.p9qemu-{uuid4().hex}.part")
    try:
        with temporary.open("xb") as output:
            output.write(content)
        os.replace(temporary, path)
    except OSError as error:
        raise P9QemuError(f"could not save download resume metadata: {error}") from error
    finally:
        temporary.unlink(missing_ok=True)


def _load_metadata(path: Path, manifest: ReadyImageManifest) -> _Validator | None:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    expected_keys = {"schema", "kind", "url", "filename", "size", "sha256", "validator"}
    if not isinstance(document, dict) or set(document) != expected_keys:
        return None
    if any(
        document.get(key) != value
        for key, value in {
            "schema": DOWNLOAD_METADATA_SCHEMA,
            "kind": DOWNLOAD_METADATA_KIND,
            "url": manifest.artifact.url,
            "filename": manifest.artifact.filename,
            "size": manifest.artifact.size,
            "sha256": manifest.artifact.sha256,
        }.items()
    ):
        return None
    validator = document.get("validator")
    if not isinstance(validator, dict) or set(validator) != {"kind", "value"}:
        return None
    kind = validator.get("kind")
    value = validator.get("value")
    if kind not in {"etag", "last-modified"} or not isinstance(value, str) or not value:
        return None
    if any(ord(character) < 32 for character in value):
        return None
    return _Validator(kind, value)


def _discard_partial(partial: Path, metadata: Path) -> None:
    partial.unlink(missing_ok=True)
    metadata.unlink(missing_ok=True)


def _resume_state(
    partial: Path, metadata: Path, manifest: ReadyImageManifest
) -> tuple[int, _Validator] | None:
    if not partial.exists() or not metadata.exists():
        _discard_partial(partial, metadata)
        return None
    if not partial.is_file() or not metadata.is_file():
        raise P9QemuError("ready-image partial download state is not made of files")
    validator = _load_metadata(metadata, manifest)
    size = partial.stat().st_size
    if validator is None or size <= 0 or size > manifest.artifact.size:
        _discard_partial(partial, metadata)
        return None
    return size, validator


def _validate_fresh_response(
    response: HTTPResponse, expected_size: int
) -> _Validator | None:
    if response.status != 200:
        raise P9QemuError(
            f"archive HTTPS request returned HTTP {response.status}"
        )
    _require_identity_encoding(response.headers)
    length = _content_length(response.headers)
    if length is not None and length != expected_size:
        raise P9QemuError("archive Content-Length does not match image manifest")
    return _response_validator(response.headers)


def _resume_response_is_exact(
    response: HTTPResponse,
    *,
    offset: int,
    expected_size: int,
    validator: _Validator,
) -> bool:
    if response.status != 206 or not _validator_matches(response.headers, validator):
        return False
    _require_identity_encoding(response.headers)
    value = _header(response.headers, "Content-Range")
    match = _CONTENT_RANGE.fullmatch(value or "")
    if match is None:
        return False
    start, end, total = (int(part) for part in match.groups())
    if (start, end, total) != (offset, expected_size - 1, expected_size):
        return False
    length = _content_length(response.headers)
    return length is None or length == expected_size - offset


def _stream_archive_response(
    response: HTTPResponse,
    partial: Path,
    metadata: Path,
    manifest: ReadyImageManifest,
    *,
    append: bool,
    validator: _Validator | None,
) -> None:
    expected_size = manifest.artifact.size
    if validator is not None:
        _write_metadata(metadata, _metadata_document(manifest, validator))
    else:
        metadata.unlink(missing_ok=True)
    mode = "ab" if append else "wb"
    try:
        with partial.open(mode) as output:
            written = partial.stat().st_size if append else 0
            while True:
                block = response.read(min(_BLOCK_SIZE, expected_size - written + 1))
                if not block:
                    break
                output.write(block)
                written += len(block)
                if written > expected_size:
                    raise P9QemuError(
                        "archive HTTPS response exceeds the manifest size"
                    )
        if written != expected_size:
            raise P9QemuError(
                "archive HTTPS response ended before the manifest size"
            )
    except (URLError, OSError) as error:
        if validator is None:
            _discard_partial(partial, metadata)
        raise P9QemuError(
            f"HTTPS request failed for {redact_url(manifest.artifact.url)}"
        ) from error
    except P9QemuError:
        if validator is None or (
            partial.is_file() and partial.stat().st_size > expected_size
        ):
            _discard_partial(partial, metadata)
        raise


def _verify_and_publish(
    partial: Path, metadata: Path, destination: Path, manifest: ReadyImageManifest
) -> None:
    if partial.stat().st_size != manifest.artifact.size:
        raise P9QemuError("partial archive size does not match image manifest")
    actual = sha256_file(partial)
    if actual != manifest.artifact.sha256:
        _discard_partial(partial, metadata)
        raise P9QemuError(
            "ready-image archive checksum does not match image manifest"
        )
    try:
        os.link(partial, destination)
    except FileExistsError:
        if (
            not destination.is_file()
            or destination.stat().st_size != manifest.artifact.size
            or sha256_file(destination) != manifest.artifact.sha256
        ):
            raise P9QemuError(
                "completed ready-image archive cache is inconsistent"
            )
    except OSError as error:
        raise P9QemuError(f"could not publish ready-image archive: {error}") from error
    partial.unlink(missing_ok=True)
    metadata.unlink(missing_ok=True)


def _verified_destination(
    destination: Path, manifest: ReadyImageManifest
) -> bool:
    if not destination.exists():
        return False
    if not destination.is_file():
        raise P9QemuError(
            f"cached ready-image archive is not a file: {destination}"
        )
    if (
        destination.stat().st_size == manifest.artifact.size
        and sha256_file(destination) == manifest.artifact.sha256
    ):
        return True
    destination.unlink()
    return False


def _acquire_under_lock(
    manifest: ReadyImageManifest,
    directory: Path,
    *,
    progress: Progress,
    transport: HTTPTransport,
) -> AcquiredArchive:
    destination = directory / manifest.artifact.filename
    partial = directory / f"{manifest.artifact.filename}.part"
    metadata = directory / f"{manifest.artifact.filename}.part.json"
    if _verified_destination(destination, manifest):
        progress(f"Using verified ready-image archive: {destination}")
        return AcquiredArchive(manifest, destination, 0)

    state = _resume_state(partial, metadata, manifest)
    if state is not None and state[0] == manifest.artifact.size:
        _verify_and_publish(partial, metadata, destination, manifest)
        progress(f"Verified ready-image archive: {destination}")
        return AcquiredArchive(manifest, destination, state[0])

    offset = state[0] if state is not None else 0
    validator = state[1] if state is not None else None
    remaining = manifest.artifact.size - offset
    if shutil.disk_usage(directory).free < remaining:
        raise P9QemuError("not enough free space to download the ready-image archive")

    request_headers = _headers()
    if state is not None:
        request_headers["Range"] = f"bytes={offset}-"
        request_headers["If-Range"] = validator.value
        progress(f"Resuming ready-image archive at byte {offset}: {destination}")
    else:
        progress(
            f"Downloading ready-image archive: {redact_url(manifest.artifact.url)}"
        )
    response = _open(transport, manifest.artifact.url, headers=request_headers)
    try:
        if state is not None and _resume_response_is_exact(
            response,
            offset=offset,
            expected_size=manifest.artifact.size,
            validator=validator,
        ):
            _stream_archive_response(
                response,
                partial,
                metadata,
                manifest,
                append=True,
                validator=validator,
            )
        elif state is not None and response.status == 200:
            progress("Remote object changed or ignored Range; restarting safely")
            _discard_partial(partial, metadata)
            fresh_validator = _validate_fresh_response(
                response, manifest.artifact.size
            )
            _stream_archive_response(
                response,
                partial,
                metadata,
                manifest,
                append=False,
                validator=fresh_validator,
            )
            offset = 0
        elif state is not None and response.status in {206, 412, 416}:
            response.close()
            progress("Resume response was inconsistent; restarting safely")
            _discard_partial(partial, metadata)
            response = _open(transport, manifest.artifact.url, headers=_headers())
            fresh_validator = _validate_fresh_response(
                response, manifest.artifact.size
            )
            _stream_archive_response(
                response,
                partial,
                metadata,
                manifest,
                append=False,
                validator=fresh_validator,
            )
            offset = 0
        elif state is not None:
            raise P9QemuError(
                f"archive resume request returned HTTP {response.status}"
            )
        else:
            fresh_validator = _validate_fresh_response(
                response, manifest.artifact.size
            )
            _stream_archive_response(
                response,
                partial,
                metadata,
                manifest,
                append=False,
                validator=fresh_validator,
            )
    finally:
        response.close()
    _verify_and_publish(partial, metadata, destination, manifest)
    progress(f"Verified ready-image archive SHA-256: {manifest.artifact.sha256}")
    return AcquiredArchive(manifest, destination, offset)


def acquire_ready_image_archive(
    manifest: ReadyImageManifest,
    cache_dir: Path,
    *,
    progress: Progress,
    transport: HTTPTransport | None = None,
) -> AcquiredArchive:
    """Acquire one manifest-pinned archive with safe cross-invocation resume."""

    directory = cache_dir / "downloads" / manifest.artifact.sha256
    _prepare_cache_directory(directory, "ready-image download cache")
    lock = directory / ".acquire.lock"
    try:
        lock.mkdir()
    except FileExistsError as error:
        raise P9QemuError(
            f"another process owns the ready-image download lock: {lock}"
        ) from error
    except OSError as error:
        raise P9QemuError(f"could not create ready-image download lock: {error}") from error
    try:
        return _acquire_under_lock(
            manifest,
            directory,
            progress=progress,
            transport=transport or UrllibHTTPTransport(),
        )
    finally:
        try:
            lock.rmdir()
        except OSError:
            pass


def acquire_ready_image(
    manifest_url: str,
    cache_dir: Path,
    *,
    progress: Progress,
    transport: HTTPTransport | None = None,
) -> AcquiredReadyImage:
    """Resolve an exact manifest, then acquire its verified compressed archive."""

    selected_transport = transport or UrllibHTTPTransport()
    manifest = fetch_ready_image_manifest(
        manifest_url,
        cache_dir,
        progress=progress,
        transport=selected_transport,
    )
    archive = acquire_ready_image_archive(
        manifest.manifest,
        cache_dir,
        progress=progress,
        transport=selected_transport,
    )
    return AcquiredReadyImage(manifest, archive)
