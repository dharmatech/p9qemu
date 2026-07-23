# Private Ready-Image Checkpoints

This guide documents the current manual workflow for making inexpensive,
private checkpoints from P9QEMU ready-image instances. P9QEMU does not yet
provide a managed checkpoint command.

## Storage model

An instance created by `p9qemu image create` contains:

```text
instance-name/
  disk.qcow2
  instance.json
```

`disk.qcow2` stores only writes made after the published standalone image. Its
backing path points directly to that immutable image in the content-addressed
per-user cache. `instance.json` names `disk.qcow2` relatively and records the
absolute cache entry and base-image identity.

Copying the complete instance directory therefore creates a sibling overlay
with the same state at the moment of the copy. It does not create a backing
chain through the source instance:

```text
immutable cached base
  |-- dev/disk.qcow2
  |-- checkpoint-010-git-ready-private/disk.qcow2
  `-- checkpoint-020-toolchain-ready-private/disk.qcow2
```

Deleting an earlier sibling checkpoint does not break a later one. Every
sibling still depends on the shared cached base.

## Create a checkpoint

First finish guest writes and shut down from 9front:

```text
fshalt
```

Wait until QEMU exits completely. Never copy a live writable QCOW2.

On Windows PowerShell, from the directory containing `dev`:

```powershell
Copy-Item `
    -LiteralPath .\dev `
    -Destination .\checkpoint-020-toolchain-ready-private `
    -Recurse
```

On Linux:

```sh
cp -a --sparse=always \
    ./dev \
    ./checkpoint-020-toolchain-ready-private
```

Verify the copy without launching QEMU:

```console
p9qemu start \
    --instance checkpoint-020-toolchain-ready-private \
    --dry-run
```

Use the appropriate explicit accelerator only when needed, such as
`--accel whpx` for the tested opt-in Windows profile.

## Naming and lineage

Keep one clearly mutable working instance and give frozen checkpoints ordered,
semantic names:

```text
dev/
checkpoint-010-git-ready-private/
checkpoint-020-toolchain-ready-private/
checkpoint-030-project-build-ready-private/
```

Increments of ten leave room for an intermediate milestone. Numeric order is a
human convention, not a technical backing relationship. If work branches, keep
a small private checkpoint ledger recording each checkpoint's parent, creation
date, guest repository commits, configuration, and smoke tests.

Do not boot a frozen checkpoint for routine work because guest writes would
change it. To resume or branch from one, copy that checkpoint to a new working
directory and boot the copy.

## Renaming and moving

With the current direct-to-cache instance format, a halted instance directory
may be renamed or moved on the same host. The root directory path is not stored
in `instance.json`, and the QCOW2 backing path names the cache base rather than
the sibling instance.

After any move or rename, run `p9qemu start --instance ... --dry-run` to
reverify the metadata, immutable base, and backing relationship.

This guarantee would not apply to a manually created multi-level QCOW2 whose
backing file is another instance overlay. Renaming that parent would invalidate
the child's recorded path. Current `p9qemu image create` does not build such
chains.

External scripts, shortcuts, or notes containing the old instance path must
also be updated.

## Cache and backup boundary

A checkpoint is not a standalone backup. Moving, deleting, or corrupting the
content-addressed cache base breaks every dependent sibling, and P9QEMU will
refuse to start them.

For normal local development, sharing the verified cache is intentional and
space-efficient. A future export workflow may create standalone artifacts, but
that is outside the current public CLI.

## Credential boundary

Copies inherit all guest contents, including SSH private keys, tokens, shell
history, private source, and credentials. If sensitive material has ever
existed in an instance, treat that checkpoint and every descendant as private.
Deleting the visible credential later does not establish a clean lineage.

Never upload a private checkpoint as a release asset. Build any public
demonstration image independently from a known-clean base using public inputs
and a reproducible recipe. The future enforcement model is described in
[Private Instance Distribution Safety](../design/11-private-instance-distribution-safety.md).
