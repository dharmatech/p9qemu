"""Build a sanitized, local-only image release-candidate archive."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from p9qemu.errors import P9QemuError
from p9qemu.release_candidate import (
    CandidateInputs,
    build_release_candidate,
    inspect_candidate_inputs,
    validate_identity,
    validate_source_commit,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build and round-trip verify a sanitized local release candidate. "
            "This tool never uploads or publishes assets."
        )
    )
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--build-id", required=True)
    parser.add_argument(
        "--source-commit",
        required=True,
        help="complete Git commit identifying the build implementation",
    )
    parser.add_argument("--disk", type=Path, required=True)
    parser.add_argument("--answers", type=Path, required=True)
    parser.add_argument("--runtime-profile", type=Path, required=True)
    parser.add_argument("--install-log", type=Path, required=True)
    parser.add_argument("--install-manifest", type=Path, required=True)
    parser.add_argument("--preparation-manifest", type=Path, required=True)
    parser.add_argument("--validation-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--confirm-image-hygiene-reviewed",
        action="store_true",
        help=(
            "confirm that the guest image contents were reviewed for credentials, "
            "personal state, and machine-specific material"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and hash inputs without creating or archiving output",
    )
    return parser


def _absolute(path: Path) -> Path:
    return path.expanduser().resolve()


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        identity = validate_identity(args.image_id, args.build_id)
        source_commit = validate_source_commit(args.source_commit)
        inputs = CandidateInputs(
            identity=identity,
            source_commit=source_commit,
            disk=_absolute(args.disk),
            answers_path=_absolute(args.answers),
            runtime_profile_path=_absolute(args.runtime_profile),
            install_log=_absolute(args.install_log),
            install_manifest=_absolute(args.install_manifest),
            preparation_manifest=_absolute(args.preparation_manifest),
            validation_manifest=_absolute(args.validation_manifest),
            output_dir=_absolute(args.output_dir),
            image_hygiene_reviewed=args.confirm_image_hygiene_reviewed,
        )

        print("Experimental mode: local release-candidate bundle")
        print(f"Candidate: {identity.bundle_name}")
        print(f"Immutable image: {inputs.disk}")
        print(f"Answer file: {inputs.answers_path}")
        print(f"Runtime profile: {inputs.runtime_profile_path}")
        print(f"Install log: {inputs.install_log}")
        print(f"Installation manifest: {inputs.install_manifest}")
        print(f"Preparation manifest: {inputs.preparation_manifest}")
        print(f"Validation manifest: {inputs.validation_manifest}")
        print(f"New output directory: {inputs.output_dir}")
        print("Publishing: disabled")

        if args.dry_run:
            inspected = inspect_candidate_inputs(inputs)
            image_sha256 = inspected[5]
            answers_sha256 = inspected[6]
            preparation_artifacts = inspected[8]
            validation_artifacts = inspected[9]
            print("\nDry run passed; no output was created.")
            print(f"Image SHA-256: {image_sha256}")
            print(f"Answers SHA-256: {answers_sha256}")
            print(
                "Validated public evidence files: "
                f"{len(preparation_artifacts) + len(validation_artifacts)}"
            )
            return 0

        result = build_release_candidate(inputs)
        print("\nRelease candidate built and round-trip verified.")
        print(f"Bundle: {result.bundle_dir}")
        print(f"Archive: {result.archive}")
        print(f"Archive SHA-256: {result.archive_sha256}")
        print(f"Image SHA-256: {result.image_sha256}")
        print(f"Manifest: {result.manifest}")
        print(f"Verification: {result.verification}")
        print("Nothing was uploaded.")
        return 0
    except P9QemuError as error:
        print(f"build_release_candidate: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nbuild_release_candidate: interrupted", file=sys.stderr)
        return 130


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
