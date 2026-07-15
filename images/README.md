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

External ready-image manifest examples live under [`manifests`](manifests/).
They contain no VM binaries. Schema 1 and the local immutable-cache trust
boundary are documented in
[`docs/design/10-ready-image-manifest-and-cache.md`](../docs/design/10-ready-image-manifest-and-cache.md).

## Reference builds

- [`9front-11554-amd64-hjfs-manual-001`](9front-11554-amd64-hjfs-manual-001/README.md) — experimental manual serial-console installation used to derive the first answer-file profile.
- [`9front-11554-amd64-hjfs-gmt-reference-001`](9front-11554-amd64-hjfs-gmt-reference-001/README.md) — canonical GMT answer profile for the first fresh reference-image build.

## Ready-image manifest examples

- [`p9qemu-9front-11554-amd64-hjfs-gmt-002.example.json`](manifests/p9qemu-9front-11554-amd64-hjfs-gmt-002.example.json) — exact local candidate-002 metadata with an intentionally non-downloadable placeholder URL.
