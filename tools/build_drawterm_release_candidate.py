"""Build a sanitized, local-only Drawterm ready-image candidate archive."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from p9qemu.drawterm_release_candidate import (
    DrawtermCandidateInputs,
    build_drawterm_release_candidate,
    inspect_drawterm_candidate_inputs,
)
from p9qemu.errors import P9QemuError
from p9qemu.release_candidate import validate_identity, validate_source_commit


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build and round-trip verify a sanitized Drawterm ready-image "
            "candidate. This tool never uploads or publishes assets."
        )
    )
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--disk", type=Path, required=True)
    parser.add_argument("--postinstall-profile", type=Path, required=True)
    parser.add_argument("--parent-manifest", type=Path, required=True)
    parser.add_argument("--preparation-manifest", type=Path, required=True)
    parser.add_argument("--validation-manifest", type=Path, required=True)
    parser.add_argument("--password-rotation-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--confirm-image-hygiene-reviewed", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _absolute(path: Path) -> Path:
    return path.expanduser().resolve()


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        inputs = DrawtermCandidateInputs(
            identity=validate_identity(args.image_id, args.build_id),
            source_commit=validate_source_commit(args.source_commit),
            disk=_absolute(args.disk),
            postinstall_profile_path=_absolute(args.postinstall_profile),
            parent_manifest_path=_absolute(args.parent_manifest),
            preparation_manifest=_absolute(args.preparation_manifest),
            validation_manifest=_absolute(args.validation_manifest),
            password_rotation_manifest=_absolute(args.password_rotation_manifest),
            output_dir=_absolute(args.output_dir),
            image_hygiene_reviewed=args.confirm_image_hygiene_reviewed,
        )
        print("Experimental mode: local Drawterm release-candidate bundle")
        print(f"Candidate: {inputs.identity.bundle_name}")
        print(f"Immutable image: {inputs.disk}")
        print(f"New output directory: {inputs.output_dir}")
        print("Publishing: disabled")
        if args.dry_run:
            # The full builder is new-only and atomic. A temporary output is not
            # desirable for dry-run, so validate through its private input gate.
            inspected = inspect_drawterm_candidate_inputs(inputs)
            print("\nDry run passed; no output was created.")
            print(f"Image SHA-256: {inspected.image_sha256}")
            print(f"Post-install profile SHA-256: {inspected.profile_sha256}")
            return 0
        result = build_drawterm_release_candidate(inputs)
        print("\nDrawterm release candidate built and round-trip verified.")
        print(f"Bundle: {result.bundle_dir}")
        print(f"Archive: {result.archive}")
        print(f"Archive SHA-256: {result.archive_sha256}")
        print(f"Image SHA-256: {result.image_sha256}")
        print(f"Manifest: {result.manifest}")
        print(f"Verification: {result.verification}")
        print("Nothing was uploaded.")
        return 0
    except P9QemuError as error:
        print(f"build_drawterm_release_candidate: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nbuild_drawterm_release_candidate: interrupted", file=sys.stderr)
        return 130


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
