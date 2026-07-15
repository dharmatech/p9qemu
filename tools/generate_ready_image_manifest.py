"""Generate a local external manifest from one verified candidate archive."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from p9qemu.errors import P9QemuError
from p9qemu.ready_image_manifest import (
    ReadyImageManifestInputs,
    build_ready_image_manifest,
    write_ready_image_manifest,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Stream and verify a local release-candidate archive, then generate "
            "its deterministic external image.json. This tool never downloads, "
            "uploads, or publishes assets."
        )
    )
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        help="new manifest path (default: image.json beside the archive)",
    )
    parser.add_argument("--asset-url", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--variant", required=True)
    parser.add_argument("--distribution", required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--architecture", required=True)
    parser.add_argument(
        "--capability",
        action="append",
        required=True,
        dest="capabilities",
        help="reviewed runtime capability; repeat for each value",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="stream and validate the archive without creating image.json",
    )
    return parser


def _absolute(path: Path) -> Path:
    return path.expanduser().resolve()


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        archive = _absolute(args.archive)
        output = _absolute(args.output or archive.with_name("image.json"))
        if output.exists():
            raise P9QemuError(f"refusing to replace ready-image manifest: {output}")
        if not output.parent.is_dir():
            raise P9QemuError(
                f"ready-image manifest parent directory does not exist: {output.parent}"
            )
        inputs = ReadyImageManifestInputs(
            archive=archive,
            output=output,
            asset_url=args.asset_url,
            title=args.title,
            variant=args.variant,
            distribution=args.distribution,
            release=args.release,
            architecture=args.architecture,
            capabilities=tuple(args.capabilities),
        )

        print("Experimental mode: local ready-image manifest generation")
        print(f"Candidate archive: {archive}")
        print(f"New manifest: {output}")
        print("Archive extraction: disabled")
        print("Network access: disabled")
        print("Publishing: disabled")

        result = (
            build_ready_image_manifest(inputs)
            if args.dry_run
            else write_ready_image_manifest(inputs)
        )
        inspection = result.inspection
        heading = (
            "Dry run passed; no output was created."
            if args.dry_run
            else ("Ready-image manifest generated.")
        )
        print(f"\n{heading}")
        print(f"Image ID: {result.manifest.image_id}")
        print(f"Archive SHA-256: {inspection.archive_sha256}")
        print(f"Internal manifest SHA-256: {inspection.manifest_sha256}")
        print(f"Image SHA-256: {inspection.image.sha256}")
        print(
            "Archive inventory: "
            f"{inspection.member_count} members, "
            f"{inspection.file_count} files, "
            f"{inspection.extracted_size} extracted bytes"
        )
        if not args.dry_run:
            print(f"Manifest: {output}")
        print("Nothing was uploaded.")
        return 0
    except P9QemuError as error:
        print(f"generate_ready_image_manifest: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ngenerate_ready_image_manifest: interrupted", file=sys.stderr)
        return 130


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
