from __future__ import annotations

from dataclasses import asdict
from io import BytesIO
import json
from pathlib import Path
import tarfile

import pytest

from p9qemu.answers import load_answers
from p9qemu.errors import P9QemuError
from p9qemu.media import sha256_file
from p9qemu.release_candidate import (
    CandidateInputs,
    build_release_candidate,
    create_deterministic_tar_gz,
    extract_release_archive,
    scan_public_text,
    validate_identity,
    validate_source_commit,
)


ROOT = Path(__file__).parents[1]
REFERENCE_ANSWERS = (
    ROOT / "images" / "9front-11554-amd64-hjfs-manual-001" / "answers.toml"
)


def _artifact(path: Path, *, root: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _validation_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    disk = tmp_path / "target.qcow2"
    disk.write_bytes(b"synthetic qcow2 fixture\0" * 64)
    answers = tmp_path / "answers.toml"
    answers.write_bytes(REFERENCE_ANSWERS.read_bytes())
    install_log = tmp_path / "console.raw.log"
    install_log.write_text("Plan 9 install completed\n", encoding="utf-8")

    evidence = tmp_path / "validation"
    evidence.mkdir()
    files = {
        "console_log": evidence / "boot.raw.log",
        "events": evidence / "events.jsonl",
        "qemu_img_check_before": evidence / "base-check-before.txt",
        "qemu_img_check_after": evidence / "base-check-after.txt",
    }
    files["console_log"].write_text("Plan 9\nterm% fshalt\n", encoding="utf-8")
    files["events"].write_text('{"message":"booted"}\n', encoding="utf-8")
    files["qemu_img_check_before"].write_text("No errors found\n", encoding="utf-8")
    files["qemu_img_check_after"].write_text("No errors found\n", encoding="utf-8")

    image_digest = sha256_file(disk)
    answers_digest = sha256_file(answers)
    manifest = {
        "schema": 1,
        "kind": "p9qemu-image-validation",
        "status": "passed",
        "started_at": "2026-07-14T01:00:00Z",
        "completed_at": "2026-07-14T01:01:00Z",
        "p9qemu": {"version": "0.1.0"},
        "answers": {
            "sha256": answers_digest,
            "resolved": asdict(load_answers(answers)),
        },
        "host": {
            "system": "Linux",
            "distribution_id": "ubuntu",
            "distribution_name": "Ubuntu",
            "version_id": "22.04",
            "architecture": "x86_64",
            "kernel": "private-kernel-detail",
        },
        "image": {
            "path": "/home/developer/private/target.qcow2",
            "sha256_before": image_digest,
            "sha256_after": image_digest,
            "unchanged": True,
            "qemu_img_info": {
                "filename": "/home/developer/private/target.qcow2",
                "format": "qcow2",
                "virtual-size": 1024,
                "actual-size": disk.stat().st_size,
                "cluster-size": 65536,
                "dirty-flag": False,
                "format-specific": {
                    "type": "qcow2",
                    "data": {"compat": "1.1", "corrupt": False},
                },
            },
        },
        "overlay": {
            "path": "/home/developer/private/overlay.qcow2",
            "exists": False,
            "removed": True,
            "retained_on_failure": False,
        },
        "qemu": {
            "system_version": "QEMU 9",
            "img_version": "qemu-img 9",
            "acceleration": "KVM",
            "memory_mib": 2048,
            "command": {
                "argv": ["/usr/bin/qemu", "/home/developer/private/overlay.qcow2"],
                "rendered": "/usr/bin/qemu /home/developer/private/overlay.qcow2",
            },
        },
        "validation": {
            "network_mode": "required",
            "checks": [
                {
                    "name": name,
                    "category": (
                        "environmental" if name == "network-ping" else "deterministic"
                    ),
                    "status": "passed",
                    "detail": "verified",
                }
                for name in (
                    "serial-boot",
                    "root-filesystem",
                    "guest.user",
                    "guest.home",
                    "guest.sysname",
                    "guest.plan9-ini",
                    "network-ping",
                    "orderly-shutdown",
                )
            ],
            "error": None,
            "failure_category": None,
        },
        "artifacts": {
            name: _artifact(path, root=evidence) for name, path in files.items()
        },
    }
    manifest_path = evidence / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return disk, answers, install_log, manifest_path


def _inputs(tmp_path: Path) -> CandidateInputs:
    disk, answers, install_log, validation = _validation_fixture(tmp_path)
    return CandidateInputs(
        identity=validate_identity("9front-11554-amd64-hjfs", "001"),
        source_commit="a" * 40,
        disk=disk,
        answers_path=answers,
        install_log=install_log,
        validation_manifest=validation,
        output_dir=tmp_path / "candidate",
        image_hygiene_reviewed=True,
    )


@pytest.mark.parametrize(
    ("image_id", "build_id"),
    [
        ("Latest", "001"),
        ("latest", "001"),
        ("9front-11554", "build_1"),
        ("9front--11554", "001"),
    ],
)
def test_candidate_identity_rejects_moving_or_unsafe_names(
    image_id: str, build_id: str
) -> None:
    with pytest.raises(P9QemuError):
        validate_identity(image_id, build_id)


def test_source_commit_must_be_complete_and_immutable() -> None:
    with pytest.raises(P9QemuError, match="40-character"):
        validate_source_commit("main")
    assert validate_source_commit("a" * 40) == "a" * 40


@pytest.mark.parametrize(
    "text",
    [
        "file=/home/alice/private/disk.qcow2\n",
        r"file=C:\Users\alice\private\disk.qcow2",
        "token=github_pat_examplevalue\n",
        "-----BEGIN PRIVATE KEY-----\n",
    ],
)
def test_public_text_scan_rejects_host_paths_and_secrets(
    tmp_path: Path, text: str
) -> None:
    path = tmp_path / "public.txt"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(P9QemuError, match="privacy scan rejected"):
        scan_public_text([path], root=tmp_path)


def test_release_candidate_is_sanitized_and_round_trip_verified(
    tmp_path: Path,
) -> None:
    inputs = _inputs(tmp_path)
    result = build_release_candidate(inputs)

    assert result.output_dir.is_dir()
    assert result.archive.is_file()
    assert result.archive_sha256 == sha256_file(result.archive)
    verification = json.loads(result.verification.read_text(encoding="utf-8"))
    assert verification["status"] == "passed"
    assert verification["image_sha256"] == result.image_sha256

    release = json.loads(result.manifest.read_text(encoding="utf-8"))
    assert release["stage"] == "local-only"
    assert release["publication"]["uploaded"] is False
    assert release["hygiene"]["public_text_scan"] == "passed"
    assert release["source"]["p9qemu_commit"] == "a" * 40

    public_validation_path = result.bundle_dir / "validation" / "manifest.json"
    public_validation = json.loads(public_validation_path.read_text(encoding="utf-8"))
    serialized = json.dumps(public_validation)
    assert "/home/developer" not in serialized
    assert "private-kernel-detail" not in serialized
    assert "command" not in public_validation["qemu"]
    assert "filename" not in public_validation["image"]["qemu_img_info"]
    assert not (result.bundle_dir / "validation" / "events.jsonl").exists()
    assert public_validation["source_manifest_sha256"] == sha256_file(
        inputs.validation_manifest
    )

    image_name = f"{inputs.identity.bundle_name}.qcow2"
    running = (result.bundle_dir / "RUNNING.md").read_text(encoding="utf-8")
    assert f"cp {image_name}" in running
    assert "-accel whpx,kernel-irqchip=off" in running
    assert "-display sdl" in running


def test_release_candidate_never_replaces_existing_output(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    inputs.output_dir.mkdir()
    with pytest.raises(P9QemuError, match="refusing to replace"):
        build_release_candidate(inputs)


def test_release_candidate_requires_explicit_image_hygiene_review(
    tmp_path: Path,
) -> None:
    inputs = _inputs(tmp_path)
    unsafe = CandidateInputs(**{**inputs.__dict__, "image_hygiene_reviewed": False})
    with pytest.raises(P9QemuError, match="confirm-image-hygiene"):
        build_release_candidate(unsafe)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (("status",), "status 'passed'"),
        (("image", "unchanged"), "base image was unchanged"),
        (("overlay", "removed"), "overlay was not removed"),
        (("validation", "checks", 0, "status"), "every validation check"),
    ],
)
def test_release_candidate_rejects_unpromotable_validation(
    tmp_path: Path, mutation: tuple[object, ...], message: str
) -> None:
    inputs = _inputs(tmp_path)
    document = json.loads(inputs.validation_manifest.read_text(encoding="utf-8"))
    value: object = document
    for key in mutation[:-1]:
        value = value[key]
    last = mutation[-1]
    if mutation == ("status",):
        document["status"] = "failed"
    elif mutation == ("image", "unchanged"):
        value[last] = False
    elif mutation == ("overlay", "removed"):
        value[last] = False
    else:
        value[last] = "failed"
    inputs.validation_manifest.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(P9QemuError, match=message):
        build_release_candidate(inputs)


def test_deterministic_archive_ignores_source_metadata(tmp_path: Path) -> None:
    first = tmp_path / "first" / "bundle"
    second = tmp_path / "second" / "bundle"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "file.txt").write_text("same bytes\n", encoding="utf-8")
    (second / "file.txt").write_text("same bytes\n", encoding="utf-8")
    first_archive = tmp_path / "first.tar.gz"
    second_archive = tmp_path / "second.tar.gz"

    create_deterministic_tar_gz(first, first_archive)
    create_deterministic_tar_gz(second, second_archive)

    assert first_archive.read_bytes() == second_archive.read_bytes()


def test_archive_extraction_rejects_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "malicious.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        payload = b"escape"
        info = tarfile.TarInfo("bundle/../escape.txt")
        info.size = len(payload)
        tar.addfile(info, BytesIO(payload))

    destination = tmp_path / "extract"
    with pytest.raises(P9QemuError, match="unsafe path"):
        extract_release_archive(archive, destination, "bundle")
    assert not destination.exists()
    assert not (tmp_path / "escape.txt").exists()
