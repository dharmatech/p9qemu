# p9qemu

`p9qemu` is a small, transparent command-line utility for installing and
running [9front](https://9front.org/) virtual machines with QEMU. It keeps each
VM in an ordinary directory and prints the exact QEMU command before every
launch, so the underlying configuration remains visible and copyable.

The project is in early version 1 development. Installation, startup, and guest
networking have been tested on Ubuntu under WSL with KVM and on native Windows
11 with TCG software emulation. The opt-in Windows WHPX profile has also passed
boot and desktop-responsiveness testing on the development host; its remaining
installation, networking, and broader compatibility checks are still pending.

## Prerequisites

- [uv](https://docs.astral.sh/uv/)
- QEMU with `qemu-img` and `qemu-system-x86_64` on `PATH`

On Ubuntu, install QEMU with:

```console
$ sudo apt install qemu-system-x86 qemu-utils
```

For Windows, use the [official QEMU download
guidance](https://www.qemu.org/download/#windows) and ensure the QEMU directory
is on `PATH`.

If QEMU is missing, `p9qemu` detects that before changing anything and displays
the appropriate installation guidance.

## Install p9qemu

Install the current GitHub version as an isolated command-line tool:

```console
$ uv tool install git+https://github.com/dharmatech/p9qemu.git
```

The repository is private during early development, so this currently requires
repository access. It will become a one-command public installation when the
repository is opened. A future package release will support the shorter
`uv tool install p9qemu` form.

If uv reports that its tool directory is not on `PATH`, run:

```console
$ uv tool update-shell
```

## Install 9front

Create one directory for each VM instance, then run the installer:

```console
$ mkdir my-9front
$ cd my-9front
$ p9qemu install
```

`p9qemu install`:

1. verifies that QEMU is available;
2. downloads and verifies the configured 9front installation archive;
3. safely decompresses the ISO into the per-user cache;
4. creates a sparse `30G` `9front.qcow2.img` disk if it does not exist;
5. prints the exact QEMU command; and
6. starts the 9front installer.

An existing disk is reused and is never overwritten. The download is shared by
all instances through the normal per-user cache location.

To inspect all planned actions without downloading, creating a disk, or
launching QEMU:

```console
$ p9qemu install --dry-run
```

Useful installer options include:

```text
--disk PATH       Disk-image path
--disk-size SIZE  Size of a newly created disk (default: 30G)
--memory MIB      Guest memory in MiB (default: 1024)
--accel MODE      auto, kvm, whpx, or tcg
--iso-url URL     Override the installation archive URL
--iso-sha256 HEX  Checksum for an overridden archive
--dry-run         Validate and show actions without changing state
--quiet           Suppress routine p9qemu output
```

The built-in archive is pinned to the SHA-256 digest published in its GitHub
release metadata. An overridden URL without `--iso-sha256` produces a warning.

## Start an installed VM

After installation, return to the same instance directory and run:

```console
$ p9qemu start
```

The default runtime profile uses 2048 MiB of memory, VirtIO networking, VirtIO
SCSI storage, and the localhost-only port forwards established by the original
`9front-notes` scripts. On Linux, `--accel auto` uses KVM when `/dev/kvm` is
accessible and otherwise uses TCG. Windows `auto` currently uses the proven TCG
profile while WHPX is validated against stock 9front.

Windows users may explicitly test hardware acceleration with:

```console
> p9qemu start --accel whpx
```

Before changing instance state, p9qemu verifies that the installed QEMU binary
advertises WHPX support. The Windows Hypervisor Platform feature must also be
enabled for QEMU to initialize WHPX. Explicit WHPX mode has no TCG fallback, so
the test cannot silently run under software emulation. Use `--accel tcg` to
force the portable known-working profile.

The experimental WHPX profile uses `kernel-irqchip=off` so QEMU emulates the
interrupt controller in userspace. Bare WHPX hung stock 9front during LAPIC
setup; disabling the kernel irqchip allowed it to boot, but only after a long
delay and with severe input and display latency. An Agent9 comparison guest
running under confirmed WHPX was responsive and used two virtual CPUs among its
other differences. Adding `-smp 2` brought up the second CPU in stock 9front but
did not improve responsiveness. Agent9 also explicitly selects SDL, so the next
single-variable experiment added `-display sdl`; stock 9front then booted
quickly and was highly responsive. Removing `-smp 2` while retaining SDL
produced the same fast, responsive result, demonstrating that a second virtual
CPU is unnecessary for this compatibility profile. The resulting minimal
profile is `-accel whpx,kernel-irqchip=off -display sdl`. It keeps p9qemu's
proven storage, networking, and memory settings, and it still has no TCG
fallback.

The complete development-host test matrix, including unsuccessful profiles and
the Agent9 comparison, is recorded in
[`docs/design/03-windows-whpx-experiments.md`](docs/design/03-windows-whpx-experiments.md).

Use `p9qemu start --dry-run` to display the resolved command without launching
the VM. QEMU inherits the terminal normally; `p9qemu` never executes commands
through a shell.

## Development

Clone the repository and let uv create the locked development environment:

```console
$ git clone https://github.com/dharmatech/p9qemu.git
$ cd p9qemu
$ uv sync
$ uv run pytest
$ uv run ruff check .
$ uv run ruff format --check .
```

Run the checked-out CLI with:

```console
$ uv run p9qemu --help
```

Tests never download the production ISO, create a real large disk, launch a VM,
or require QEMU to be installed.

Design notes live in [`docs/design`](docs/design), including the
[Windows acceleration experiment log](docs/design/03-windows-whpx-experiments.md).
