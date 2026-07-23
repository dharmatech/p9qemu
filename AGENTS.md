# P9QEMU Repository Guidance

## Start here

Read only the material needed for the task:

- [README.md](README.md) is the user-facing command and behavior contract.
- [docs/project-status.md](docs/project-status.md) is the concise current-state
  handoff.
- [docs/design/README.md](docs/design/README.md) routes to the relevant design
  record without requiring every design note to be loaded.
- `images/*/README.md` and `media/*/README.md` are the authoritative
  human-readable records for published artifacts.
- Machine-readable manifests under `images/manifests` and `media` bind exact
  release identities, sizes, and digests.

Use this source-of-truth order when statements differ:

1. current code, tests, manifests, and public release records;
2. the user-facing README and artifact-specific documentation;
3. the dated project-status snapshot; and
4. design notes, which also preserve historical decisions and experiments.

Before continuing earlier work, inspect `git status`, recent commits, and the
relevant tests. Do not assume a dated checkpoint is still the active state.

## Project contract

P9QEMU is a transparent Python CLI for installing and running 9front with QEMU
on Windows and Linux. Preserve these invariants:

- Print the exact QEMU command before launch unless quiet mode is selected.
- Build subprocess invocations as argument lists; never use `shell=True`.
- Keep normal defaults simple while retaining explicit overrides and dry runs.
- Never overwrite an existing disk, instance, cache entry, or release artifact.
- Treat published media, manifests, archives, and standalone bases as
  immutable.
- Keep ready-image bases in a verified content-addressed cache and writable
  guest state in per-instance QCOW2 overlays.
- Preserve the documented dry-run boundaries: installer dry-run is local-only,
  while ready-image creation dry-run may fetch only the bounded manifest.
- Keep the runtime dependency-free outside the Python standard library unless a
  deliberate design change is approved.

Windows `auto` uses TCG. WHPX is explicit and currently resolves to
`-accel whpx,kernel-irqchip=off -display sdl` with no fallback. Linux `auto`
uses KVM when available and otherwise TCG. Do not change these profiles without
focused command tests and appropriate live validation.

## Repository layout

- `src/p9qemu`: CLI, host/QEMU resolution, media, installation, ready-image,
  validation, and release logic.
- `tests`: isolated automated tests; keep external processes and network access
  injected or mocked.
- `tools`: internal installation, preparation, validation, packaging, and
  manifest-generation drivers.
- `media`: checked-in metadata for mirrored upstream installation media.
- `images`: answer files, runtime profiles, public manifests, build records,
  and user-facing ready-image pages.
- `docs/design`: implemented boundaries, evidence records, and future
  directions.
- `docs/guides`: current operational guidance.

## Development and verification

Use the uv-managed project environment:

```console
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Run focused tests while iterating, then run the complete suite before committing
code changes. Documentation-only changes require at least `git diff --check`
and validation of changed relative links. Run broader checks when documentation
changes executable examples or machine-readable contracts.

Tests must not download production media, create large real disks, launch a
live VM, require QEMU, or mutate per-user caches. Live Windows, WSL, QEMU, or
Drawterm validation is a separate explicitly scoped gate.

## VM, release, and credential safety

- Ensure the guest has completed `fshalt` and QEMU has exited before copying,
  renaming, inspecting, or packaging a writable image.
- Before a large Windows or WSL image operation, follow the host-free-space and
  WSL-VHDX checks in
  [docs/design/06-new-release-qualification.md](docs/design/06-new-release-qualification.md).
- Do not delete or move user VM directories, caches, WSL images, or ignored
  run artifacts unless the task explicitly places them in scope.
- Treat any instance that has contained an SSH key, token, private source, or
  other credential as permanently private for that lineage.
- Build public demonstration images from a known-clean base; do not sanitize a
  credential-bearing development image after the fact.
- Treat publication as a separate, explicit external action. Local build,
  validation, packaging, or manifest generation does not authorize uploading,
  replacing, or promoting a GitHub release.

See [the private checkpoint guide](docs/guides/private-instance-checkpoints.md)
and [the future distribution-safety design](docs/design/11-private-instance-distribution-safety.md).

## Documentation maintenance

Clearly label behavior as implemented, manually operated, experimental, or
future direction. Preserve unsuccessful experiments when they explain current
compatibility settings.

When a milestone changes supported commands, published artifacts, platform
validation, major limitations, or the selected next objective, update
`docs/project-status.md` in the same change. Keep it concise and dated rather
than turning it into a development transcript.

A repo-local skill is not currently required for general project context.
Consider one under `.agents/skills` only after a complex workflow—most likely
new-release qualification and promotion—has repeated enough to be stable.
