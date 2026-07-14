# Image recipes

This directory contains small, reviewable provenance bundles for 9front images.
It does not contain ISO, QCOW2, or other virtual-machine binaries. Those large
artifacts belong in immutable GitHub release assets and are identified here by
cryptographic digest.

Each recipe should distinguish among:

- upstream installation media;
- the answer file and installation transcript;
- the resulting post-install image;
- later guest customization; and
- validation performed before publication.

Canonical installation-media records live under [`../media`](../media/README.md).
Recipes reference those records while retaining the exact media digests used
for an individual build.

## Reference builds

- [`9front-11554-amd64-hjfs-manual-001`](9front-11554-amd64-hjfs-manual-001/README.md) — experimental manual serial-console installation used to derive the first answer-file profile.
