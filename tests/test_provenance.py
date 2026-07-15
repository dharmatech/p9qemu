from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from p9qemu.answers import load_answers
from p9qemu.errors import P9QemuError
from p9qemu.host import Acceleration, HostInfo
from p9qemu.provenance import (
    artifact_record,
    build_install_manifest,
    build_release_preparation_manifest,
    build_validation_manifest,
    qemu_img_check,
    qemu_img_info,
    query_tool_version,
    require_unchanged_image,
    write_json_new,
    write_text_new,
)
from p9qemu.runtime import load_runtime_profile
from p9qemu.validation import GuestValidationResult, ValidationCheck


ROOT = Path(__file__).parents[1]
REFERENCE_ANSWERS = (
    ROOT / "images" / "9front-11554-amd64-hjfs-manual-001" / "answers.toml"
)
RUNTIME_PROFILE = (
    ROOT / "images" / "9front-11554-amd64-hjfs-gmt-reference-001" / "runtime.toml"
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
    runtime = load_runtime_profile(RUNTIME_PROFILE)
    validation = GuestValidationResult(
        (
            ValidationCheck("serial-boot", "deterministic", "passed", "booted"),
            ValidationCheck("network-ping", "environmental", "failed", "offline"),
        )
    )
    manifest = build_validation_manifest(
        status=validation.status,
        started_at="2026-07-14T01:00:00Z",
        completed_at="2026-07-14T01:01:00Z",
        source_commit="a" * 40,
        answers=answers,
        answers_sha256="a" * 64,
        runtime_profile=runtime,
        runtime_profile_sha256="e" * 64,
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
        failure_category=None,
    )
    assert manifest["status"] == "passed-with-environmental-failures"
    assert manifest["image"]["unchanged"] is True
    assert manifest["overlay"]["removed"] is True
    assert manifest["overlay"]["retained_on_failure"] is False
    assert manifest["validation"]["checks"][1]["category"] == "environmental"
    assert manifest["validation"]["failure_category"] is None
    assert manifest["p9qemu"]["commit"] == "a" * 40
    assert manifest["runtime_profile"]["sha256"] == "e" * 64
    assert manifest["runtime_profile"]["resolved"]["target_monitor"] == "vesa"


def test_install_manifest_binds_source_media_answers_image_and_log() -> None:
    answers = load_answers(REFERENCE_ANSWERS)
    manifest = build_install_manifest(
        started_at="2026-07-14T00:00:00Z",
        completed_at="2026-07-14T00:01:00Z",
        source_commit="a" * 40,
        answers=answers,
        answers_path=Path("answers.toml"),
        answers_sha256="b" * 64,
        iso_path=Path("install.iso"),
        iso_sha256=answers.iso_sha256,
        image_path=Path("target.qcow2"),
        image_sha256="c" * 64,
        console_log=Path("install.raw.log"),
        console_log_sha256="d" * 64,
        host=HostInfo(system="Linux", distribution_id="ubuntu"),
        acceleration=Acceleration("KVM", ("-accel", "kvm")),
        memory_mib=1024,
        qemu_system_version="QEMU 9",
        qemu_img_version="qemu-img 9",
        qemu_command=["qemu-system-x86_64", "-accel", "kvm"],
        rendered_qemu_command="qemu-system-x86_64 -accel kvm",
        image_info={"format": "qcow2", "dirty-flag": False},
        image_check="No errors found\n",
    )
    assert manifest["kind"] == "p9qemu-image-installation"
    assert manifest["p9qemu"]["commit"] == "a" * 40
    assert manifest["answers"]["sha256"] == "b" * 64
    assert manifest["media"]["sha256"] == answers.iso_sha256
    assert manifest["image"]["sha256"] == "c" * 64
    assert manifest["console_log"]["sha256"] == "d" * 64


def test_preparation_manifest_binds_install_runtime_and_output_digest() -> None:
    answers = load_answers(
        ROOT / "images" / "9front-11554-amd64-hjfs-gmt-reference-001" / "answers.toml"
    )
    runtime = load_runtime_profile(RUNTIME_PROFILE)
    manifest = build_release_preparation_manifest(
        started_at="2026-07-15T00:00:00Z",
        completed_at="2026-07-15T00:01:00Z",
        source_commit="a" * 40,
        answers=answers,
        answers_path=Path("answers.toml"),
        answers_sha256="b" * 64,
        runtime_profile=runtime,
        runtime_profile_path=Path("runtime.toml"),
        runtime_profile_sha256="c" * 64,
        input_image=Path("installed.qcow2"),
        input_sha256="d" * 64,
        input_sha256_after="d" * 64,
        output_image=Path("prepared.qcow2"),
        output_sha256="e" * 64,
        input_image_info={"format": "qcow2", "dirty-flag": False},
        output_image_info={"format": "qcow2", "dirty-flag": False},
        input_image_check="No errors found\n",
        output_image_check="No errors found\n",
        host=HostInfo(system="Linux", distribution_id="ubuntu"),
        acceleration=Acceleration("KVM", ("-accel", "kvm")),
        memory_mib=2048,
        qemu_system_version="QEMU 9",
        qemu_img_version="qemu-img 9",
        qemu_command=["qemu-system-x86_64"],
        rendered_qemu_command="qemu-system-x86_64",
        artifacts={"before": {"path": "plan9.ini.before.txt"}},
    )
    assert manifest["kind"] == "p9qemu-image-release-preparation"
    assert manifest["image"]["input"]["unchanged"] is True
    assert manifest["image"]["changed"] is True
    assert manifest["runtime_profile"]["sha256"] == "c" * 64


def test_text_writer_requires_existing_parent(tmp_path: Path) -> None:
    with pytest.raises(P9QemuError, match="parent directory does not exist"):
        write_text_new(tmp_path / "missing" / "file.txt", "text")


def test_changed_base_digest_is_rejected() -> None:
    with pytest.raises(P9QemuError, match="base image digest changed"):
        require_unchanged_image("before", "after")
