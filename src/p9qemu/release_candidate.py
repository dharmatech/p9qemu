"""Local, non-publishing release-candidate bundle construction."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
import gzip
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
from typing import Any
from uuid import uuid4

from p9qemu import __version__
from p9qemu.answers import InstallAnswers
from p9qemu.constants import DEFAULT_MAC_ADDRESS
from p9qemu.errors import P9QemuError
from p9qemu.host import Acceleration
from p9qemu.media import sha256_file
from p9qemu.provenance import (
    artifact_record,
    utc_timestamp,
    write_json_new,
    write_text_new,
)
from p9qemu.qemu import DEFAULT_PORT_FORWARDS, build_start_command, render_command


_IDENTIFIER = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_HOST_PATH_PATTERNS = (
    re.compile(r"(?i)\b[a-z]:(?:\\{1,2}|/)(?:users|home)(?:\\{1,2}|/)[^\\/\s]+"),
    re.compile(r"/(?:home|users)/[^/\s]+"),
    re.compile(r"(?i)/mnt/[a-z]/users/[^/\s]+"),
)
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]+\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]+\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(
        r"""(?ix)["']?\b(?:pass|password|passwd|token|secret|api[_-]?key)\b["']?"""
        r"""\s*[:=]\s*["']?[^\s"',}]+"""
    ),
)
_PUBLIC_VALIDATION_ARTIFACTS = {
    "console_log": "validation/boot.raw.log",
    "qemu_img_check_before": "validation/qemu-img-check-before.txt",
    "qemu_img_check_after": "validation/qemu-img-check-after.txt",
}
_REQUIRED_VALIDATION_CHECKS = {
    "serial-boot",
    "root-filesystem",
    "guest.user",
    "guest.home",
    "guest.sysname",
    "guest.plan9-ini",
    "network-ping",
    "orderly-shutdown",
}


@dataclass(frozen=True)
class CandidateIdentity:
    image_id: str
    build_id: str

    @property
    def bundle_name(self) -> str:
        return f"p9qemu-{self.image_id}-{self.build_id}"


@dataclass(frozen=True)
class CandidateInputs:
    identity: CandidateIdentity
    source_commit: str
    disk: Path
    answers_path: Path
    install_log: Path
    validation_manifest: Path
    output_dir: Path
    image_hygiene_reviewed: bool


@dataclass(frozen=True)
class CandidateResult:
    output_dir: Path
    bundle_dir: Path
    archive: Path
    archive_sha256: str
    image_sha256: str
    manifest: Path
    verification: Path


def validate_identity(image_id: str, build_id: str) -> CandidateIdentity:
    """Validate immutable, filename-safe release-candidate identity fields."""

    for label, value in (("image ID", image_id), ("build ID", build_id)):
        if not _IDENTIFIER.fullmatch(value):
            raise P9QemuError(
                f"{label} must contain lowercase letters, digits, and single hyphens: "
                f"{value!r}"
            )
        if value == "latest":
            raise P9QemuError(f"{label} cannot use the moving name 'latest'")
    identity = CandidateIdentity(image_id, build_id)
    if len(identity.bundle_name) > 90:
        raise P9QemuError("release-candidate bundle name is too long")
    return identity


def validate_source_commit(value: str) -> str:
    """Require an exact lowercase Git commit rather than a moving ref."""

    if not _COMMIT.fullmatch(value):
        raise P9QemuError("source commit must be a complete 40-character Git SHA")
    return value


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    """Load one UTF-8 JSON object with an actionable error."""

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise P9QemuError(f"could not read {label} {path}: {error}") from error
    if not isinstance(document, dict):
        raise P9QemuError(f"{label} must contain a JSON object: {path}")
    return document


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise P9QemuError(f"validation manifest requires an object at {label}")
    return value


def _string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise P9QemuError(f"validation manifest requires text at {label}")
    return value


def _require_validation_checkpoints(
    document: Mapping[str, Any],
    *,
    image_sha256: str,
    answers_sha256: str,
) -> None:
    if document.get("schema") != 1 or document.get("kind") != "p9qemu-image-validation":
        raise P9QemuError("unsupported validation manifest schema or kind")
    if document.get("status") != "passed":
        raise P9QemuError("release candidates require validation status 'passed'")

    image = _mapping(document.get("image"), "image")
    before = _string(image.get("sha256_before"), "image.sha256_before")
    after = _string(image.get("sha256_after"), "image.sha256_after")
    if image.get("unchanged") is not True or before != after:
        raise P9QemuError("validation did not prove that the base image was unchanged")
    if before.lower() != image_sha256.lower():
        raise P9QemuError("validated image digest does not match the supplied disk")

    manifest_answers = _mapping(document.get("answers"), "answers")
    recorded_answers = _string(manifest_answers.get("sha256"), "answers.sha256")
    if recorded_answers.lower() != answers_sha256.lower():
        raise P9QemuError(
            "validated answer digest does not match the supplied answer file"
        )

    overlay = _mapping(document.get("overlay"), "overlay")
    if overlay.get("removed") is not True or overlay.get("exists") is not False:
        raise P9QemuError("successful validation overlay was not removed")

    validation = _mapping(document.get("validation"), "validation")
    if validation.get("error") is not None:
        raise P9QemuError("validation manifest records an error")
    checks = validation.get("checks")
    if not isinstance(checks, list) or not checks:
        raise P9QemuError("validation manifest contains no guest checks")
    if any(
        not isinstance(check, dict) or check.get("status") != "passed"
        for check in checks
    ):
        raise P9QemuError("release candidates require every validation check to pass")
    check_names = {
        check.get("name") for check in checks if isinstance(check.get("name"), str)
    }
    missing_checks = sorted(_REQUIRED_VALIDATION_CHECKS - check_names)
    if missing_checks:
        raise P9QemuError(
            f"validation manifest is missing required checks: {missing_checks}"
        )

    image_info = _mapping(image.get("qemu_img_info"), "image.qemu_img_info")
    if image_info.get("format") != "qcow2":
        raise P9QemuError("release candidate image format must be qcow2")
    virtual_size = image_info.get("virtual-size")
    if type(virtual_size) is not int or virtual_size <= 0:
        raise P9QemuError("validation manifest requires a positive image virtual size")
    if image_info.get("dirty-flag") is not False:
        raise P9QemuError("release candidate QCOW2 image has a dirty flag")


def _sanitized_image_info(value: object) -> dict[str, object]:
    info = _mapping(value, "image.qemu_img_info")
    result = {
        name: info[name]
        for name in (
            "format",
            "virtual-size",
            "actual-size",
            "cluster-size",
            "dirty-flag",
        )
        if name in info
    }
    format_specific = info.get("format-specific")
    if isinstance(format_specific, dict):
        data = format_specific.get("data")
        sanitized_data = {}
        if isinstance(data, dict):
            sanitized_data = {
                name: data[name]
                for name in (
                    "compat",
                    "compression-type",
                    "corrupt",
                    "extended-l2",
                    "lazy-refcounts",
                    "refcount-bits",
                )
                if name in data
            }
        result["format-specific"] = {
            "type": format_specific.get("type"),
            "data": sanitized_data,
        }
    return result


def sanitize_validation_manifest(
    document: Mapping[str, Any],
    *,
    source_sha256: str,
) -> dict[str, object]:
    """Create the allow-listed, path-free public validation record."""

    image = _mapping(document.get("image"), "image")
    host = _mapping(document.get("host"), "host")
    qemu = _mapping(document.get("qemu"), "qemu")
    overlay = _mapping(document.get("overlay"), "overlay")
    answers = _mapping(document.get("answers"), "answers")
    validation = _mapping(document.get("validation"), "validation")
    p9qemu = _mapping(document.get("p9qemu"), "p9qemu")
    return {
        "schema": 1,
        "kind": "p9qemu-public-image-validation",
        "source_manifest_sha256": source_sha256,
        "status": document.get("status"),
        "started_at": document.get("started_at"),
        "completed_at": document.get("completed_at"),
        "p9qemu": {"version": p9qemu.get("version")},
        "answers": {
            "sha256": answers.get("sha256"),
            "resolved": answers.get("resolved"),
        },
        "host": {
            name: host.get(name)
            for name in ("system", "distribution_id", "version_id", "architecture")
        },
        "image": {
            "sha256": image.get("sha256_after"),
            "unchanged_during_validation": image.get("unchanged"),
            "qemu_img_info": _sanitized_image_info(image.get("qemu_img_info")),
        },
        "overlay": {
            "exists": overlay.get("exists"),
            "removed": overlay.get("removed"),
        },
        "qemu": {
            name: qemu.get(name)
            for name in (
                "system_version",
                "img_version",
                "acceleration",
                "memory_mib",
            )
        },
        "validation": {
            "network_mode": validation.get("network_mode"),
            "checks": validation.get("checks"),
            "error": validation.get("error"),
            "failure_category": validation.get("failure_category"),
        },
    }


def scan_public_text(paths: Sequence[Path], *, root: Path) -> tuple[str, ...]:
    """Reject common host paths, tokens, and inline secret assignments."""

    scanned: list[str] = []
    for path in paths:
        try:
            relative = path.relative_to(root).as_posix()
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, ValueError) as error:
            raise P9QemuError(
                f"could not privacy-scan public text {path}: {error}"
            ) from error
        for pattern in (*_HOST_PATH_PATTERNS, *_SECRET_PATTERNS):
            match = pattern.search(text)
            if match:
                raise P9QemuError(
                    f"public text privacy scan rejected {relative}: matched "
                    f"{match.group(0)!r}"
                )
        scanned.append(relative)
    return tuple(sorted(scanned))


def _safe_relative_artifact(root: Path, record: Mapping[str, Any], label: str) -> Path:
    relative_text = _string(record.get("path"), f"artifacts.{label}.path")
    relative = PurePosixPath(relative_text)
    if relative.is_absolute() or ".." in relative.parts:
        raise P9QemuError(f"validation artifact path is unsafe: {relative_text}")
    source = root.joinpath(*relative.parts).resolve()
    try:
        source.relative_to(root.resolve())
    except ValueError as error:
        raise P9QemuError(
            f"validation artifact escapes its evidence directory: {source}"
        ) from error
    if not source.is_file():
        raise P9QemuError(f"validation artifact is missing: {source}")
    expected_size = record.get("size")
    if type(expected_size) is not int or source.stat().st_size != expected_size:
        raise P9QemuError(f"validation artifact size mismatch: {source}")
    expected_digest = _string(record.get("sha256"), f"artifacts.{label}.sha256")
    if sha256_file(source).lower() != expected_digest.lower():
        raise P9QemuError(f"validation artifact checksum mismatch: {source}")
    return source


def public_validation_artifacts(
    document: Mapping[str, Any], manifest_path: Path
) -> dict[str, Path]:
    """Resolve and verify the public-safe subset of validation evidence."""

    records = _mapping(document.get("artifacts"), "artifacts")
    sources: dict[str, Path] = {}
    for label, destination in _PUBLIC_VALIDATION_ARTIFACTS.items():
        record = _mapping(records.get(label), f"artifacts.{label}")
        sources[destination] = _safe_relative_artifact(
            manifest_path.parent, record, label
        )
    return sources


def copy_file_new(source: Path, destination: Path) -> None:
    """Copy one file without replacing an existing destination."""

    try:
        with source.open("rb") as input_stream, destination.open("xb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
    except OSError as error:
        raise P9QemuError(
            f"could not copy {source} to {destination}: {error}"
        ) from error


def _runtime_profile() -> dict[str, object]:
    return {
        "schema": 1,
        "memory_mib": 2048,
        "nic_model": "virtio",
        "storage": "virtio-scsi",
        "network": "user",
        "default_mac_address": DEFAULT_MAC_ADDRESS,
        "port_forwards": [
            {
                "protocol": item.protocol,
                "host_address": item.host_address,
                "host_port": item.host_port,
                "guest_port": item.guest_port,
            }
            for item in DEFAULT_PORT_FORWARDS
        ],
    }


def _running_document(image_name: str, identity: CandidateIdentity) -> str:
    working_name = f"{identity.image_id}-working.qcow2"
    linux_command = build_start_command(
        "qemu-system-x86_64",
        disk=Path(working_name),
        memory_mib=2048,
        acceleration=Acceleration("KVM", ("-cpu", "host", "-accel", "kvm")),
    )
    windows_whpx = build_start_command(
        r"C:\Program Files\qemu\qemu-system-x86_64.exe",
        disk=Path(working_name),
        memory_mib=2048,
        acceleration=Acceleration(
            "WHPX with userspace irqchip and SDL",
            ("-accel", "whpx,kernel-irqchip=off", "-display", "sdl"),
        ),
    )
    windows_tcg = build_start_command(
        r"C:\Program Files\qemu\qemu-system-x86_64.exe",
        disk=Path(working_name),
        memory_mib=2048,
        acceleration=Acceleration("TCG software emulation", ("-accel", "tcg")),
    )
    return f"""# Run {identity.bundle_name}

This is a local release candidate, not a published p9qemu image release.
The supplied `{image_name}` file is the immutable, checksum-bound base image.
Do not boot it directly. First make a writable working copy.

## Linux

```console
$ cp {image_name} {working_name}
$ p9qemu start --disk {working_name}
```

Without p9qemu, use the tested Linux KVM profile:

```sh
{render_command(linux_command, system="Linux")}
```

## Windows

```powershell
Copy-Item {image_name} {working_name}
p9qemu start --disk {working_name} --accel whpx
```

Without p9qemu, use the tested WHPX plus SDL profile:

```powershell
{render_command(windows_whpx, system="Windows")}
```

Use this portable software-emulation profile if WHPX is unavailable:

```powershell
{render_command(windows_tcg, system="Windows")}
```

Every working copy is independent. Verify the immutable base against
`manifest.json` before creating it.
"""


def _artifact_records(
    bundle: Path, paths: Sequence[Path]
) -> dict[str, dict[str, object]]:
    return {
        path.relative_to(bundle).as_posix(): artifact_record(path, root=bundle)
        for path in sorted(paths)
    }


def _release_manifest(
    *,
    inputs: CandidateInputs,
    answers: InstallAnswers,
    image_path: Path,
    image_sha256: str,
    public_validation_path: Path,
    public_validation: Mapping[str, Any],
    private_validation_sha256: str,
    artifacts: Mapping[str, Mapping[str, object]],
    scanned_text: Sequence[str],
) -> dict[str, object]:
    info = _mapping(
        _mapping(public_validation.get("image"), "public_validation.image").get(
            "qemu_img_info"
        ),
        "public_validation.image.qemu_img_info",
    )
    return {
        "schema": 1,
        "kind": "p9qemu-image-release-candidate",
        "stage": "local-only",
        "created_at": utc_timestamp(),
        "identity": {
            "image_id": inputs.identity.image_id,
            "build_id": inputs.identity.build_id,
            "bundle_name": inputs.identity.bundle_name,
        },
        "source": {
            "p9qemu_version": __version__,
            "p9qemu_commit": inputs.source_commit,
            "installer_profile": answers.installer_profile,
            "installation_media_sha256": answers.iso_sha256,
            "answer_schema": answers.schema,
        },
        "image": {
            "path": image_path.name,
            "format": info.get("format"),
            "virtual_size": info.get("virtual-size"),
            "stored_size": image_path.stat().st_size,
            "sha256": image_sha256,
        },
        "validation": {
            "path": public_validation_path.relative_to(image_path.parent).as_posix(),
            "status": public_validation.get("status"),
            "private_source_manifest_sha256": private_validation_sha256,
        },
        "runtime_profile": _runtime_profile(),
        "hygiene": {
            "image_contents_review_confirmed": inputs.image_hygiene_reviewed,
            "public_text_scan": "passed",
            "scanned_text_files": list(scanned_text),
            "scope": (
                "The automated scan covers public text metadata and logs; the image "
                "contents require the separately recorded human review."
            ),
        },
        "artifacts": {name: dict(record) for name, record in artifacts.items()},
        "publication": {
            "uploaded": False,
            "asset_replacement_permitted": False,
        },
    }


def create_deterministic_tar_gz(bundle: Path, archive: Path) -> None:
    """Archive one bundle with stable names, order, ownership, modes, and times."""

    if archive.exists():
        raise P9QemuError(f"refusing to replace release archive: {archive}")
    partial = archive.with_name(f".{archive.name}.p9qemu-{uuid4().hex}.part")
    try:
        with partial.open("xb") as raw:
            with gzip.GzipFile(
                fileobj=raw, mode="wb", filename="", mtime=0
            ) as compressed:
                with tarfile.open(
                    fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT
                ) as tar:
                    paths = [bundle, *sorted(bundle.rglob("*"))]
                    for path in paths:
                        relative = path.relative_to(bundle.parent).as_posix()
                        info = tar.gettarinfo(str(path), arcname=relative)
                        info.uid = 0
                        info.gid = 0
                        info.uname = ""
                        info.gname = ""
                        info.mtime = 0
                        info.mode = 0o755 if path.is_dir() else 0o644
                        if path.is_dir():
                            tar.addfile(info)
                        elif path.is_file():
                            with path.open("rb") as source:
                                tar.addfile(info, source)
                        else:
                            raise P9QemuError(
                                f"release bundle contains an unsupported entry: {path}"
                            )
        os.replace(partial, archive)
    except (OSError, tarfile.TarError) as error:
        raise P9QemuError(
            f"could not create release archive {archive}: {error}"
        ) from error
    finally:
        partial.unlink(missing_ok=True)


def _safe_archive_member(member: tarfile.TarInfo, bundle_name: str) -> PurePosixPath:
    path = PurePosixPath(member.name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise P9QemuError(f"release archive contains an unsafe path: {member.name}")
    if path.parts[0] != bundle_name:
        raise P9QemuError(f"release archive contains an unexpected root: {member.name}")
    if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
        raise P9QemuError(
            f"release archive contains an unsupported entry: {member.name}"
        )
    return path


def extract_release_archive(archive: Path, destination: Path, bundle_name: str) -> Path:
    """Safely extract a candidate archive without links or path traversal."""

    if destination.exists():
        raise P9QemuError(
            f"refusing to replace archive verification path: {destination}"
        )
    destination.mkdir()
    try:
        with tarfile.open(archive, mode="r:gz") as tar:
            seen: set[str] = set()
            for member in tar:
                relative = _safe_archive_member(member, bundle_name)
                normalized = relative.as_posix()
                if normalized in seen:
                    raise P9QemuError(
                        f"release archive contains a duplicate entry: {normalized}"
                    )
                seen.add(normalized)
                target = destination.joinpath(*relative.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=False)
                    continue
                if not target.parent.is_dir():
                    raise P9QemuError(
                        f"archive file appears before its parent directory: {normalized}"
                    )
                source = tar.extractfile(member)
                if source is None:
                    raise P9QemuError(f"could not read archive member: {normalized}")
                with source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
    except (OSError, tarfile.TarError, P9QemuError):
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return destination / bundle_name


def verify_extracted_bundle(bundle: Path) -> dict[str, object]:
    """Verify every manifest-bound artifact in an extracted bundle."""

    manifest_path = bundle / "manifest.json"
    manifest = load_json_object(manifest_path, "release manifest")
    if manifest.get("schema") != 1 or manifest.get("kind") != (
        "p9qemu-image-release-candidate"
    ):
        raise P9QemuError("unsupported extracted release manifest")
    artifacts = _mapping(manifest.get("artifacts"), "release artifacts")
    expected_paths = {"manifest.json"}
    for label, value in artifacts.items():
        record = _mapping(value, f"artifacts.{label}")
        relative_text = _string(record.get("path"), f"artifacts.{label}.path")
        relative = PurePosixPath(relative_text)
        if relative.is_absolute() or ".." in relative.parts:
            raise P9QemuError(f"release artifact path is unsafe: {relative_text}")
        path = bundle.joinpath(*relative.parts)
        if not path.is_file():
            raise P9QemuError(f"release artifact is missing after extraction: {path}")
        expected_size = record.get("size")
        if type(expected_size) is not int or path.stat().st_size != expected_size:
            raise P9QemuError(f"release artifact size mismatch: {relative_text}")
        expected_sha256 = _string(record.get("sha256"), f"artifacts.{label}.sha256")
        if sha256_file(path) != expected_sha256:
            raise P9QemuError(f"release artifact checksum mismatch: {relative_text}")
        expected_paths.add(relative.as_posix())
    actual_paths = {
        path.relative_to(bundle).as_posix()
        for path in bundle.rglob("*")
        if path.is_file()
    }
    if actual_paths != expected_paths:
        extras = sorted(actual_paths - expected_paths)
        missing = sorted(expected_paths - actual_paths)
        raise P9QemuError(
            f"release bundle file inventory mismatch; extras={extras}, missing={missing}"
        )
    image = _mapping(manifest.get("image"), "release image")
    image_relative = PurePosixPath(_string(image.get("path"), "image.path"))
    if image_relative.is_absolute() or ".." in image_relative.parts:
        raise P9QemuError(f"release image path is unsafe: {image_relative}")
    image_path = bundle.joinpath(*image_relative.parts)
    image_sha256 = sha256_file(image_path)
    if image_sha256 != image.get("sha256"):
        raise P9QemuError("release image checksum does not match its manifest")
    return {
        "status": "passed",
        "bundle_name": bundle.name,
        "files_verified": len(expected_paths),
        "image_sha256": image_sha256,
        "manifest_sha256": sha256_file(manifest_path),
    }


def _require_input_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise P9QemuError(f"{label} is not an existing file: {path}")


def _require_new_output(path: Path) -> None:
    if path.exists():
        raise P9QemuError(f"refusing to replace release-candidate output: {path}")
    if not path.parent.is_dir():
        raise P9QemuError(
            f"release-candidate parent directory does not exist: {path.parent}"
        )


def inspect_candidate_inputs(
    inputs: CandidateInputs,
) -> tuple[InstallAnswers, dict[str, Any], str, str, dict[str, Path]]:
    """Validate all candidate inputs without creating any output."""

    validate_identity(inputs.identity.image_id, inputs.identity.build_id)
    validate_source_commit(inputs.source_commit)
    if not inputs.image_hygiene_reviewed:
        raise P9QemuError("release candidate requires --confirm-image-hygiene-reviewed")
    for path, label in (
        (inputs.disk, "disk image"),
        (inputs.answers_path, "answer file"),
        (inputs.install_log, "install log"),
        (inputs.validation_manifest, "validation manifest"),
    ):
        _require_input_file(path, label)
    _require_new_output(inputs.output_dir)

    from p9qemu.answers import load_answers

    answers = load_answers(inputs.answers_path)
    image_sha256 = sha256_file(inputs.disk)
    answers_sha256 = sha256_file(inputs.answers_path)
    validation = load_json_object(inputs.validation_manifest, "validation manifest")
    _require_validation_checkpoints(
        validation,
        image_sha256=image_sha256,
        answers_sha256=answers_sha256,
    )
    manifest_answers = _mapping(validation.get("answers"), "answers")
    if manifest_answers.get("resolved") != asdict(answers):
        raise P9QemuError(
            "validation manifest resolved answers do not match the supplied answer file"
        )
    public_artifacts = public_validation_artifacts(
        validation, inputs.validation_manifest
    )
    for path in (inputs.answers_path, inputs.install_log, *public_artifacts.values()):
        scan_public_text([path], root=path.parent)
    return answers, validation, image_sha256, answers_sha256, public_artifacts


def build_release_candidate(inputs: CandidateInputs) -> CandidateResult:
    """Build and round-trip verify one local release candidate atomically."""

    answers, validation, image_sha256, _, validation_artifacts = (
        inspect_candidate_inputs(inputs)
    )
    temporary = inputs.output_dir.with_name(
        f".{inputs.output_dir.name}.p9qemu-{uuid4().hex}.part"
    )
    if temporary.exists():
        raise P9QemuError(f"temporary release path already exists: {temporary}")
    try:
        temporary.mkdir()
        bundle = temporary / inputs.identity.bundle_name
        bundle.mkdir()
        (bundle / "validation").mkdir()
        image_path = bundle / f"{inputs.identity.bundle_name}.qcow2"
        answers_path = bundle / "answers.toml"
        install_log = bundle / "install.raw.log"
        running_path = bundle / "RUNNING.md"
        public_validation_path = bundle / "validation" / "manifest.json"

        copy_file_new(inputs.disk, image_path)
        if sha256_file(image_path) != image_sha256:
            raise P9QemuError("copied release image digest does not match its source")
        copy_file_new(inputs.answers_path, answers_path)
        copy_file_new(inputs.install_log, install_log)
        for destination, source in validation_artifacts.items():
            copy_file_new(source, bundle / destination)

        private_validation_sha256 = sha256_file(inputs.validation_manifest)
        public_validation = sanitize_validation_manifest(
            validation,
            source_sha256=private_validation_sha256,
        )
        write_json_new(public_validation_path, public_validation)
        write_text_new(
            running_path,
            _running_document(image_path.name, inputs.identity),
        )

        text_paths = [
            path for path in bundle.rglob("*") if path.is_file() and path != image_path
        ]
        scanned_text = scan_public_text(text_paths, root=bundle)
        artifact_paths = [path for path in bundle.rglob("*") if path.is_file()]
        artifacts = _artifact_records(bundle, artifact_paths)
        release_manifest = _release_manifest(
            inputs=inputs,
            answers=answers,
            image_path=image_path,
            image_sha256=image_sha256,
            public_validation_path=public_validation_path,
            public_validation=public_validation,
            private_validation_sha256=private_validation_sha256,
            artifacts=artifacts,
            scanned_text=scanned_text,
        )
        manifest_path = bundle / "manifest.json"
        write_json_new(manifest_path, release_manifest)
        scan_public_text([manifest_path], root=bundle)

        archive = temporary / f"{inputs.identity.bundle_name}.tar.gz"
        create_deterministic_tar_gz(bundle, archive)
        archive_sha256 = sha256_file(archive)
        checksum = temporary / f"{archive.name}.sha256"
        write_text_new(checksum, f"{archive_sha256}  {archive.name}\n")

        verification_scratch = temporary / ".round-trip-verification"
        extracted = extract_release_archive(
            archive, verification_scratch, inputs.identity.bundle_name
        )
        verification = verify_extracted_bundle(extracted)
        if verification["image_sha256"] != image_sha256:
            raise P9QemuError("round-trip extracted image digest does not match source")
        shutil.rmtree(verification_scratch)
        verification.update(
            {
                "archive": archive.name,
                "archive_sha256": archive_sha256,
                "archive_size": archive.stat().st_size,
                "verified_at": utc_timestamp(),
            }
        )
        verification_path = temporary / "verification.json"
        write_json_new(verification_path, verification)

        os.replace(temporary, inputs.output_dir)
    except (OSError, P9QemuError, KeyboardInterrupt):
        shutil.rmtree(temporary, ignore_errors=True)
        raise

    final_bundle = inputs.output_dir / inputs.identity.bundle_name
    return CandidateResult(
        output_dir=inputs.output_dir,
        bundle_dir=final_bundle,
        archive=inputs.output_dir / f"{inputs.identity.bundle_name}.tar.gz",
        archive_sha256=archive_sha256,
        image_sha256=image_sha256,
        manifest=final_bundle / "manifest.json",
        verification=inputs.output_dir / "verification.json",
    )
