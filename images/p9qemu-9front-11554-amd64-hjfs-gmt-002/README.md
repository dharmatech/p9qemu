# 9front 11554 AMD64 HJFS GMT stock image

> **Prerelease candidate 002.** This exact image is immutable and has passed
> graphical boot, networking, and clean-shutdown tests with Linux KVM, Windows
> TCG, and the opt-in Windows WHPX profile.

This is a stock-software post-install 9front system with qualified graphical
and serial boot settings. It is for users who want to begin with a working VM
instead of running the installer, and it does not enable Drawterm or add
post-install software.

## Quick start

Install [QEMU and uv](../../README.md#prerequisites), then install P9QEMU if it
is not already available:

```text
uv tool install git+https://github.com/dharmatech/p9qemu.git
```

Open a terminal in the directory where you want to keep the VM and run:

```text
p9qemu image create https://github.com/dharmatech/p9qemu/releases/download/ready-9front-11554-amd64-hjfs-gmt-002/image.json 9front-11554
p9qemu start --instance 9front-11554
```

P9QEMU downloads and verifies the image, creates a small writable instance,
prints the exact QEMU command, and starts the VM. On the first boot, leave
9boot untouched and press Enter to accept the default `bootargs` and
`user[glenda]:` values. Rio then starts automatically.

Shut down cleanly from a 9front terminal:

```text
fshalt
```

## Optional Windows acceleration

Windows uses the portable TCG profile by default. On a system with Windows
Hypervisor Platform enabled, the tested opt-in profile is:

```text
p9qemu start --instance 9front-11554 --accel whpx
```

Explicit WHPX mode has no silent TCG fallback. P9QEMU prints the selected
profile before launching QEMU.

## Create another instance

Run the same `p9qemu image create` command with a different final name. P9QEMU
reuses the verified download and immutable cached base, then creates another
small, independent writable overlay. Changes in one instance do not affect the
base or any sibling instance.

## Image contents

- 9front release `11554`, AMD64
- 30 GiB virtual QCOW2 disk with HJFS
- user `glenda` and system name `cirno`
- `GMT` timezone
- graphical Rio console plus a retained serial-console channel
- no configured password, authentication secret, Drawterm access, or other
  additionally enabled remote service

Rio intentionally opens the usual stats window and two terminal windows. The
second terminal displays the retained serial-console channel; it is not a
leftover installation or validation process.

## Instance storage

The instance's `disk.qcow2` stores only changes from the immutable base in the
per-user P9QEMU cache. It starts very small and grows as the guest writes data.
Do not move or delete the shared cache while instances depend on it. Removing
an instance directory discards only that instance and does not remove the
shared base.

## Release and provenance

- [GitHub prerelease and assets](https://github.com/dharmatech/p9qemu/releases/tag/ready-9front-11554-amd64-hjfs-gmt-002)
- [Checked-in ready-image manifest](../manifests/p9qemu-9front-11554-amd64-hjfs-gmt-002.json)
- [Build profile and candidate record](../9front-11554-amd64-hjfs-gmt-reference-001/README.md)
- [Manifest, cache, and acceptance design record](../../docs/design/10-ready-image-manifest-and-cache.md)
