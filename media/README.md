# Installation media

This directory contains small, reviewable records for upstream installation
media used by `p9qemu`. It does not contain ISO images or compressed media
archives. Those large, immutable byte streams belong in GitHub release assets.

A media record identifies:

- the upstream project, release, architecture, and download location;
- the exact compressed and unpacked byte-stream digests;
- which artifact is mirrored by `p9qemu`;
- the dedicated GitHub release tag and asset name; and
- upstream licensing and attribution information.

Image-build recipes under [`../images`](../images/README.md) reference a media
identifier and retain the exact digests they consumed. Installation media and
post-install VM images have independent release lifecycles.

## Records

- [`9front-11554-amd64`](9front-11554-amd64/README.md) — 9front 11554 AMD64 installation ISO and compressed archive metadata.
