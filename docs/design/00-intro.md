# p9qemu: Introduction and Initial Design

## Status

Initial design for version 1.

## Overview

`p9qemu` is a small cross-platform command-line utility for creating and
installing 9front virtual machines with QEMU. It grows out of the transparent,
platform-specific scripts in `9front-notes`, but centralizes the shared logic
that would otherwise be duplicated across Bash and PowerShell.

The utility must not hide QEMU. It will print the command it executes and offer
a dry-run mode so users can inspect, copy, and adapt that command.

## Version 1 workflow

The initial user experience is:

```console
$ mkdir my-9front
$ cd my-9front
$ p9qemu install
```

`p9qemu install` will:

1. Treat the current directory as one VM instance directory.
2. Download the configured 9front installation archive when it is not cached.
3. Safely decompress and cache the ISO.
4. Verify a known SHA-256 digest when configured.
5. Create a sparse QCOW2 disk when it does not exist.
6. Print the exact QEMU command.
7. Start QEMU with the ISO as boot media and the disk as the install target.

Existing disk images must never be overwritten.

## Platforms

Version 1 explicitly supports Windows and Linux. The implementation should use
portable Python and leave room for macOS, but macOS will remain unverified until
it can be tested.

Platform-specific behavior—cache locations, executable discovery, and command
display—should be isolated. Subprocesses must be invoked with argument lists,
never shell command strings or `shell=True`.

## Packaging

The project will be a proper Python package with `pyproject.toml` and a console
entry point named `p9qemu`. It will use `uv` for development, testing, building,
and recommended installation:

```console
$ uv tool install .
$ p9qemu --help
```

Developers may install the clone in editable mode:

```console
$ uv tool install --editable .
```

`uv` is an installer and development tool, not an application runtime
dependency. Version 1 should use only the Python standard library.

## Instance and cache model

The source checkout and VM data are separate. Users install `p9qemu` once and
create ordinary directories for their instances:

```text
9front-vms/
  main/
    9front.qcow2.img
  auth/
    9front.qcow2.img
  experiments/
    9front.qcow2.img
```

Creating another VM means creating another directory, not cloning the source
repository again. Instance-specific state belongs in the current directory.
Immutable installation media belongs in a shared per-user cache:

- Linux: the appropriate XDG cache directory, normally `~/.cache/p9qemu`
- Windows: the user's local application-data directory
- macOS, when supported: its conventional per-user cache directory

The application must not depend on being run from its source checkout.

## Installation media

Until `p9qemu` publishes its own release asset, use:

```text
https://github.com/dharmatech/9front-notes/releases/download/v0.0.1/9front-11554.amd64.iso.gz
```

The initial filenames are:

```text
9front-11554.amd64.iso.gz
9front-11554.amd64.iso
```

The URL, release identifier, filenames, and expected checksum must be
centralized. Moving the asset to the eventual `p9qemu` release should require a
small, obvious change.

Downloads and decompression must write temporary files and atomically rename
them on success. Interrupted work must not be mistaken for valid media. Cached
media should be reused.

## Disk image

The initial default disk is `9front.qcow2.img`, with a virtual size of `30G`:

```console
$ qemu-img create -f qcow2 9front.qcow2.img 30G
```

QCOW2 images are sparse, so this does not immediately consume 30 GB. A requested
size applies only to a newly created disk and must never resize or replace an
existing image implicitly.

## QEMU invocation

The initial QEMU configuration preserves the known working behavior from
`9front-notes`:

- `qemu-system-x86_64`
- 1024 MiB memory by default
- user-mode networking
- a VirtIO network adapter
- a VirtIO SCSI controller
- the QCOW2 image as a SCSI hard disk
- the ISO as a bootable SCSI CD-ROM

The reference command is conceptually:

```console
qemu-system-x86_64 -m 1024 \
  -net nic,model=virtio,macaddr=00:20:91:37:33:77 -net user \
  -device virtio-scsi-pci,id=scsi \
  -drive if=none,id=vd0,file=9front.qcow2.img,format=qcow2 \
  -device scsi-hd,drive=vd0 \
  -drive if=none,id=vd1,file=9front-11554.amd64.iso,format=raw \
  -device scsi-cd,drive=vd1,bootindex=0
```

Internally it must be represented as `list[str]`. Rendering a copyable command
for the host platform is separate from execution. Normal operation prints the
command before launching it.

## Initial CLI

The public interface begins with:

```console
p9qemu install [OPTIONS]
p9qemu start [OPTIONS]
```

Likely initial options:

```text
--disk PATH       Disk-image path
--disk-size SIZE  New disk size (default: 30G)
--memory MIB      Guest memory in MiB (default: 1024)
--iso-url URL     Override the installation-media URL
--dry-run         Show planned actions without changing state
```

Defaults should make the ordinary command require no options. Errors should be
concise and actionable. Missing QEMU programs, invalid arguments, download or
decompression failures, checksum mismatches, and filesystem conflicts must
produce nonzero exit codes without corrupting existing data.

`p9qemu start` runs an existing instance without the installation ISO. It uses
2048 MiB by default and preserves the localhost-only port forwards from the
field-tested Linux scripts. Both commands accept
`--accel auto|kvm|whpx|tcg`; `auto` selects KVM on Linux when `/dev/kvm` is
accessible and otherwise uses TCG software emulation. Windows `auto` remains on
the proven TCG profile while explicit WHPX is validated. Both commands print
the resolved QEMU command before launch and support `--dry-run` and `--quiet`.

Explicit `--accel whpx` is available only on Windows and first verifies that
the installed QEMU binary advertises WHPX through `-accel help`. It intentionally
has no fallback so tests cannot silently run under TCG. Bare WHPX initialized on
the initial Windows 11 test host but stock 9front hung during LAPIC setup. The
second controlled profile therefore used
`-accel whpx,kernel-irqchip=off`, changing only interrupt-controller placement.
That profile eventually booted but was extremely slow and unresponsive. Agent9
then demonstrated responsive, confirmed WHPX operation on the same host with
the same irqchip setting and two virtual CPUs. Adding only `-smp 2` brought up
the second CPU in stock 9front but did not improve responsiveness. Because
Agent9 also explicitly uses the SDL display backend, the next controlled
profile adds only `-display sdl`, while retaining the current CPU, storage, and
network settings.

## Executable discovery

Version 1 requires `qemu-img` and `qemu-system-x86_64`. Discover them through
`PATH` using standard Python facilities. When either program is missing, report
all missing programs together, explain that QEMU must be installed and placed
on `PATH`, and provide concise, actionable installation guidance for recognized
supported platforms.

For a recognized Ubuntu host, the error should show the verified native package
installation command. On Windows, it should point to trusted QEMU installation
instructions; a package-manager command should be shown only after its package
identifier and behavior have been verified. Unknown Linux distributions should
receive a generic message that names the required executables rather than an
unreliable guessed command.

For example, the shape of an Ubuntu error is:

```text
p9qemu: qemu-img and qemu-system-x86_64 were not found.

Install QEMU on Ubuntu with:

  sudo apt install qemu-system-x86 qemu-utils

Then run p9qemu again.
```

Installation advice is informational in version 1. `p9qemu` must not invoke
`sudo`, a system package manager, or a graphical installer. Host detection and
advice should be isolated from executable discovery so the advice can evolve
without changing QEMU command construction.

Hard-coded QEMU installation paths and automatic QEMU installation are outside
the version 1 scope.

## Transparency requirements

- Keep QEMU command construction in one small, readable function or module.
- Print the command before execution.
- Support `--dry-run` from the beginning.
- Centralize and document defaults.
- Never execute through a shell.
- Avoid runtime dependencies initially.
- Document the equivalent native QEMU command.

A dedicated command-printing subcommand may be added later if it is clearer
than `--dry-run`.

## Provisional layout

```text
p9qemu/
  docs/
    design/
      00-intro.md
  src/
    p9qemu/
      __init__.py
      cli.py
      download.py
      instance.py
      qemu.py
  tests/
  pyproject.toml
  README.md
  uv.lock
```

This is a guide, not a requirement to create tiny modules prematurely. A compact
implementation is preferable while the application has one command.

## Testing

Tests must not download the production ISO, create a real large disk, launch a
VM, or require QEMU to be installed. The design should permit injection or
mocking of downloads, paths, executable discovery, and process execution.

Tests should cover:

- CLI parsing and defaults
- Windows and POSIX cache selection
- ISO cache hits and misses
- cleanup after interrupted downloads
- decompression and checksum verification
- disk creation and preservation of existing disks
- QEMU argument construction
- Windows and POSIX command rendering
- dry-run behavior
- missing executable errors
- recognized-platform installation guidance
- generic guidance for unknown platforms

The standard development workflow is:

```console
$ uv sync
$ uv run pytest
```

Development tools belong in a development dependency group. Runtime
dependencies should remain empty unless a compelling need appears. Continuous
integration can later test Windows, Linux, and macOS.

## Non-goals for version 1

- A global registry of named instances
- Complex configuration files
- Backups and snapshots
- Disk resizing
- Drawterm management
- Mounting guest filesystems
- Installing QEMU
- Automatically updating 9front
- A GUI
- Standalone native executables

## Expected evolution

Downloadable post-install base images are the likely next major workflow; they
are described in `01-downloadable-post-install-images.md`. Commands such as
`create`, `download`, `command`, `backup`, or `drawterm` should be added only in
response to concrete workflows. `p9qemu` should remain a focused,
comprehensible utility rather than becoming a general VM manager.

## Resolved version 1 decisions

1. The compressed archive's SHA-256 digest is
   `5aaf54327b4bb73a17e192488dc3e65d9d8e526728732e2fdf402bccb8c60236`,
   as published in its GitHub release metadata.
2. The default disk is `9front.qcow2.img`, matching the established scripts.
3. `--dry-run` validates arguments, paths, cached archives, and installed QEMU
   programs while performing no downloads, decompression, disk creation, or VM
   launch.
4. Version 1 retains the established fixed MAC address. Unique per-instance
   addresses are deferred until concurrent instances are supported.
5. Version 1 requires Python 3.11 or newer.

Windows hardware acceleration remains opt-in while the conservative WHPX
profile is tested. After successful installation and runtime testing, Windows
`auto` may prefer WHPX with an ordered TCG fallback.
