# 9front 11554 AMD64 installation media

## Status

Canonical metadata for an unmodified upstream-media mirror. The application
must use the release asset URL only after the upload and independent checksum
verification steps have completed.

## Identity

| Property | Value |
| --- | --- |
| Media identifier | `9front-11554-amd64` |
| Upstream project | 9front |
| Upstream release | `11554` |
| Architecture | AMD64 |
| Upstream archive | `https://9front.org/iso/9front-11554.amd64.iso.gz` |
| Planned mirror tag | `media-9front-11554` |
| Planned mirror asset | `9front-11554.amd64.iso.gz` |

## Artifacts

| File | Purpose | Size | SHA-256 | Published |
| --- | --- | ---: | --- | --- |
| `9front-11554.amd64.iso.gz` | Upstream gzip archive | 243,859,187 bytes | `5aaf54327b4bb73a17e192488dc3e65d9d8e526728732e2fdf402bccb8c60236` | Yes |
| `9front-11554.amd64.iso` | ISO produced by gzip decompression | 491,550,720 bytes | `1dcfbb3ec221307329545a37d1562beeea1f4174f6df80d245e0a222893b3bb6` | No |

The compressed archive is mirrored byte-for-byte. It is not recompressed,
renamed, or modified. The unpacked ISO digest is recorded so `p9qemu` and
manual users can verify the second stage, but publishing both forms would
waste release storage and download bandwidth.

## Release contract

The media release is separate from application and ready-to-run image releases:

```text
media-9front-11554
    unmodified upstream installation media

v0.1.0
    p9qemu application release

image-9front-11554-hjfs-base-v1
    future p9qemu-produced post-install image
```

The release should contain:

```text
9front-11554.amd64.iso.gz
SHA256SUMS
media-manifest.json
```

The tag and filename are immutable identifiers. If different bytes ever need
to be distributed, create a new tag or asset name rather than replacing this
asset in place.

## Verification

On Linux, after downloading the archive and this checksum file:

```console
$ echo '5aaf54327b4bb73a17e192488dc3e65d9d8e526728732e2fdf402bccb8c60236  9front-11554.amd64.iso.gz' | sha256sum -c -
$ gzip -dk 9front-11554.amd64.iso.gz
$ echo '1dcfbb3ec221307329545a37d1562beeea1f4174f6df80d245e0a222893b3bb6  9front-11554.amd64.iso' | sha256sum -c -
```

On Windows PowerShell, verify the downloaded archive before decompression:

```powershell
$expected = '5AAF54327B4BB73A17E192488DC3E65D9D8E526728732E2FDF402BCCB8C60236'
$actual = (Get-FileHash -LiteralPath '.\9front-11554.amd64.iso.gz' -Algorithm SHA256).Hash
if ($actual -ne $expected) { throw "Checksum mismatch: $actual" }
```

The release process must also download the public GitHub asset without
authentication and verify it independently before the `p9qemu` default URL is
changed.

## Provenance and licensing

The earlier `9front-notes` workflow records the official upstream URL used to
obtain this archive. The cached archive and its decompressed ISO were both
rehashed on 2026-07-13 before this record was created.

9front states that the system is provided under the MIT License unless a
component indicates otherwise. The notices shipped inside the media remain
authoritative; mirroring the archive does not alter or replace them. The mirror
must be described as an unofficial convenience copy and must not imply that
the 9front project produced or endorses `p9qemu`.

## Machine-readable record

[`media-manifest.json`](media-manifest.json) contains the same identity,
distribution, and integrity data for tooling. [`SHA256SUMS`](SHA256SUMS) uses
the standard `sha256sum` format for both the compressed archive and unpacked
ISO.
