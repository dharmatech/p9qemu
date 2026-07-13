"""Safe download, verification, decompression, and caching of install media."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import gzip
import hashlib
from pathlib import Path
import shutil
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from p9qemu import __version__
from p9qemu.constants import (
    DEFAULT_ARCHIVE_NAME,
    DEFAULT_ARCHIVE_SHA256,
    DEFAULT_ISO_NAME,
    DEFAULT_ISO_URL,
)
from p9qemu.errors import P9QemuError


Progress = Callable[[str], None]


@dataclass(frozen=True)
class MediaSpec:
    url: str = DEFAULT_ISO_URL
    archive_name: str = DEFAULT_ARCHIVE_NAME
    iso_name: str = DEFAULT_ISO_NAME
    archive_sha256: str | None = DEFAULT_ARCHIVE_SHA256


@dataclass(frozen=True)
class MediaPaths:
    archive: Path
    iso: Path


def paths_for(cache_dir: Path, spec: MediaSpec) -> MediaPaths:
    return MediaPaths(cache_dir / spec.archive_name, cache_dir / spec.iso_name)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise P9QemuError(f"could not read {path}: {error}") from error
    return digest.hexdigest()


def verify_archive(path: Path, expected: str | None) -> None:
    if expected is None:
        return
    actual = sha256_file(path)
    if actual.lower() != expected.lower():
        raise P9QemuError(
            f"checksum mismatch for {path}\n"
            f"expected: {expected.lower()}\n"
            f"actual:   {actual.lower()}"
        )


def inspect_media(
    cache_dir: Path, spec: MediaSpec, *, progress: Progress
) -> MediaPaths:
    if cache_dir.exists() and not cache_dir.is_dir():
        raise P9QemuError(f"cache path is not a directory: {cache_dir}")
    paths = paths_for(cache_dir, spec)
    if paths.iso.exists():
        if not paths.iso.is_file():
            raise P9QemuError(f"cached ISO path is not a file: {paths.iso}")
        progress(f"Using cached installation ISO: {paths.iso}")
        return paths
    if paths.archive.exists():
        if not paths.archive.is_file():
            raise P9QemuError(f"cached archive path is not a file: {paths.archive}")
        verify_archive(paths.archive, spec.archive_sha256)
        progress(f"Using cached installation archive: {paths.archive}")
    else:
        progress(f"Would download {spec.url} to {paths.archive}")
    progress(f"Would unpack installation ISO to {paths.iso}")
    return paths


def _download(spec: MediaSpec, destination: Path) -> None:
    partial = destination.with_name(destination.name + ".part")
    partial.unlink(missing_ok=True)
    request = Request(spec.url, headers={"User-Agent": f"p9qemu/{__version__}"})
    digest = hashlib.sha256()
    try:
        with urlopen(request, timeout=60) as response, partial.open("xb") as output:
            while block := response.read(1024 * 1024):
                output.write(block)
                digest.update(block)
        if spec.archive_sha256 is not None:
            actual = digest.hexdigest()
            if actual.lower() != spec.archive_sha256.lower():
                raise P9QemuError(
                    f"checksum mismatch for downloaded archive\n"
                    f"expected: {spec.archive_sha256.lower()}\n"
                    f"actual:   {actual.lower()}"
                )
        partial.replace(destination)
    except (HTTPError, URLError, OSError) as error:
        raise P9QemuError(f"could not download {spec.url}: {error}") from error
    finally:
        partial.unlink(missing_ok=True)


def _decompress(source: Path, destination: Path) -> None:
    partial = destination.with_name(destination.name + ".part")
    partial.unlink(missing_ok=True)
    try:
        with gzip.open(source, "rb") as compressed, partial.open("xb") as output:
            shutil.copyfileobj(compressed, output, length=1024 * 1024)
        partial.replace(destination)
    except (gzip.BadGzipFile, EOFError, OSError) as error:
        raise P9QemuError(f"could not unpack {source}: {error}") from error
    finally:
        partial.unlink(missing_ok=True)


def prepare_media(
    cache_dir: Path, spec: MediaSpec, *, progress: Progress
) -> MediaPaths:
    paths = paths_for(cache_dir, spec)
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise P9QemuError(
            f"could not create cache directory {cache_dir}: {error}"
        ) from error
    if not cache_dir.is_dir():
        raise P9QemuError(f"cache path is not a directory: {cache_dir}")

    if paths.iso.exists():
        if not paths.iso.is_file():
            raise P9QemuError(f"cached ISO path is not a file: {paths.iso}")
        progress(f"Using cached installation ISO: {paths.iso}")
        return paths

    if paths.archive.exists():
        if not paths.archive.is_file():
            raise P9QemuError(f"cached archive path is not a file: {paths.archive}")
        verify_archive(paths.archive, spec.archive_sha256)
        progress(f"Using cached installation archive: {paths.archive}")
    else:
        progress(f"Downloading {spec.url}")
        _download(spec, paths.archive)

    progress(f"Unpacking installation ISO: {paths.iso}")
    _decompress(paths.archive, paths.iso)
    return paths
