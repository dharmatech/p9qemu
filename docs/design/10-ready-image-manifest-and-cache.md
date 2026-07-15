# Ready-Image Manifest and Cache

## Status

Implemented as a local-only foundation. Schema 1 can describe a deterministic
release-candidate archive, and `p9qemu` can verify a local archive and install
its QCOW2 into a content-addressed, read-only cache. This phase deliberately
does not add network acquisition, a public catalog, a CLI command, instance
overlays, or GitHub publication.

The checked-in [candidate-002 example](../../images/manifests/p9qemu-9front-11554-amd64-hjfs-gmt-002.example.json)
uses the exact measurements and digests from the validated local bundle. Its
`example.invalid` URL is intentionally non-downloadable until a human approves
and publishes the asset.

## Separation of metadata

The release archive already contains a detailed `manifest.json`. That internal
manifest is build evidence: it binds the image to the installer, answer file,
runtime profile, validation evidence, and hygiene review.

The small external `image.json` is distribution metadata. It gives a future
client enough information to acquire and unpack the archive safely without
first trusting anything inside that archive. Keeping these roles separate also
allows a repository page or URL to point to a small manifest instead of asking
the user to type a large binary URL directly.

Schema 1 uses these top-level fields:

| Field | Meaning |
| --- | --- |
| `schema`, `kind` | Exact parser dispatch: `1` and `p9qemu-ready-image`. |
| `id`, `title`, `variant` | Immutable distribution identity and display text. |
| `guest` | 9front release and architecture. |
| `artifact` | Tar-gzip URL, filename, byte size, and compressed SHA-256. |
| `bundle` | Expected root, internal manifest digest, member/file counts, and total expanded file bytes. |
| `image` | QCOW2 relative path, stored and virtual sizes, and uncompressed SHA-256. |
| `runtime` | Structured p9qemu profile ID and descriptive capabilities. |

Unknown or missing fields fail closed. Identifiers cannot use a moving name
such as `latest`. Paths are canonical relative POSIX paths and must also be
safe on Windows: backslashes, drive prefixes, parent traversal, control
characters, and non-canonical forms are rejected. Artifact URLs must use HTTPS,
contain no credentials or fragment, and end in the declared filename.

Schema 1 supports `tar-gzip` containing one QCOW2 release-candidate bundle. The
Python standard library handles the format on Linux, Windows, and macOS, so no
host `tar` or `gzip` command is required. Raw QCOW2 and other compression
formats can be considered in a later schema rather than weakening this first
contract.

## Verification chain

The external manifest independently pins both packaged and usable forms:

```text
image.json
  |-- archive filename + byte size + SHA-256
  |-- archive member/file/expanded-byte inventory
  |-- internal manifest SHA-256
  `-- extracted QCOW2 size + SHA-256
```

Installation verifies, in order:

1. strict external-manifest syntax and supported limits;
2. local archive name, compressed byte size, and SHA-256;
3. available space for the declared expanded file bytes;
4. every tar member before any member is written;
5. the exact member count, regular-file count, and expanded byte total;
6. every artifact recorded by the internal release manifest;
7. the internal manifest digest and bundle identity;
8. agreement on image path, format, sizes, digest, and runtime profile; and
9. the extracted QCOW2 digest once more at the cache boundary.

Archive extraction permits only regular files and directories under the one
declared bundle root. It rejects absolute and non-portable paths, traversal,
duplicates, links, special files, missing parent directories, excessive member
counts, and inventory mismatches. Validation happens before file extraction,
and a failure removes the incomplete tree.

SHA-256 protects against corruption and unexpected substitution relative to
the selected `image.json`; it is not author authentication by itself. A public
catalog, signed release metadata, or an attestation can provide a stronger
origin story later without changing the archive verification chain.

## Immutable cache boundary

Verified bases use the uncompressed image digest as their content address:

```text
<cache>/images/<image-sha256>/
  image.json
  bundle/
    <bundle-id>/
      manifest.json
      <bundle-id>.qcow2
      ...provenance and validation files...
```

Extraction occurs in a unique sibling `.part` directory. Only a completely
verified tree is atomically renamed to its final digest path. Interrupted or
failed work is removed and cannot become a cache hit. A cache hit is not based
only on directory presence: the saved external manifest, complete internal
bundle inventory, image digest, and read-only state are checked again.

The base QCOW2 is marked read-only. Future instance creation must produce a
writable overlay and must never launch the shared base as a writable instance
disk. Overlay creation, backing-file relocation policy, and the user-facing
command remain outside this phase.

## Candidate 002 measurements

The first example records:

- archive: `p9qemu-9front-11554-amd64-hjfs-gmt-002.tar.gz`;
- compressed bytes: `250529927`;
- archive SHA-256: `ddf9086ab7925e891ea6d577474f70a6eccd91dccc85d5fc29b0d3acf29b6c4d`;
- archive inventory: 21 members, 17 files, 559069219 expanded file bytes;
- QCOW2 stored bytes: `559022080` and virtual bytes: `32212254720`; and
- QCOW2 SHA-256: `1ef80c81a3f2dd09d2f173ff7dfa93d07ecee2ba453fc0f0964190adb6ee44a8`.

These values describe the validated local candidate. They do not announce a
release or authorize an upload.

## Next phases

The next useful increments are intentionally separable:

1. generate `image.json` from an approved release candidate rather than
   transcribing measurements;
2. add resumable HTTPS acquisition of a selected manifest and its archive;
3. create a writable per-instance overlay over the cached base;
4. expose the workflow through a final CLI vocabulary; and
5. only then define the reviewed GitHub publication procedure and catalog.

The selection design remains open. A future command may accept a manifest URL,
an exact catalog ID, or both. Moving aliases must resolve visibly to an exact
immutable manifest before acquisition, and existing instances must never be
silently rebased.
