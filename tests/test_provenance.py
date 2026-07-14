from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from p9qemu.answers import load_answers
from p9qemu.errors import P9QemuError
from p9qemu.host import Acceleration, HostInfo
from p9qemu.provenance import (
    artifact_record,
    build_validation_manifest,
    qemu_img_check,
    qemu_img_info,
    query_tool_version,
    require_unchanged_image,
    write_json_new,
    write_text_new,
)
from p9qemu.validation import GuestValidationResult, ValidationCheck


ROOT = Path(__file__).parents[1]
REFERENCE_ANSWERS = (
    ROOT / "images" / "9front-11554-amd64-hjfs-manual-001" / "answers.toml"
)


def test_atomic_provenance_write_never_replaces_existing_file(tmp_path: Path) -> None:
    destination = tmp_path / "manifest.json"
    write_json_new(destination, {"schema": 1})
    with pytest.raises(P9QemuError, match="refusing to replace"):
        write_json_new(destination, {"schema": 2})
    assert destination.read_text(encoding="utf-8") == '{\n  "schema": 1\n}\n'


def test_artifact_record_is_relative_and_digest_bound(tmp_path: Path) -> None:
    artifact = tmp_path / "nested" / "console.raw.log"
    artifact.parent.mkdir()
    artifact.write_bytes(b"console\n")
    record = artifact_record(artifact, root=tmp_path)
    assert record["path"] == "nested/console.raw.log"
    assert record["size"] == 8
    assert len(str(record["sha256"])) == 64


def test_tool_version_and_qemu_img_evidence_parsing() -> None:
    def version_runner(*_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout="QEMU emulator version 9.0\n")

    def check_runner(*_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout="No errors found\n", stderr="")

    def info_runner(*_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout='{"format": "qcow2"}')

    assert query_tool_version("qemu", runner=version_runner) == (
        "QEMU emulator version 9.0"
    )
    assert qemu_img_check("qemu-img", Path("disk"), runner=check_runner) == (
        "No errors found\n"
    )
    assert qemu_img_info("qemu-img", Path("disk"), runner=info_runner) == {
        "format": "qcow2"
    }


def test_validation_manifest_records_immutability_and_check_categories() -> None:
    answers = load_answers(REFERENCE_ANSWERS)
    validation = GuestValidationResult(
        (
            ValidationCheck("serial-boot", "deterministic", "passed", "booted"),
            ValidationCheck(
                "network-ping", "environmental", "failed", "offline"
            ),
        )
    )
    manifest = build_validation_manifest(
        status=validation.status,
        started_at="2026-07-14T01:00:00Z",
        completed_at="2026-07-14T01:01:00Z",
        answers=answers,
        answers_sha256="a" * 64,
        base_image=Path("base.qcow2"),
        base_sha256_before="b" * 64,
        base_sha256_after="b" * 64,
        overlay=Path("validation-overlay.qcow2"),
        overlay_removed=True,
        overlay_exists=False,
        host=HostInfo(system="Linux", distribution_id="ubuntu"),
        acceleration=Acceleration("KVM", ("-accel", "kvm")),
        memory_mib=2048,
        qemu_system_version="QEMU 9",
        qemu_img_version="qemu-img 9",
        qemu_command=["qemu-system-x86_64", "-accel", "kvm"],
        rendered_qemu_command="qemu-system-x86_64 -accel kvm",
        image_info={"format": "qcow2"},
        validation=validation,
        network_mode="optional",
        artifacts={"console": {"path": "boot.raw.log", "sha256": "c" * 64}},
    )
    assert manifest["status"] == "passed-with-environmental-failures"
    assert manifest["image"]["unchanged"] is True
    assert manifest["overlay"]["removed"] is True
    assert manifest["overlay"]["retained_on_failure"] is False
    assert manifest["validation"]["checks"][1]["category"] == "environmental"


def test_text_writer_requires_existing_parent(tmp_path: Path) -> None:
    with pytest.raises(P9QemuError, match="parent directory does not exist"):
        write_text_new(tmp_path / "missing" / "file.txt", "text")


def test_changed_base_digest_is_rejected() -> None:
    with pytest.raises(P9QemuError, match="base image digest changed"):
        require_unchanged_image("before", "after")
