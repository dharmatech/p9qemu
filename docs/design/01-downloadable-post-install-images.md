# Downloadable Post-Install Images

## Status

Phased future direction. The first local-only schema and immutable-cache
foundation is now implemented, without a network, CLI, instance-creation, or
publication workflow. See
[`10-ready-image-manifest-and-cache.md`](10-ready-image-manifest-and-cache.md)
for the exact implemented boundary.

## Motivation

The version 1 `p9qemu install` workflow makes a new 9front installation easier
and preserves the normal installer experience. A later workflow should also let
users begin with a known post-install image published as a `p9qemu` GitHub
release asset. This would provide a quick path to a working Plan 9 environment
without requiring every user to perform the interactive installation first.

The manual installation workflow should remain available. Downloadable images
are an additional starting point, not a replacement for transparent QEMU use or
for learning how 9front is installed.

## Proposed experience

A concise command could select a published image by version:

```console
$ mkdir my-9front
$ cd my-9front
$ p9qemu run 11554
```

When the selected image is not cached, `p9qemu` would download and verify it.
It would then create writable instance state in the current directory and start
QEMU using the ordinary runtime profile. As with every QEMU launch, the exact
QEMU command must be printed before execution.

The final CLI vocabulary remains open. Persistent creation may be clearer as:

```console
$ p9qemu create --image 11554
$ p9qemu start
```

This avoids implying that `run` is ephemeral when it actually preserves guest
changes. Version 2 should define the lifecycle semantics before committing to a
public command name.

## Immutable base images and writable overlays

A downloaded release image should be an immutable base, not the user's writable
VM disk. The preferred model is:

```text
shared per-user cache
  verified, read-only base QCOW2 image
                    |
                    | backing image
                    v
instance directory
  writable QCOW2 overlay
```

Each instance receives its own small QCOW2 overlay whose backing file is the
cached base image. Guest writes go only to the overlay.

This model:

- reuses one large download across multiple instances;
- creates new instances quickly;
- prevents guest writes from modifying a verified release artifact;
- makes reset and disposal straightforward;
- separates immutable distribution media from user-owned state; and
- permits integrity checks of cached base images.

Backing-file paths require care. Moving the cache or instance directory must
not silently leave an unusable overlay. The implementation should either use a
stable backing-file convention or provide an explicit repair or relocation
strategy. It must never modify an existing overlay implicitly.

## Release assets and manifest

Release assets should have immutable, versioned names. An asset must never be
replaced with different contents while retaining the same version and filename.

Each published image should have machine-readable metadata containing at least:

- the public image identifier and 9front revision;
- target architecture;
- asset URL and filename;
- compression format;
- uncompressed image format and virtual size;
- SHA-256 digest of the downloaded asset;
- optionally, a digest of the unpacked base image;
- the compatible `p9qemu`/QEMU profile version; and
- a human-readable description of the installed system.

Schema 1 now defines a small external manifest that pins a deterministic
tar-gzip release bundle, its internal provenance manifest, and its extracted
QCOW2. A later catalog location and selection vocabulary remain open. `p9qemu`
must not infer security-critical metadata solely from filenames.

Downloads, decompression, and cache publication must follow the same safety
rules as installation media: write temporary files, verify them, and atomically
rename them into place only on success. Interrupted work must not be treated as
a valid cached image.

GitHub release asset size limits and practical download times must be measured
before selecting the compression and publication scheme. The initial private
repository may also require GitHub authentication; public releases should be
downloadable without credentials once the project is public.

## Image provenance and reproducibility

Every image release should document exactly how it was produced, including:

- the 9front source or installation revision;
- installer choices and disk layout;
- packages or source changes applied after installation;
- enabled network services;
- default users and authentication state;
- cleanup and image-compaction steps; and
- the `p9qemu` and QEMU versions used for validation.

The long-term goal should be a reproducible or at least repeatable image build.
An initially manual build is acceptable only when every step is recorded well
enough to audit and repeat.

The proposed answer-file format, automated installer, transcript, and resolved
build manifest are described in
[`04-automated-installation-answer-files.md`](04-automated-installation-answer-files.md).
The local-only promotion boundary, sanitized release manifest, deterministic
archive, and archive round-trip verification are described in
[`09-release-candidate-promotion.md`](09-release-candidate-promotion.md).

## Image hygiene and first-run identity

Published images must be treated as distribution artifacts rather than copies
of a developer's personal VM. Before publication they must not contain:

- private keys or credentials;
- personal authentication databases;
- development host keys or tokens;
- shell history or private source trees;
- cached secrets;
- machine-specific paths or mount assumptions; or
- other personal or instance-specific data.

Version 2 must determine which identity material should be generated on first
run. This includes the QEMU MAC address and any guest authentication or service
identity that should differ among instances. Multiple instances created from
one base must be safe to run concurrently.

Published documentation should also state the image's trust model. Checksums
protect against corruption and unexpected replacement but do not by themselves
prove how an image was constructed. Signed manifests or attestations may be
considered later.

## QEMU profiles and compatibility

Downloaded images should use the same transparent runtime command builder as
locally installed images. Image metadata may declare a compatible profile, but
must not provide arbitrary command fragments that are executed through a shell.
All invocations remain structured `list[str]` arguments, rendered separately
for display, printed before launch, and executed without `shell=True`.

Hardware acceleration remains a host capability rather than an image property.
Linux may select KVM, Windows may later select an appropriate tested
acceleration backend, and portable emulation remains the fallback. An image
should not require a particular accelerator unless that requirement is explicit
in its metadata.

Port forwards and fixed host ports also require attention. Multiple instances
cannot bind the same host address and ports concurrently. The alternatives and
recommended per-VM loopback-address plus shared-Ethernet topology are described
in [`07-multiple-plan9-vms.md`](07-multiple-plan9-vms.md). Packaging several
such instances as a reproducible environment is described separately in
[`08-downloadable-multi-vm-labs.md`](08-downloadable-multi-vm-labs.md).

## Version selection and updates

Image identifiers must remain stable. The CLI should distinguish among:

- an exact immutable image version;
- an optional moving channel such as `latest`; and
- the local instance created from that version.

Resolving `latest` should produce and display an exact version before download
or execution. Existing instances must never be silently rebased onto a newer
base image. Guest upgrades and base-image replacement are separate operations
and require explicit user intent.

Cache management will eventually need commands or policies for listing cached
bases, finding which instances depend on them, and safely removing unreferenced
artifacts. Cache cleanup must not delete a backing image still required by an
instance overlay.

## Version 1 architectural implications

Version 1 does not need to implement downloadable post-install images, but it
should maintain boundaries that make this feature additive:

- acquisition of immutable media should be separate from instance state;
- shared cache management should not assume that every artifact is an ISO;
- disk creation should distinguish new standalone disks from future overlays;
- QEMU command construction should be independent of how a disk was acquired;
- install and start lifecycle orchestration should remain separate; and
- checksums, filenames, URLs, and profile defaults should be centralized.

No speculative version 2 abstraction is required if a compact version 1 design
preserves these responsibilities.

## Testing direction

Tests for this feature should not download large production images or launch a
real VM. They should cover:

- manifest parsing and validation;
- exact-version and moving-channel resolution;
- cache hits, misses, and checksum failures;
- cleanup after interrupted download or decompression;
- creation of overlays without modifying the base;
- preservation of existing instance disks;
- backing-file path handling and relocation failures;
- concurrent-instance MAC and port behavior;
- prevention of silent instance rebasing; and
- safe cache cleanup with dependent overlays.

Small synthetic QCOW2 fixtures or mocked process execution should be used for
unit tests. Real QEMU and release-asset checks belong in explicit integration
tests.

## Open questions

1. Should the primary workflow be `run VERSION`, `create --image VERSION`, or
   both with clearly distinct persistence semantics?
2. Where should the signed or checksummed image catalog live?
3. Which compression format provides the best portability and release size?
4. How should private-repository release authentication work during early
   development?
5. How should overlays refer to cached bases while remaining relocatable?
6. Which guest identities and authentication data must be regenerated on first
   run?
7. How should concurrent instances receive unique MAC addresses and host ports?
8. What constitutes a reproducible and auditable official image build?
9. How should cached bases be garbage-collected without breaking overlays?
