"""Versioned validation provenance and atomic artifact helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import subprocess
from typing import Any
from uuid import uuid4

from p9qemu import __version__
from p9qemu.answers import InstallAnswers
from p9qemu.errors import P9QemuError
from p9qemu.host import Acceleration, HostInfo
from p9qemu.media import sha256_file
from p9qemu.validation import GuestValidationResult


Runner = Callable[..., subprocess.CompletedProcess[str]]
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


def utc_timestamp() -> str:
    """Return an RFC 3339 UTC timestamp with second precision."""

    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def validate_source_commit(value: str) -> str:
    """Require an exact lowercase Git commit rather than a moving ref."""

    if not _COMMIT.fullmatch(value):
        raise P9QemuError("source commit must be a complete 40-character Git SHA")
    return value


def write_text_new(path: Path, text: str) -> None:
    """Atomically create a UTF-8 file without replacing an existing path."""

    if path.exists():
        raise P9QemuError(f"refusing to replace provenance artifact: {path}")
    if not path.parent.is_dir():
        raise P9QemuError(
            f"provenance artifact parent directory does not exist: {path.parent}"
        )
    temporary = path.with_name(f".{path.name}.p9qemu-{uuid4().hex}.part")
    try:
        temporary.write_text(text, encoding="utf-8", newline="\n")
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            raise P9QemuError(
                f"refusing to replace provenance artifact created concurrently: {path}"
            ) from error
        except OSError as error:
            raise P9QemuError(
                f"could not publish provenance artifact {path}: {error}"
            ) from error
    except OSError as error:
        raise P9QemuError(
            f"could not write provenance artifact {path}: {error}"
        ) from error
    finally:
        temporary.unlink(missing_ok=True)


def write_json_new(path: Path, document: Mapping[str, Any]) -> None:
    """Atomically create a stable indented JSON document."""

    write_text_new(path, json.dumps(document, indent=2, sort_keys=True) + "\n")


def artifact_record(path: Path, *, root: Path) -> dict[str, object]:
    """Describe one bundle artifact using a portable relative path."""

    if not path.is_file():
        raise P9QemuError(f"provenance artifact is not a file: {path}")
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise P9QemuError(
            f"provenance artifact is outside its bundle root: {path}"
        ) from error
    return {
        "path": relative.as_posix(),
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def query_tool_version(
    executable: str,
    *,
    runner: Runner = subprocess.run,
) -> str:
    """Return the first non-empty line from a tool's --version output."""

    try:
        result = runner(
            [executable, "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise P9QemuError(f"could not query {executable} version: {error}") from error
    if result.returncode != 0:
        raise P9QemuError(
            f"{executable} --version exited with status {result.returncode}"
        )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise P9QemuError(f"{executable} --version returned no version text")
    return lines[0]


def qemu_img_check(
    executable: str,
    image: Path,
    *,
    runner: Runner = subprocess.run,
) -> str:
    """Run a non-repairing qemu-img check and return its evidence text."""

    try:
        result = runner(
            [executable, "check", str(image)],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise P9QemuError(f"could not run qemu-img check: {error}") from error
    output = "\n".join(
        part.rstrip() for part in (result.stdout, result.stderr) if part.rstrip()
    )
    if result.returncode != 0:
        suffix = f": {output}" if output else ""
        raise P9QemuError(
            f"qemu-img check exited with status {result.returncode}{suffix}"
        )
    return output + ("\n" if output else "")


def qemu_img_info(
    executable: str,
    image: Path,
    *,
    runner: Runner = subprocess.run,
) -> dict[str, object]:
    """Return decoded JSON metadata for one QEMU disk image."""

    try:
        result = runner(
            [executable, "info", "--output=json", str(image)],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise P9QemuError(f"could not run qemu-img info: {error}") from error
    if result.returncode != 0:
        raise P9QemuError(f"qemu-img info exited with status {result.returncode}")
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise P9QemuError(f"qemu-img info returned invalid JSON: {error}") from error
    if not isinstance(document, dict):
        raise P9QemuError("qemu-img info JSON was not an object")
    return document


def require_unchanged_image(before: str, after: str) -> None:
    """Fail when validation changed the digest of its immutable base image."""

    if before != after:
        raise P9QemuError(
            "base image digest changed during disposable-overlay validation"
        )


def build_install_manifest(
    *,
    started_at: str,
    completed_at: str,
    source_commit: str,
    answers: InstallAnswers,
    answers_path: Path,
    answers_sha256: str,
    iso_path: Path,
    iso_sha256: str,
    image_path: Path,
    image_sha256: str,
    console_log: Path,
    console_log_sha256: str,
    host: HostInfo,
    acceleration: Acceleration,
    memory_mib: int,
    qemu_system_version: str,
    qemu_img_version: str,
    qemu_command: list[str],
    rendered_qemu_command: str,
    image_info: Mapping[str, object],
    image_check: str,
) -> dict[str, object]:
    """Build the private, path-bearing manifest for a completed install."""

    return {
        "schema": 1,
        "kind": "p9qemu-image-installation",
        "status": "passed",
        "started_at": started_at,
        "completed_at": completed_at,
        "p9qemu": {"version": __version__, "commit": source_commit},
        "answers": {
            "path": str(answers_path),
            "sha256": answers_sha256,
            "resolved": asdict(answers),
        },
        "media": {"path": str(iso_path), "sha256": iso_sha256},
        "image": {
            "path": str(image_path),
            "sha256": image_sha256,
            "qemu_img_info": dict(image_info),
            "qemu_img_check": image_check,
        },
        "console_log": {
            "path": str(console_log),
            "sha256": console_log_sha256,
        },
        "host": {
            "system": host.system,
            "distribution_id": host.distribution_id,
            "distribution_name": host.distribution_name,
            "version_id": host.version_id,
            "architecture": platform.machine(),
            "kernel": platform.release(),
        },
        "qemu": {
            "system_version": qemu_system_version,
            "img_version": qemu_img_version,
            "acceleration": acceleration.name,
            "memory_mib": memory_mib,
            "command": {
                "argv": qemu_command,
                "rendered": rendered_qemu_command,
            },
        },
    }


def build_validation_manifest(
    *,
    status: str,
    started_at: str,
    completed_at: str,
    answers: InstallAnswers,
    answers_sha256: str,
    base_image: Path,
    base_sha256_before: str,
    base_sha256_after: str,
    overlay: Path,
    overlay_removed: bool,
    overlay_exists: bool,
    host: HostInfo,
    acceleration: Acceleration,
    memory_mib: int,
    qemu_system_version: str,
    qemu_img_version: str,
    qemu_command: list[str],
    rendered_qemu_command: str,
    image_info: Mapping[str, object],
    validation: GuestValidationResult | None,
    network_mode: str,
    artifacts: Mapping[str, Mapping[str, object]],
    error: str | None = None,
    failure_category: str | None = None,
) -> dict[str, object]:
    """Build the version 1 disposable-overlay validation manifest."""

    unchanged = base_sha256_before == base_sha256_after
    checks = (
        [] if validation is None else [asdict(check) for check in validation.checks]
    )
    return {
        "schema": 1,
        "kind": "p9qemu-image-validation",
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at,
        "p9qemu": {"version": __version__},
        "answers": {
            "sha256": answers_sha256,
            "resolved": asdict(answers),
        },
        "host": {
            "system": host.system,
            "distribution_id": host.distribution_id,
            "distribution_name": host.distribution_name,
            "version_id": host.version_id,
            "architecture": platform.machine(),
            "kernel": platform.release(),
        },
        "image": {
            "path": str(base_image),
            "sha256_before": base_sha256_before,
            "sha256_after": base_sha256_after,
            "unchanged": unchanged,
            "qemu_img_info": dict(image_info),
        },
        "overlay": {
            "path": str(overlay),
            "exists": overlay_exists,
            "removed": overlay_removed,
            "retained_on_failure": overlay_exists and not overlay_removed,
        },
        "qemu": {
            "system_version": qemu_system_version,
            "img_version": qemu_img_version,
            "acceleration": acceleration.name,
            "memory_mib": memory_mib,
            "command": {
                "argv": qemu_command,
                "rendered": rendered_qemu_command,
            },
        },
        "validation": {
            "network_mode": network_mode,
            "checks": checks,
            "error": error,
            "failure_category": failure_category,
        },
        "artifacts": {name: dict(record) for name, record in artifacts.items()},
    }
