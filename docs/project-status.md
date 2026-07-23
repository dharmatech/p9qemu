# P9QEMU Project Status

## Snapshot

Last reviewed: 2026-07-23

This is a concise handoff snapshot, not the ultimate source of truth. Before
acting on it, inspect the current working tree, recent commits, relevant tests,
checked-in manifests, and public GitHub releases. See
[the repository guidance](../AGENTS.md) for the complete precedence order.

P9QEMU remains in early version 1 development, but its principal installation,
startup, ready-image, and Drawterm-ready workflows have completed real Windows
and Linux acceptance and are being used by downstream projects. No next
implementation objective is currently selected. A successful private
native-Windows prototype now informs, but does not yet authorize, the smallest
possible public concurrent-management slice.

## Implemented user workflows

The public CLI currently provides:

- `p9qemu install` for the pinned 9front installation media;
- `p9qemu start --disk ...` for standalone installed disks;
- `p9qemu image create MANIFEST_URL INSTANCE_DIR` for verified ready images;
- `p9qemu start --instance ...` for ready-image instances;
- `--dry-run`, `--quiet`, memory, disk, media, and acceleration controls; and
- aligned human-readable summaries plus the exact platform-native QEMU command.

Ready-image acquisition verifies a bounded external manifest, compressed
archive, internal bundle, standalone QCOW2, and content-addressed cache entry.
Each instance is a small writable overlay backed directly by that immutable
cached base. Startup reverifies the saved manifest identity, cached image, and
overlay relationship before launching QEMU.

Internal tools also automate the pinned 11554 installation, prepare stock and
Drawterm variants, validate disposable overlays, rotate the Drawterm
demonstration password, construct release-candidate bundles, and generate
external ready-image manifests. These are engineering workflows, not additional
public CLI commands.

## Published artifacts

The repository is public at
[dharmatech/p9qemu](https://github.com/dharmatech/p9qemu).

| Artifact | Public state | Canonical entry point |
| --- | --- | --- |
| 9front 11554 AMD64 installation media | Immutable GitHub prerelease mirror | [`media-9front-11554`](https://github.com/dharmatech/p9qemu/releases/tag/media-9front-11554) |
| 9front 11554 AMD64 HJFS GMT stock image, candidate 002 | Immutable GitHub prerelease | [Stock image page](../images/p9qemu-9front-11554-amd64-hjfs-gmt-002/README.md) |
| 9front 11554 AMD64 HJFS GMT Drawterm image, revision 001 | Stable GitHub release and current Latest release | [Drawterm image page](../images/p9qemu-9front-11554-amd64-hjfs-gmt-drawterm-001/README.md) |

The installation-media prerelease is intentionally separate from application
and ready-image releases. The stock candidate remains a prerelease even though
it passed the required acceptance gates. The Drawterm derivative was promoted
without changing its immutable tag, manifest, archive, or image bytes.

## Validated hosts and profiles

- Ubuntu under WSL: installation and startup with KVM; ready-image public
  acquisition, overlay creation, graphical boot, networking, Drawterm, and
  clean shutdown.
- Native Windows 11: installation and startup with TCG; ready-image public
  acquisition, overlay creation, graphical boot, networking, Drawterm, and
  clean shutdown with TCG and explicit WHPX.
- Windows WHPX compatibility profile:
  `-accel whpx,kernel-irqchip=off -display sdl`, with no silent fallback.

Windows `auto` remains the conservative TCG profile because WHPX compatibility
can vary by host, QEMU version, and guest. Linux `auto` selects KVM only when
available. macOS remains structurally accommodated but unverified.

The Drawterm image boots unattended as a CPU/auth server, retains serial
diagnostics, exposes services only through localhost forwards, and uses the
documented public demonstration credential. That credential must be changed
before broadening network exposure.

## Important current boundaries

- Version 1 has one built-in installation-media release and no media catalog or
  version selector.
- Ready images are selected by explicit immutable manifest URL; there is no
  public catalog or moving alias in the CLI.
- Ready-image instances depend on their cached base and are not standalone
  backups.
- P9QEMU does not yet provide managed clone, checkpoint, snapshot, delete,
  garbage-collection, cache-relocation, export, or publication commands.
- Current local promotion tooling builds and verifies release candidates but
  does not itself upload assets or create/promote GitHub releases.
- Private-instance sensitivity metadata and fail-closed publication gates are
  future design only.
- A private native-Windows TCG prototype ran two Drawterm-ready instances
  concurrently with the complete forward map repeated on `127.0.0.20` and
  `127.0.0.21`. Public address selection, Linux and WHPX qualification,
  automatic allocation, and guest-to-guest lab networking remain future work.
- Automated installation is pinned to one known installer interaction and must
  be requalified for every new 9front release.

## Current operating practices

- During active development, an editable uv tool installation may point
  `p9qemu` at the working tree.
- End-user acceptance reinstalls the public Git source before testing.
- Private development checkpoints are copied only after clean guest shutdown,
  remain siblings over the immutable cache base, and are never published when
  their lineage has contained credentials.
- Large image gates begin and end with Windows free-space, WSL VHDX, and WSL
  filesystem measurements.

At this review, the complete pytest suite and `ruff check` pass.
`ruff format --check .` reports seven previously committed Python files that
would be reformatted. That formatting baseline predates this documentation
milestone and should be resolved as a separate, reviewable cleanup rather than
mixed into unrelated changes.

See the [private-instance checkpoint guide](guides/private-instance-checkpoints.md)
and the [design-note index](design/README.md).

## Possible next directions

No item below is an active commitment:

- managed private-instance clone/checkpoint metadata;
- fail-closed export and distribution-safety enforcement;
- qualification of the next 9front installation-media release;
- a public ready-image catalog or controlled aliases;
- broader WHPX host testing;
- an explicit loopback-only host-forward address for `p9qemu start`, preserving
  `127.0.0.1` as the default, after the Windows prototype is reviewed and the
  corresponding Linux behavior is qualified;
- shared-Ethernet multi-VM networking and downloadable labs; and
- a repo-local release skill after the qualification/promotion workflow repeats
  and stabilizes.

When one direction is selected, update this section with the concrete objective
and keep detailed execution state in the relevant issue, plan, or design note.
