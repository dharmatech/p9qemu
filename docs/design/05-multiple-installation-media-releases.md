# Multiple Installation Media Releases

## Status

Future direction. Version 1 intentionally supports one built-in 9front
installation release. This design should be implemented when p9qemu accepts a
second validated release into its media collection, or earlier only if a real
user need appears.

There is no requirement to add an older release merely to exercise this
design. Unit tests can model multiple catalog entries without publishing or
supporting media that the project would not otherwise recommend.

## Decision summary

When multiple releases are available, p9qemu should provide both a simple
recommended default and an explicit release selector:

```console
$ p9qemu install
$ p9qemu install --release 11554
```

Plain `p9qemu install` should resolve to the release that the project has
deliberately marked as recommended. It must not discover and select the newest
upstream release dynamically. An explicit `--release` value should select one
immutable, checksum-pinned catalog entry.

The likely discovery interface is:

```console
$ p9qemu media list
$ p9qemu media show 11554
```

`--release` is preferable to `--version`: `p9qemu --version` describes the
p9qemu program, while `--release` describes the 9front installation media.

## Motivation

New 9front releases should not make older, known-working installations
impossible to reproduce. At the same time, requiring every new user to choose
from a release list would weaken the project's simple on-ramp.

The design therefore needs to preserve all of the following:

- one-command installation for new users;
- deliberate selection of a historical release;
- exact, immutable media URLs and checksums;
- a visible distinction between "newest upstream" and "recommended by
  p9qemu";
- coexistence of different releases in the media cache; and
- enough provenance to explain how an installed image was created.

## Goals

- Keep `p9qemu install` working without new required arguments.
- Let users name an exact supported 9front release.
- Resolve every built-in release without an untrusted or mutable network
  lookup.
- Pin compressed and decompressed artifact checksums.
- Retain older release assets and provenance after a new default is promoted.
- Make the resolved release visible in normal and dry-run output.
- Preserve `--iso-url` as an advanced custom-media escape hatch.

## Non-goals

- Automatically choosing the numerically newest upstream release.
- Treating newly published upstream media as supported before validation.
- Replacing or mutating an asset under an existing release tag.
- Implementing a general 9front package or system updater.
- Inferring the installed guest release by inspecting an arbitrary disk.
- Adding multi-architecture support before p9qemu supports another QEMU target.

## Proposed command behavior

### Recommended release

```console
$ p9qemu install
Installation media: 9front 11554 amd64 (recommended)
Using cached installation ISO: ...
```

The recommended release is a source-controlled project decision. Updating
p9qemu may update that decision; running the same installed p9qemu version
should not silently resolve to different media because an upstream or GitHub
"latest" pointer changed.

### Exact release

```console
$ p9qemu install --release 11554
Installation media: 9front 11554 amd64
```

An explicit selection must resolve to exactly one catalog entry. If that entry
cannot be downloaded or verified, p9qemu should fail. It must not fall forward
to a newer release or backward to an older one.

### Discovery

```console
$ p9qemu media list
RELEASE  ARCH   STATUS
11554    amd64  recommended
11620    amd64  available
```

The second row is an illustrative future release, not a statement that p9qemu
currently publishes or supports that release.

`p9qemu media show 11554` should display the resolved asset URL, archive and
ISO checksums, cache filenames, provenance-document location, and whether the
release is recommended. These commands should be read-only.

Exact subcommand names can be finalized during implementation. `media list`
is preferred over a top-level `releases` command because the catalog describes
installation artifacts, not p9qemu program releases.

## Media catalog

The current single-release constants should eventually become a validated
catalog. Each entry needs at least:

- an internal media identifier such as `9front-11554-amd64`;
- the upstream 9front release number;
- guest architecture;
- archive and decompressed ISO filenames;
- immutable release tag and asset URL;
- archive byte size and SHA-256 digest;
- decompressed ISO byte size and SHA-256 digest;
- provenance manifest path;
- availability status; and
- any compatibility or retirement note.

The catalog also needs exactly one recommended media identifier. A conceptual
shape is:

```json
{
  "schema": 1,
  "recommended": "9front-11554-amd64",
  "media": [
    {
      "id": "9front-11554-amd64",
      "release": "11554",
      "architecture": "amd64",
      "status": "available",
      "asset_url": "https://github.com/dharmatech/p9qemu/releases/download/media-9front-11554/9front-11554.amd64.iso.gz",
      "archive_sha256": "5aaf54327b4bb73a17e192488dc3e65d9d8e526728732e2fdf402bccb8c60236",
      "iso_sha256": "1dcfbb3ec221307329545a37d1562beeea1f4174f6df80d245e0a222893b3bb6"
    }
  ]
}
```

The per-media manifests under `media/` remain the detailed provenance record.
Implementation should establish one authoritative source and validate or
generate any compact runtime catalog from it so duplicated fields cannot drift.
The runtime data must be packaged with p9qemu; ordinary resolution should not
depend on fetching a mutable remote catalog.

Release number alone is sufficient for the initial CLI because p9qemu currently
targets amd64 through `qemu-system-x86_64`. Internal identifiers should include
architecture so a future `--arch` option can be added without redesigning the
catalog.

## Promoting a new recommended release

A new upstream release should become the p9qemu default only after a deliberate
promotion workflow:

1. Obtain the media from the documented upstream location.
2. Record compressed and decompressed sizes and SHA-256 digests.
3. Publish a byte-for-byte mirror under a new immutable media tag.
4. Add and review its provenance manifest and checksum file.
5. Validate downloading through p9qemu from an empty cache.
6. Complete installation, startup, and basic networking tests on the supported
   host profiles appropriate to that release.
7. Add the catalog entry as available.
8. Change the source-controlled recommended identifier in a separate reviewed
   change.

Publishing an entry and making it recommended are intentionally separate
decisions. A new release may remain available for opt-in testing before it is
promoted.

Media tags and assets are immutable. If bytes change for any reason, publish a
new tag or corrected identifier and retain the old provenance record.

## Interaction with custom media options

The built-in and custom-media paths should remain distinct:

- `p9qemu install` uses the recommended catalog entry.
- `p9qemu install --release RELEASE` uses an exact catalog entry.
- `p9qemu install --iso-url URL` uses custom media.
- `--release` and `--iso-url` are mutually exclusive.
- `--iso-sha256` belongs to the custom URL path; catalog entries always supply
  their pinned checksum.

Unknown releases should produce an actionable error and list the available
release identifiers. A catalog checksum failure must remain fatal.

## Cache and instance behavior

Release-specific filenames already allow multiple installation archives and
ISOs to coexist in the shared cache. Catalog validation should reject entries
whose cache filenames collide while referring to different bytes.

Users should create a separate VM directory for each installed system. A
future instance metadata file should record at least:

- the selected media identifier and release;
- the archive and ISO checksums;
- the target disk path and creation time; and
- the QEMU installation command or profile.

This metadata records installation intent and provenance; it does not claim
that the user completed the installer or that the guest disk was never changed.
If an existing disk's recorded media differs from a newly selected release,
p9qemu should warn or require an explicit override rather than silently
presenting a different installer.

## Compatibility and lifecycle

Older supported entries should remain selectable after the default changes.
They may later be marked deprecated or unavailable, but their metadata,
checksums, and explanation should remain in Git. Removal from the recommended
set must not imply deletion of an immutable GitHub release asset.

A future catalog schema change must preserve the behavior of existing release
identifiers. Aliases can be added if upstream naming changes, but normal output
should always show the canonical resolved identifier.

## Implementation trigger and plan

Implementation should begin when the project is ready to publish or support a
second installation release. At that point:

1. Introduce catalog parsing and validation while preserving the current
   single-release default.
2. Add `--release` and reject conflicting custom-media options.
3. Add read-only media discovery commands.
4. Migrate the existing 11554 constants into the catalog without changing its
   URL, filenames, or checksums.
5. Test default, explicit, unknown, conflicting, cache-collision, and checksum
   behavior with synthetic catalog fixtures.
6. Add the second real media entry and perform the promotion workflow.

An older real release may be used for optional integration testing, but it is
not necessary to implement or validate the catalog abstraction.

## Testing expectations

The eventual implementation should include tests proving that:

- plain install resolves the single recommended entry;
- explicit selection is deterministic;
- unknown and ambiguous release selections fail clearly;
- catalog URLs and checksums reach the existing media preparation path;
- custom URL and release selectors cannot be combined;
- distinct releases use distinct cache paths;
- a failed download or checksum never falls back to another release;
- list and show commands are read-only; and
- Linux and Windows dry-run output names the resolved release.
