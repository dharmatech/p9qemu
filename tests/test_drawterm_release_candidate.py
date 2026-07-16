from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

import pytest

from p9qemu.drawterm_postinstall import load_drawterm_postinstall_profile
from p9qemu.drawterm_release_candidate import (
    DrawtermCandidateInputs,
    build_drawterm_release_candidate,
)
from p9qemu.errors import P9QemuError
from p9qemu.media import sha256_file
from p9qemu.release_candidate import validate_identity


ROOT = Path(__file__).parents[1]
PROFILE = (
    ROOT
    / "images"
    / "p9qemu-9front-11554-amd64-hjfs-gmt-drawterm-001"
    / "postinstall.json"
)
PARENT = ROOT / "images" / "manifests" / "p9qemu-9front-11554-amd64-hjfs-gmt-002.json"
CORE_CHECKS = (
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
)
ROTATION_CHECKS = (
    "nvram-password-write",
    "mutation-session-exit",
    "mutation-shutdown",
    "old-password-rejected",
    "new-password-accepted",
    "verification-cold-boot",
    "verification-shutdown",
    "port-release",
)


def _artifact(path: Path, *, root: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _write_artifacts(root: Path, names: tuple[str, ...]) -> dict[str, dict[str, object]]:
    root.mkdir()
    result = {}
    for name in names:
        path = root / f"{name}.txt"
        path.write_text("No errors were found on the image.\n", encoding="utf-8")
        result[name] = _artifact(path, root=root)
    return result


def _inputs(tmp_path: Path) -> DrawtermCandidateInputs:
    profile = load_drawterm_postinstall_profile(PROFILE)
    profile_sha256 = sha256_file(PROFILE)
    disk = tmp_path / "drawterm.qcow2"
    disk.write_bytes(b"synthetic standalone drawterm qcow2\0" * 64)
    image_sha256 = sha256_file(disk)
    image_info = {
        "filename": "/home/developer/private/drawterm.qcow2",
        "format": "qcow2",
        "virtual-size": 4096,
        "actual-size": disk.stat().st_size,
        "cluster-size": 65536,
        "dirty-flag": False,
        "format-specific": {
            "type": "qcow2",
            "data": {"compat": "1.1", "corrupt": False},
        },
    }
    profile_binding = {
        "profile_id": profile.profile_id,
        "sha256": profile_sha256,
        "credential_class": "public-demo",
        "password_redacted": True,
    }
    host = {"system": "Linux", "distribution_id": "ubuntu", "version_id": "22.04"}
    qemu = {
        "system_version": "QEMU 9",
        "img_version": "qemu-img 9",
        "acceleration": "KVM",
        "memory_mib": 2048,
        "serial_input": False,
    }
    drawterm = {
        "source_commit": "d" * 40,
        "executable_sha256": "e" * 64,
        "password_redacted": True,
        "password_transport": "PASS environment",
    }

    prep_root = tmp_path / "preparation"
    prep_artifacts = _write_artifacts(
        prep_root,
        (
            "plan9_ini_before",
            "plan9_ini_after",
            "qemu_img_check_input",
            "qemu_img_check_output",
        ),
    )
    preparation = {
        "schema": 1,
        "kind": "p9qemu-image-postinstall-preparation",
        "status": "passed",
        "started_at": "2026-07-15T00:00:00Z",
        "completed_at": "2026-07-15T00:01:00Z",
        "p9qemu": {"commit": "a" * 40},
        "postinstall_profile": profile_binding,
        "parent_manifest": {
            "id": profile.parent.image_id,
            "sha256": profile.parent.manifest_sha256,
            "resolved": asdict(profile.parent),
        },
        "image": {
            "input": {
                "sha256": profile.parent.image_sha256,
                "sha256_after": profile.parent.image_sha256,
                "unchanged": True,
                "qemu_img_info": image_info,
            },
            "output": {"sha256": image_sha256, "qemu_img_info": image_info},
            "changed": True,
        },
        "host": host,
        "qemu": qemu,
        "artifacts": prep_artifacts,
    }
    preparation_manifest = prep_root / "manifest.json"
    preparation_manifest.write_text(json.dumps(preparation), encoding="utf-8")

    validation_root = tmp_path / "validation"
    validation_artifacts = _write_artifacts(
        validation_root,
        ("base_qemu_img_check_before", "base_qemu_img_check_after"),
    )
    validation = {
        "schema": 1,
        "kind": "p9qemu-drawterm-image-validation",
        "status": "passed",
        "error": None,
        "started_at": "2026-07-15T00:02:00Z",
        "completed_at": "2026-07-15T00:03:00Z",
        "p9qemu": {"commit": "b" * 40},
        "postinstall_profile": profile_binding,
        "image": {
            "expected_sha256": image_sha256,
            "sha256_before": image_sha256,
            "sha256_after": image_sha256,
            "unchanged": True,
            "qemu_img_info": image_info,
        },
        "overlay": {"exists": False, "removed": True},
        "host": host,
        "qemu": qemu,
        "drawterm": drawterm,
        "network_check": "required",
        "checks": [{"name": name, "detail": "passed"} for name in CORE_CHECKS],
        "artifacts": validation_artifacts,
    }
    validation_manifest = validation_root / "manifest.json"
    validation_manifest.write_text(json.dumps(validation), encoding="utf-8")

    rotation_root = tmp_path / "rotation"
    rotation_artifacts = _write_artifacts(
        rotation_root,
        (
            "base_qemu_img_check_before",
            "base_qemu_img_check_after",
            "overlay_qemu_img_check",
        ),
    )
    rotation_drawterm = {
        **drawterm,
        "passwords_redacted": True,
        "replacement_format": "24 lowercase hexadecimal characters",
        "replacement_generated": True,
        "replacement_recorded": False,
        "old_password_transport": "PASS environment",
        "new_password_transport": "stdin then PASS environment",
    }
    rotation = {
        "schema": 1,
        "kind": "p9qemu-drawterm-password-rotation-validation",
        "status": "passed",
        "error": None,
        "started_at": "2026-07-15T00:04:00Z",
        "completed_at": "2026-07-15T00:05:00Z",
        "p9qemu": {"commit": "c" * 40},
        "postinstall_profile": profile_binding,
        "image": {
            "expected_sha256": image_sha256,
            "sha256_before": image_sha256,
            "sha256_after": image_sha256,
            "unchanged": True,
            "qemu_img_info": image_info,
        },
        "overlay": {"exists": False, "removed": True, "retained_on_failure": False},
        "host": host,
        "qemu": qemu,
        "drawterm": rotation_drawterm,
        "checks": [{"name": name, "detail": "passed"} for name in ROTATION_CHECKS],
        "artifacts": rotation_artifacts,
    }
    rotation_manifest = rotation_root / "manifest.json"
    rotation_manifest.write_text(json.dumps(rotation), encoding="utf-8")

    return DrawtermCandidateInputs(
        identity=validate_identity("9front-11554-amd64-hjfs-gmt-drawterm", "001"),
        source_commit="f" * 40,
        disk=disk,
        postinstall_profile_path=PROFILE,
        parent_manifest_path=PARENT,
        preparation_manifest=preparation_manifest,
        validation_manifest=validation_manifest,
        password_rotation_manifest=rotation_manifest,
        output_dir=tmp_path / "candidate",
        image_hygiene_reviewed=True,
    )


def test_drawterm_candidate_is_sanitized_and_round_trip_verified(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    result = build_drawterm_release_candidate(inputs)

    assert result.archive_sha256 == sha256_file(result.archive)
    verification = json.loads(result.verification.read_text(encoding="utf-8"))
    assert verification["status"] == "passed"
    assert verification["image_sha256"] == sha256_file(inputs.disk)

    internal = json.loads(result.manifest.read_text(encoding="utf-8"))
    serialized_internal = json.dumps(internal)
    assert internal["installation"]["status"] == "passed"
    assert internal["preparation"]["status"] == "passed"
    assert internal["validation"]["status"] == "passed"
    assert internal["security_validation"]["status"] == "passed"
    assert internal["source"]["parent_image_sha256"] == (
        load_drawterm_postinstall_profile(PROFILE).parent.image_sha256
    )
    assert "/home/developer" not in serialized_internal
    assert "p9qemu-demo" not in serialized_internal

    public_validation = json.loads(
        (result.bundle_dir / "validation" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    serialized_validation = json.dumps(public_validation)
    assert "/home/developer" not in serialized_validation
    assert "filename" not in public_validation["image"]["qemu_img_info"]
    assert "p9qemu-demo" not in serialized_validation

    public_rotation = json.loads(
        (result.bundle_dir / "security" / "password-rotation.json").read_text(
            encoding="utf-8"
        )
    )
    assert public_rotation["drawterm"]["replacement_recorded"] is False
    assert "commands" not in public_rotation["drawterm"]
    assert not (result.bundle_dir / "security" / "mutation-boot.raw.log").exists()
    assert "p9qemu-demo" in (result.bundle_dir / "RUNNING.md").read_text(
        encoding="utf-8"
    )
    assert "p9qemu-demo" in (result.bundle_dir / "postinstall.json").read_text(
        encoding="utf-8"
    )


def test_drawterm_candidate_rejects_failed_password_rotation(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    document = json.loads(inputs.password_rotation_manifest.read_text(encoding="utf-8"))
    document["status"] = "failed"
    inputs.password_rotation_manifest.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(P9QemuError, match="passed password rotation"):
        build_drawterm_release_candidate(inputs)


def test_drawterm_candidate_rejects_changed_derivative(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    inputs.disk.write_bytes(inputs.disk.read_bytes() + b"changed")
    with pytest.raises(P9QemuError, match="digest chain is invalid"):
        build_drawterm_release_candidate(inputs)


def test_drawterm_candidate_never_replaces_output(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    inputs.output_dir.mkdir()
    with pytest.raises(P9QemuError, match="refusing to replace"):
        build_drawterm_release_candidate(inputs)
