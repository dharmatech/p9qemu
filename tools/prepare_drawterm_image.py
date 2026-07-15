"""Create a Drawterm-ready derivative from the pinned stock ready image."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import os
from pathlib import Path
import shutil
import sys
from uuid import uuid4

import pexpect

from p9qemu.drawterm_postinstall import (
    DrawtermPostinstallProfile,
    drive_drawterm_preparation,
    load_drawterm_postinstall_profile,
)
from p9qemu.errors import P9QemuError
from p9qemu.host import current_host, discover_qemu, resolve_acceleration
from p9qemu.media import sha256_file
from p9qemu.pexpect_validation import PexpectGuestValidationTransport
from p9qemu.provenance import (
    artifact_record,
    qemu_img_check,
    qemu_img_info,
    query_tool_version,
    require_unchanged_image,
    utc_timestamp,
    validate_source_commit,
    write_json_new,
    write_text_new,
)
from p9qemu.qemu import build_automated_validation_command, render_command
from p9qemu.ready_image import ReadyImageManifest, parse_ready_image_manifest
from p9qemu.release_candidate import load_json_object


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Copy the exact stock ready image, apply a qualified Drawterm "
            "post-install profile through the serial console, and record the "
            "digest chain without modifying the parent."
        )
    )
    parser.add_argument("--postinstall-profile", type=Path, required=True)
    parser.add_argument("--parent-manifest", type=Path, required=True)
    parser.add_argument("--input-disk", type=Path, required=True)
    parser.add_argument("--output-disk", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--memory", type=int, default=2048, metavar="MIB")
    parser.add_argument("--accel", choices=("kvm", "tcg"), default="kvm")
    parser.add_argument(
        "--confirm-create-drawterm-copy",
        action="store_true",
        help="confirm creation and mutation of the new --output-disk",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="validate inputs without copying a disk"
    )
    return parser


def _absolute(path: Path) -> Path:
    return path.expanduser().resolve()


def _copy_new(source: Path, destination: Path) -> None:
    temporary = destination.with_name(f".{destination.name}.p9qemu-{uuid4().hex}.part")
    try:
        with source.open("rb") as input_stream, temporary.open("xb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
        try:
            os.link(temporary, destination)
        except FileExistsError as error:
            raise P9QemuError(
                f"refusing to replace Drawterm-prepared disk: {destination}"
            ) from error
        except OSError as error:
            raise P9QemuError(
                f"could not publish Drawterm-prepared disk {destination}: {error}"
            ) from error
    except OSError as error:
        raise P9QemuError(
            f"could not copy stock disk to {destination}: {error}"
        ) from error
    finally:
        temporary.unlink(missing_ok=True)


def _terminate(child: pexpect.spawn) -> None:
    if child.isalive():
        child.terminate(force=True)
    child.close(force=True)


def _prepare(command: list[str], profile: DrawtermPostinstallProfile):
    try:
        child = pexpect.spawn(
            command[0],
            command[1:],
            encoding="utf-8",
            codec_errors="replace",
            echo=False,
            timeout=None,
        )
    except (OSError, pexpect.ExceptionPexpect) as error:
        raise P9QemuError(
            f"could not start Drawterm-preparation QEMU: {error}"
        ) from error
    child.delaybeforesend = 0.05
    try:
        return drive_drawterm_preparation(
            PexpectGuestValidationTransport(child), profile
        )
    finally:
        _terminate(child)


def _require_parent(
    profile: DrawtermPostinstallProfile,
    *,
    parent_manifest_path: Path,
    input_disk: Path,
) -> tuple[ReadyImageManifest, str]:
    manifest_hash = sha256_file(parent_manifest_path)
    if manifest_hash != profile.parent.manifest_sha256:
        raise P9QemuError(
            "parent manifest hash mismatch: "
            f"expected {profile.parent.manifest_sha256}, got {manifest_hash}"
        )
    manifest = parse_ready_image_manifest(
        load_json_object(parent_manifest_path, "parent ready-image manifest")
    )
    if manifest.image_id != profile.parent.image_id:
        raise P9QemuError("parent manifest ID does not match the post-install profile")
    if manifest.image.sha256 != profile.parent.image_sha256:
        raise P9QemuError(
            "parent manifest image digest does not match the post-install profile"
        )
    input_hash = sha256_file(input_disk)
    if input_hash != profile.parent.image_sha256:
        raise P9QemuError(
            "input disk hash mismatch: "
            f"expected {profile.parent.image_sha256}, got {input_hash}"
        )
    return manifest, input_hash


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if not args.confirm_create_drawterm_copy:
            raise P9QemuError("--confirm-create-drawterm-copy is required")
        if args.memory <= 0:
            raise P9QemuError("memory must be a positive number of MiB")
        host = current_host()
        if host.system != "Linux":
            raise P9QemuError(
                "Drawterm image preparation is currently supported only on Linux"
            )

        source_commit = validate_source_commit(args.source_commit)
        profile_path = _absolute(args.postinstall_profile)
        parent_manifest_path = _absolute(args.parent_manifest)
        input_disk = _absolute(args.input_disk)
        output_disk = _absolute(args.output_disk)
        output_dir = _absolute(args.output_dir)
        for path, label in (
            (profile_path, "post-install profile"),
            (parent_manifest_path, "parent ready-image manifest"),
            (input_disk, "stock input disk"),
        ):
            if not path.is_file():
                raise P9QemuError(f"{label} is not an existing file: {path}")
        if output_disk.exists():
            raise P9QemuError(
                f"refusing to replace Drawterm-prepared disk: {output_disk}"
            )
        if not output_disk.parent.is_dir():
            raise P9QemuError(
                f"Drawterm-prepared disk parent does not exist: {output_disk.parent}"
            )
        if output_dir.exists():
            raise P9QemuError(
                f"refusing to replace Drawterm-preparation evidence: {output_dir}"
            )
        if not output_dir.parent.is_dir():
            raise P9QemuError(f"evidence parent does not exist: {output_dir.parent}")

        profile = load_drawterm_postinstall_profile(profile_path)
        parent_manifest, input_hash = _require_parent(
            profile,
            parent_manifest_path=parent_manifest_path,
            input_disk=input_disk,
        )
        executables = discover_qemu(host)
        acceleration = resolve_acceleration(args.accel, host)
        input_check = qemu_img_check(executables.image, input_disk)
        input_info = qemu_img_info(executables.image, input_disk)
        if input_info.get("format") != "qcow2":
            raise P9QemuError("stock input disk is not QCOW2")
        if input_info.get("backing-filename") is not None:
            raise P9QemuError("stock input disk is not a standalone ready image")

        console_log = output_dir / "postinstall.raw.log"
        command = build_automated_validation_command(
            executables.system,
            overlay=output_disk,
            console_log=console_log,
            memory_mib=args.memory,
            acceleration=acceleration,
        )
        rendered = render_command(command, system="Linux")
        print("Drawterm derivative: qualified auth-wrkey-v1 post-install profile")
        print(f"Profile: {profile.profile_id}")
        print(f"Immutable parent: {profile.parent.image_id}")
        print(f"Parent disk: {input_disk}")
        print(f"New derivative disk: {output_disk}")
        print(f"New evidence directory: {output_dir}")
        print("Credential class: public-demo (password omitted from logs)")
        print("\nWould start QEMU:\n" if args.dry_run else "\nStarting QEMU:\n")
        print(rendered, flush=True)
        if args.dry_run:
            return 0

        output_dir.mkdir()
        _copy_new(input_disk, output_disk)
        write_text_new(output_dir / "qemu-command.txt", rendered + "\n")
        write_text_new(output_dir / "qemu-img-check-input.txt", input_check)
        write_json_new(output_dir / "qemu-img-info-input.json", input_info)

        started_at = utc_timestamp()
        result = _prepare(command, profile)
        completed_at = utc_timestamp()
        input_hash_after = sha256_file(input_disk)
        require_unchanged_image(input_hash, input_hash_after)
        output_hash = sha256_file(output_disk)
        if output_hash == input_hash:
            raise P9QemuError("Drawterm preparation did not change the copied image")
        output_check = qemu_img_check(executables.image, output_disk)
        output_info = qemu_img_info(executables.image, output_disk)
        write_text_new(output_dir / "plan9.ini.before.txt", result.before)
        write_text_new(output_dir / "plan9.ini.after.txt", result.after)
        write_text_new(output_dir / "qemu-img-check-output.txt", output_check)
        write_json_new(output_dir / "qemu-img-info-output.json", output_info)

        artifact_paths = {
            "console_log": output_dir / "postinstall.raw.log",
            "qemu_command": output_dir / "qemu-command.txt",
            "plan9_ini_before": output_dir / "plan9.ini.before.txt",
            "plan9_ini_after": output_dir / "plan9.ini.after.txt",
            "qemu_img_check_input": output_dir / "qemu-img-check-input.txt",
            "qemu_img_check_output": output_dir / "qemu-img-check-output.txt",
            "qemu_img_info_input": output_dir / "qemu-img-info-input.json",
            "qemu_img_info_output": output_dir / "qemu-img-info-output.json",
        }
        artifacts = {
            name: artifact_record(path, root=output_dir)
            for name, path in artifact_paths.items()
        }
        write_json_new(
            output_dir / "manifest.json",
            {
                "schema": 1,
                "kind": "p9qemu-image-postinstall-preparation",
                "status": "passed",
                "started_at": started_at,
                "completed_at": completed_at,
                "p9qemu": {"commit": source_commit},
                "postinstall_profile": {
                    "path": str(profile_path),
                    "sha256": sha256_file(profile_path),
                    "schema": profile.schema,
                    "kind": profile.kind,
                    "profile_id": profile.profile_id,
                    "credential_class": profile.nvram.credential_class,
                    "password_redacted": True,
                },
                "parent_manifest": {
                    "path": str(parent_manifest_path),
                    "sha256": profile.parent.manifest_sha256,
                    "id": parent_manifest.image_id,
                    "resolved": asdict(profile.parent),
                },
                "image": {
                    "input": {
                        "path": str(input_disk),
                        "sha256": input_hash,
                        "sha256_after": input_hash_after,
                        "unchanged": input_hash == input_hash_after,
                        "qemu_img_info": input_info,
                        "qemu_img_check": input_check,
                    },
                    "output": {
                        "path": str(output_disk),
                        "sha256": output_hash,
                        "qemu_img_info": output_info,
                        "qemu_img_check": output_check,
                    },
                    "changed": output_hash != input_hash,
                },
                "host": {
                    "system": host.system,
                    "distribution_id": host.distribution_id,
                    "version_id": host.version_id,
                },
                "qemu": {
                    "system_version": query_tool_version(executables.system),
                    "img_version": query_tool_version(executables.image),
                    "acceleration": acceleration.name,
                    "memory_mib": args.memory,
                    "command": {"argv": command, "rendered": rendered},
                },
                "artifacts": artifacts,
            },
        )
        print(f"Parent SHA-256: {input_hash}")
        print(f"Output SHA-256: {output_hash}")
        print(f"Preparation manifest: {output_dir / 'manifest.json'}")
        return 0
    except P9QemuError as error:
        print(f"prepare_drawterm_image: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nprepare_drawterm_image: interrupted", file=sys.stderr)
        return 130


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
