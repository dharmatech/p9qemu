"""Fail-closed release-candidate packaging for the Drawterm derivative."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
import os
from pathlib import Path
import shutil
from typing import Any
from uuid import uuid4

from p9qemu import __version__
from p9qemu.drawterm_postinstall import (
    DrawtermPostinstallProfile,
    load_drawterm_postinstall_profile,
)
from p9qemu.errors import P9QemuError
from p9qemu.media import sha256_file
from p9qemu.provenance import artifact_record, utc_timestamp, write_json_new, write_text_new
from p9qemu.ready_image import load_ready_image_manifest
from p9qemu.release_candidate import (
    CandidateIdentity,
    CandidateResult,
    copy_file_new,
    create_deterministic_tar_gz,
    extract_release_archive,
    load_json_object,
    scan_public_text,
    validate_identity,
    validate_source_commit,
    verify_extracted_bundle,
)


_CORE_CHECKS = {
    "unattended-boot",
    "root-filesystem",
    "serial-diagnostics",
    "loopback-services",
    "drawterm-authentication",
    "guest-user",
    "system-name",
    "guest-home",
    "timezone",
    "plan9-ini",
    "network-ping",
    "drawterm-session-attempts",
    "orderly-shutdown",
    "port-release",
}
_ROTATION_CHECKS = {
    "nvram-password-write",
    "mutation-session-exit",
    "mutation-shutdown",
    "old-password-rejected",
    "new-password-accepted",
    "verification-cold-boot",
    "verification-shutdown",
    "port-release",
}
_PREPARATION_ARTIFACTS = {
    "plan9_ini_before": "preparation/plan9.ini.before.txt",
    "plan9_ini_after": "preparation/plan9.ini.after.txt",
    "qemu_img_check_input": "preparation/qemu-img-check-input.txt",
    "qemu_img_check_output": "preparation/qemu-img-check-output.txt",
}
_VALIDATION_ARTIFACTS = {
    "base_qemu_img_check_before": "validation/qemu-img-check-before.txt",
    "base_qemu_img_check_after": "validation/qemu-img-check-after.txt",
}
_ROTATION_ARTIFACTS = {
    "base_qemu_img_check_before": "security/qemu-img-check-before.txt",
    "base_qemu_img_check_after": "security/qemu-img-check-after.txt",
    "overlay_qemu_img_check": "security/overlay-qemu-img-check.txt",
}


@dataclass(frozen=True)
class DrawtermCandidateInputs:
    """Exact inputs for one Drawterm-ready release candidate."""

    identity: CandidateIdentity
    source_commit: str
    disk: Path
    postinstall_profile_path: Path
    parent_manifest_path: Path
    preparation_manifest: Path
    validation_manifest: Path
    password_rotation_manifest: Path
    output_dir: Path
    image_hygiene_reviewed: bool


@dataclass(frozen=True)
class _Inspected:
    profile: DrawtermPostinstallProfile
    profile_sha256: str
    image_sha256: str
    image_info: dict[str, object]
    preparation: dict[str, Any]
    validation: dict[str, Any]
    rotation: dict[str, Any]
    preparation_artifacts: dict[str, Path]
    validation_artifacts: dict[str, Path]
    rotation_artifacts: dict[str, Path]


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise P9QemuError(f"Drawterm candidate requires an object at {label}")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise P9QemuError(f"Drawterm candidate requires text at {label}")
    return value


def _require_manifest(document: Mapping[str, Any], *, kind: str, label: str) -> None:
    if document.get("schema") != 1 or document.get("kind") != kind:
        raise P9QemuError(f"unsupported {label} schema or kind")
    if document.get("status") != "passed" or document.get("error") is not None:
        raise P9QemuError(f"Drawterm candidate requires passed {label}")


def _profile_binding(
    document: Mapping[str, Any], *, profile: DrawtermPostinstallProfile, digest: str
) -> Mapping[str, Any]:
    binding = _mapping(document.get("postinstall_profile"), "postinstall_profile")
    if binding.get("profile_id") != profile.profile_id or binding.get("sha256") != digest:
        raise P9QemuError("evidence is not bound to the selected post-install profile")
    if binding.get("credential_class") != profile.nvram.credential_class:
        raise P9QemuError("evidence credential class does not match the profile")
    if binding.get("password_redacted") is not True:
        raise P9QemuError("evidence did not redact the demonstration credential")
    return binding


def _clean_image_info(value: object, *, label: str) -> dict[str, object]:
    info = _mapping(value, label)
    if info.get("format") != "qcow2" or info.get("dirty-flag") is not False:
        raise P9QemuError(f"{label} does not describe a clean QCOW2 image")
    virtual_size = info.get("virtual-size")
    if type(virtual_size) is not int or virtual_size <= 0:
        raise P9QemuError(f"{label} requires a positive virtual size")
    for name in ("backing-filename", "full-backing-filename", "backing-filename-format"):
        if info.get(name):
            raise P9QemuError("Drawterm release image must be standalone")
    result = {
        name: info[name]
        for name in ("format", "virtual-size", "actual-size", "cluster-size", "dirty-flag")
        if name in info
    }
    format_specific = info.get("format-specific")
    if isinstance(format_specific, dict):
        data = format_specific.get("data")
        clean_data = {}
        if isinstance(data, dict):
            clean_data = {
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
            "data": clean_data,
        }
    return result


def _require_unchanged_image(
    document: Mapping[str, Any], *, expected_sha256: str, label: str
) -> dict[str, object]:
    image = _mapping(document.get("image"), f"{label}.image")
    hashes = (
        image.get("expected_sha256"),
        image.get("sha256_before"),
        image.get("sha256_after"),
    )
    if image.get("unchanged") is not True or any(value != expected_sha256 for value in hashes):
        raise P9QemuError(f"{label} did not preserve the exact derivative image")
    return _clean_image_info(image.get("qemu_img_info"), label=f"{label}.image.qemu_img_info")


def _require_removed_overlay(document: Mapping[str, Any], *, label: str) -> None:
    overlay = _mapping(document.get("overlay"), f"{label}.overlay")
    if overlay.get("removed") is not True or overlay.get("exists") is not False:
        raise P9QemuError(f"{label} did not remove its disposable overlay")


def _require_checks(document: Mapping[str, Any], expected: set[str], *, label: str) -> None:
    checks = document.get("checks")
    if not isinstance(checks, list) or not checks:
        raise P9QemuError(f"{label} contains no checks")
    names = {
        check.get("name")
        for check in checks
        if isinstance(check, dict) and isinstance(check.get("name"), str)
    }
    missing = sorted(expected - names)
    if missing:
        raise P9QemuError(f"{label} is missing required checks: {missing}")


def _drawterm_identity(document: Mapping[str, Any], *, label: str) -> tuple[str, str]:
    drawterm = _mapping(document.get("drawterm"), f"{label}.drawterm")
    source_commit = validate_source_commit(
        _text(drawterm.get("source_commit"), f"{label}.drawterm.source_commit")
    )
    executable_sha256 = _text(
        drawterm.get("executable_sha256"), f"{label}.drawterm.executable_sha256"
    )
    if len(executable_sha256) != 64 or any(c not in "0123456789abcdef" for c in executable_sha256):
        raise P9QemuError("Drawterm executable digest is not lowercase SHA-256")
    return source_commit, executable_sha256


def _artifact_paths(
    document: Mapping[str, Any], manifest_path: Path, selected: Mapping[str, str]
) -> dict[str, Path]:
    artifacts = _mapping(document.get("artifacts"), "artifacts")
    result: dict[str, Path] = {}
    for name, destination in selected.items():
        record = _mapping(artifacts.get(name), f"artifacts.{name}")
        relative = _text(record.get("path"), f"artifacts.{name}.path")
        source = manifest_path.parent / relative
        if Path(relative).is_absolute() or ".." in Path(relative).parts or not source.is_file():
            raise P9QemuError(f"unsafe or missing evidence artifact: {relative}")
        if source.stat().st_size != record.get("size") or sha256_file(source) != record.get("sha256"):
            raise P9QemuError(f"evidence artifact does not match its manifest: {relative}")
        result[destination] = source
    return result


def inspect_drawterm_candidate_inputs(inputs: DrawtermCandidateInputs) -> _Inspected:
    validate_identity(inputs.identity.image_id, inputs.identity.build_id)
    validate_source_commit(inputs.source_commit)
    if not inputs.image_hygiene_reviewed:
        raise P9QemuError("Drawterm candidate requires --confirm-image-hygiene-reviewed")
    for path, label in (
        (inputs.disk, "disk image"),
        (inputs.postinstall_profile_path, "post-install profile"),
        (inputs.parent_manifest_path, "parent manifest"),
        (inputs.preparation_manifest, "preparation manifest"),
        (inputs.validation_manifest, "Drawterm validation manifest"),
        (inputs.password_rotation_manifest, "password-rotation manifest"),
    ):
        if not path.is_file():
            raise P9QemuError(f"{label} is not an existing file: {path}")
    if inputs.output_dir.exists() or not inputs.output_dir.parent.is_dir():
        raise P9QemuError(f"refusing to replace or create outside an existing parent: {inputs.output_dir}")

    profile = load_drawterm_postinstall_profile(inputs.postinstall_profile_path)
    profile_sha256 = sha256_file(inputs.postinstall_profile_path)
    parent = load_ready_image_manifest(inputs.parent_manifest_path)
    if sha256_file(inputs.parent_manifest_path) != profile.parent.manifest_sha256:
        raise P9QemuError("parent manifest digest does not match the post-install profile")
    if parent.image_id != profile.parent.image_id or parent.image.sha256 != profile.parent.image_sha256:
        raise P9QemuError("parent manifest identity does not match the post-install profile")

    image_sha256 = sha256_file(inputs.disk)
    preparation = load_json_object(inputs.preparation_manifest, "preparation manifest")
    _require_manifest(preparation, kind="p9qemu-image-postinstall-preparation", label="preparation")
    _profile_binding(preparation, profile=profile, digest=profile_sha256)
    parent_record = _mapping(preparation.get("parent_manifest"), "preparation.parent_manifest")
    if (
        parent_record.get("id") != profile.parent.image_id
        or parent_record.get("sha256") != profile.parent.manifest_sha256
        or parent_record.get("resolved") != asdict(profile.parent)
    ):
        raise P9QemuError("preparation parent provenance does not match the profile")
    prep_image = _mapping(preparation.get("image"), "preparation.image")
    prep_input = _mapping(prep_image.get("input"), "preparation.image.input")
    prep_output = _mapping(prep_image.get("output"), "preparation.image.output")
    if (
        prep_input.get("sha256") != profile.parent.image_sha256
        or prep_input.get("sha256_after") != profile.parent.image_sha256
        or prep_input.get("unchanged") is not True
        or prep_output.get("sha256") != image_sha256
        or prep_image.get("changed") is not True
    ):
        raise P9QemuError("preparation image digest chain is invalid")
    _clean_image_info(prep_input.get("qemu_img_info"), label="preparation.image.input.qemu_img_info")
    image_info = _clean_image_info(prep_output.get("qemu_img_info"), label="preparation.image.output.qemu_img_info")

    validation = load_json_object(inputs.validation_manifest, "Drawterm validation manifest")
    _require_manifest(validation, kind="p9qemu-drawterm-image-validation", label="Drawterm validation")
    _profile_binding(validation, profile=profile, digest=profile_sha256)
    validation_info = _require_unchanged_image(validation, expected_sha256=image_sha256, label="Drawterm validation")
    if validation_info != image_info:
        raise P9QemuError("preparation and validation image metadata differ")
    _require_removed_overlay(validation, label="Drawterm validation")
    _require_checks(validation, _CORE_CHECKS, label="Drawterm validation")
    if validation.get("network_check") != "required":
        raise P9QemuError("Drawterm validation did not require networking")

    rotation = load_json_object(inputs.password_rotation_manifest, "password-rotation manifest")
    _require_manifest(rotation, kind="p9qemu-drawterm-password-rotation-validation", label="password rotation")
    _profile_binding(rotation, profile=profile, digest=profile_sha256)
    rotation_info = _require_unchanged_image(rotation, expected_sha256=image_sha256, label="password rotation")
    if rotation_info != image_info:
        raise P9QemuError("password rotation and validation image metadata differ")
    _require_removed_overlay(rotation, label="password rotation")
    rotation_overlay = _mapping(rotation.get("overlay"), "password rotation.overlay")
    if rotation_overlay.get("retained_on_failure") is not False:
        raise P9QemuError("password rotation did not prove overlay cleanup policy")
    _require_checks(rotation, _ROTATION_CHECKS, label="password rotation")
    rotation_drawterm = _mapping(rotation.get("drawterm"), "password rotation.drawterm")
    if (
        rotation_drawterm.get("passwords_redacted") is not True
        or rotation_drawterm.get("replacement_generated") is not True
        or rotation_drawterm.get("replacement_recorded") is not False
    ):
        raise P9QemuError("password-rotation evidence did not preserve credential secrecy")
    if _drawterm_identity(validation, label="validation") != _drawterm_identity(rotation, label="rotation"):
        raise P9QemuError("Drawterm source identity differs between validation gates")

    return _Inspected(
        profile=profile,
        profile_sha256=profile_sha256,
        image_sha256=image_sha256,
        image_info=image_info,
        preparation=preparation,
        validation=validation,
        rotation=rotation,
        preparation_artifacts=_artifact_paths(preparation, inputs.preparation_manifest, _PREPARATION_ARTIFACTS),
        validation_artifacts=_artifact_paths(validation, inputs.validation_manifest, _VALIDATION_ARTIFACTS),
        rotation_artifacts=_artifact_paths(rotation, inputs.password_rotation_manifest, _ROTATION_ARTIFACTS),
    )


def _host(document: Mapping[str, Any]) -> dict[str, object]:
    host = _mapping(document.get("host"), "host")
    return {name: host.get(name) for name in ("system", "distribution_id", "version_id")}


def _qemu(document: Mapping[str, Any]) -> dict[str, object]:
    qemu = _mapping(document.get("qemu"), "qemu")
    return {
        name: qemu.get(name)
        for name in ("system_version", "img_version", "acceleration", "memory_mib", "serial_input")
        if name in qemu
    }


def _public_preparation(inspected: _Inspected, source_sha256: str) -> dict[str, object]:
    document = inspected.preparation
    image = _mapping(document.get("image"), "preparation.image")
    input_image = _mapping(image.get("input"), "preparation.image.input")
    output_image = _mapping(image.get("output"), "preparation.image.output")
    p9qemu = _mapping(document.get("p9qemu"), "preparation.p9qemu")
    return {
        "schema": 1,
        "kind": "p9qemu-public-image-postinstall-preparation",
        "source_manifest_sha256": source_sha256,
        "status": "passed",
        "started_at": document.get("started_at"),
        "completed_at": document.get("completed_at"),
        "p9qemu": {"commit": p9qemu.get("commit")},
        "postinstall_profile": {
            "profile_id": inspected.profile.profile_id,
            "sha256": inspected.profile_sha256,
            "credential_class": inspected.profile.nvram.credential_class,
            "password_redacted": True,
        },
        "parent": asdict(inspected.profile.parent),
        "image": {
            "input": {
                "sha256": input_image.get("sha256"),
                "unchanged": input_image.get("unchanged"),
                "qemu_img_info": _clean_image_info(input_image.get("qemu_img_info"), label="preparation.input.info"),
                "qemu_img_check": "passed",
            },
            "output": {
                "sha256": output_image.get("sha256"),
                "qemu_img_info": inspected.image_info,
                "qemu_img_check": "passed",
            },
            "changed": image.get("changed"),
        },
        "host": _host(document),
        "qemu": _qemu(document),
    }


def _public_validation(inspected: _Inspected, source_sha256: str) -> dict[str, object]:
    document = inspected.validation
    p9qemu = _mapping(document.get("p9qemu"), "validation.p9qemu")
    drawterm = _mapping(document.get("drawterm"), "validation.drawterm")
    overlay = _mapping(document.get("overlay"), "validation.overlay")
    return {
        "schema": 1,
        "kind": "p9qemu-public-drawterm-image-validation",
        "source_manifest_sha256": source_sha256,
        "status": "passed",
        "started_at": document.get("started_at"),
        "completed_at": document.get("completed_at"),
        "p9qemu": {"commit": p9qemu.get("commit")},
        "postinstall_profile": {"profile_id": inspected.profile.profile_id, "sha256": inspected.profile_sha256},
        "image": {"sha256": inspected.image_sha256, "unchanged": True, "qemu_img_info": inspected.image_info},
        "overlay": {"exists": overlay.get("exists"), "removed": overlay.get("removed")},
        "host": _host(document),
        "qemu": _qemu(document),
        "drawterm": {
            "source_commit": drawterm.get("source_commit"),
            "executable_sha256": drawterm.get("executable_sha256"),
            "password_redacted": drawterm.get("password_redacted"),
            "password_transport": drawterm.get("password_transport"),
        },
        "network_check": document.get("network_check"),
        "checks": document.get("checks"),
    }


def _public_rotation(inspected: _Inspected, source_sha256: str) -> dict[str, object]:
    document = inspected.rotation
    p9qemu = _mapping(document.get("p9qemu"), "rotation.p9qemu")
    drawterm = _mapping(document.get("drawterm"), "rotation.drawterm")
    overlay = _mapping(document.get("overlay"), "rotation.overlay")
    return {
        "schema": 1,
        "kind": "p9qemu-public-drawterm-password-rotation-validation",
        "source_manifest_sha256": source_sha256,
        "status": "passed",
        "started_at": document.get("started_at"),
        "completed_at": document.get("completed_at"),
        "p9qemu": {"commit": p9qemu.get("commit")},
        "postinstall_profile": {"profile_id": inspected.profile.profile_id, "sha256": inspected.profile_sha256},
        "image": {"sha256": inspected.image_sha256, "unchanged": True, "qemu_img_info": inspected.image_info},
        "overlay": {
            "exists": overlay.get("exists"),
            "removed": overlay.get("removed"),
            "retained_on_failure": overlay.get("retained_on_failure"),
        },
        "host": _host(document),
        "qemu": _qemu(document),
        "drawterm": {
            name: drawterm.get(name)
            for name in (
                "source_commit",
                "executable_sha256",
                "passwords_redacted",
                "replacement_format",
                "replacement_generated",
                "replacement_recorded",
                "old_password_transport",
                "new_password_transport",
            )
        },
        "checks": document.get("checks"),
    }


def _running_document(identity: CandidateIdentity, profile: DrawtermPostinstallProfile) -> str:
    tag = f"ready-{identity.image_id}-{identity.build_id}"
    manifest_url = f"https://github.com/dharmatech/p9qemu/releases/download/{tag}/image.json"
    return f"""# Run {identity.bundle_name}

This image boots unattended as a 9front CPU/auth server for Drawterm. Its
credential is intentionally public and is safe only with the default
loopback-only host forwarding.

```console
p9qemu image create {manifest_url} INSTANCE
p9qemu start --instance INSTANCE
```

Connect as `{profile.nvram.authid}` with the public demonstration credential
`{profile.nvram.password}`. The P9QEMU runtime maps CPU to
`127.0.0.1:{profile.drawterm.cpu_host_port}` and auth to
`127.0.0.1:{profile.drawterm.auth_host_port}`. Do not bridge this image or
expose either service beyond loopback until the credential is changed.

The exact automated post-install inputs are recorded in `postinstall.json`.
The path-free preparation, cold-boot/Drawterm, and password-rotation records
are under `preparation/`, `validation/`, and `security/`.
"""


def _artifact_records(bundle: Path, paths: Sequence[Path]) -> dict[str, dict[str, object]]:
    return {
        path.relative_to(bundle).as_posix(): artifact_record(path, root=bundle)
        for path in sorted(paths)
    }


def build_drawterm_release_candidate(inputs: DrawtermCandidateInputs) -> CandidateResult:
    """Build and round-trip verify one sanitized Drawterm candidate atomically."""

    inspected = inspect_drawterm_candidate_inputs(inputs)
    temporary = inputs.output_dir.with_name(f".{inputs.output_dir.name}.p9qemu-{uuid4().hex}.part")
    try:
        temporary.mkdir()
        bundle = temporary / inputs.identity.bundle_name
        for relative in ("parent", "preparation", "validation", "security"):
            (bundle / relative).mkdir(parents=True, exist_ok=True)
        image_path = bundle / f"{inputs.identity.bundle_name}.qcow2"
        copy_file_new(inputs.disk, image_path)
        if sha256_file(image_path) != inspected.image_sha256:
            raise P9QemuError("copied Drawterm image digest does not match its source")
        copy_file_new(inputs.postinstall_profile_path, bundle / "postinstall.json")
        copy_file_new(inputs.parent_manifest_path, bundle / "parent" / "image.json")
        write_text_new(bundle / "RUNNING.md", _running_document(inputs.identity, inspected.profile))

        for mapping in (
            inspected.preparation_artifacts,
            inspected.validation_artifacts,
            inspected.rotation_artifacts,
        ):
            for destination, source in mapping.items():
                copy_file_new(source, bundle / destination)

        prep_sha = sha256_file(inputs.preparation_manifest)
        validation_sha = sha256_file(inputs.validation_manifest)
        rotation_sha = sha256_file(inputs.password_rotation_manifest)
        write_json_new(bundle / "preparation" / "manifest.json", _public_preparation(inspected, prep_sha))
        write_json_new(bundle / "validation" / "manifest.json", _public_validation(inspected, validation_sha))
        write_json_new(bundle / "security" / "password-rotation.json", _public_rotation(inspected, rotation_sha))

        controlled_public_demo = {bundle / "postinstall.json", bundle / "RUNNING.md"}
        scanned_paths = [
            path
            for path in bundle.rglob("*")
            if path.is_file() and path != image_path and path not in controlled_public_demo
        ]
        scanned_text = scan_public_text(scanned_paths, root=bundle)
        artifacts = _artifact_records(
            bundle, [path for path in bundle.rglob("*") if path.is_file()]
        )
        validation_drawterm = _mapping(inspected.validation.get("drawterm"), "validation.drawterm")
        manifest = {
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
                "runtime_profile": inspected.profile.profile_id,
                "postinstall_profile_sha256": inspected.profile_sha256,
                "parent_image_id": inspected.profile.parent.image_id,
                "parent_manifest_sha256": inspected.profile.parent.manifest_sha256,
                "parent_image_sha256": inspected.profile.parent.image_sha256,
                "preparation_commit": _mapping(inspected.preparation.get("p9qemu"), "preparation.p9qemu").get("commit"),
                "validation_commit": _mapping(inspected.validation.get("p9qemu"), "validation.p9qemu").get("commit"),
                "password_rotation_commit": _mapping(inspected.rotation.get("p9qemu"), "rotation.p9qemu").get("commit"),
                "drawterm_commit": validation_drawterm.get("source_commit"),
                "drawterm_executable_sha256": validation_drawterm.get("executable_sha256"),
            },
            "image": {
                "path": image_path.name,
                "format": "qcow2",
                "virtual_size": inspected.image_info["virtual-size"],
                "stored_size": image_path.stat().st_size,
                "sha256": inspected.image_sha256,
            },
            "installation": {
                "status": "passed",
                "inherited_from": inspected.profile.parent.image_id,
                "parent_manifest_sha256": inspected.profile.parent.manifest_sha256,
            },
            "preparation": {
                "path": "preparation/manifest.json",
                "status": "passed",
                "private_source_manifest_sha256": prep_sha,
            },
            "validation": {
                "path": "validation/manifest.json",
                "status": "passed",
                "private_source_manifest_sha256": validation_sha,
            },
            "security_validation": {
                "path": "security/password-rotation.json",
                "status": "passed",
                "private_source_manifest_sha256": rotation_sha,
            },
            "runtime_profile": {
                "guest": {
                    "user": inspected.profile.guest.user,
                    "system_name": inspected.profile.guest.system_name,
                    "root_partition": inspected.profile.guest.root_partition,
                },
                "boot": dict(inspected.profile.plan9_ini.target_required),
                "drawterm": {
                    "bind_address": inspected.profile.drawterm.bind_address,
                    "cpu_host_port": inspected.profile.drawterm.cpu_host_port,
                    "auth_host_port": inspected.profile.drawterm.auth_host_port,
                    "credential_class": inspected.profile.nvram.credential_class,
                },
            },
            "hygiene": {
                "image_contents_review_confirmed": inputs.image_hygiene_reviewed,
                "public_text_scan": "passed",
                "scanned_text_files": list(scanned_text),
                "controlled_public_demo_files": ["RUNNING.md", "postinstall.json"],
                "scope": (
                    "The strict post-install profile and generated RUNNING.md intentionally "
                    "name the public demonstration credential; all other public text was "
                    "scanned for host paths and secret-like material."
                ),
            },
            "artifacts": artifacts,
            "publication": {"uploaded": False, "asset_replacement_permitted": False},
        }
        manifest_path = bundle / "manifest.json"
        write_json_new(manifest_path, manifest)
        scan_public_text([manifest_path], root=bundle)

        archive = temporary / f"{inputs.identity.bundle_name}.tar.gz"
        create_deterministic_tar_gz(bundle, archive)
        archive_sha256 = sha256_file(archive)
        write_text_new(temporary / f"{archive.name}.sha256", f"{archive_sha256}  {archive.name}\n")
        scratch = temporary / ".round-trip-verification"
        extracted = extract_release_archive(archive, scratch, inputs.identity.bundle_name)
        verification = verify_extracted_bundle(extracted)
        if verification["image_sha256"] != inspected.image_sha256:
            raise P9QemuError("round-trip Drawterm image digest does not match source")
        shutil.rmtree(scratch)
        verification.update(
            {
                "archive": archive.name,
                "archive_sha256": archive_sha256,
                "archive_size": archive.stat().st_size,
                "verified_at": utc_timestamp(),
            }
        )
        write_json_new(temporary / "verification.json", verification)
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
        image_sha256=inspected.image_sha256,
        manifest=final_bundle / "manifest.json",
        verification=inputs.output_dir / "verification.json",
    )


__all__ = [
    "DrawtermCandidateInputs",
    "build_drawterm_release_candidate",
    "inspect_drawterm_candidate_inputs",
]
