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
    9front.qcow2
  auth/
    9front.qcow2
  experiments/
    9front.qcow2
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

The initial default disk is `9front.qcow2`, with a virtual size of `30G`:

```console
$ qemu-img create -f qcow2 9front.qcow2 30G
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
  -drive if=none,id=vd0,file=9front.qcow2 \
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

## Executable discovery

Version 1 requires `qemu-img` and `qemu-system-x86_64`. Discover them through
`PATH` using standard Python facilities. When missing, name the executable and
explain that QEMU must be installed and placed on `PATH`.

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

The standard development workflow is:

```console
$ uv sync
$ uv run pytest
```

Development tools belong in a development dependency group. Runtime
dependencies should remain empty unless a compelling need appears. Continuous
integration can later test Windows, Linux, and macOS.

## Non-goals for version 1

- Starting an installed VM as a separate workflow
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

After installation works reliably on Windows and Linux, the likely next feature
is starting an installed image without the ISO:

```console
$ p9qemu start
```

Commands such as `create`, `download`, `command`, `backup`, or `drawterm` should
be added only in response to concrete workflows. `p9qemu` should remain a
focused, comprehensible utility rather than becoming a general VM manager.

## Open questions

1. What is the authoritative SHA-256 digest of the compressed archive and/or
   unpacked ISO?
2. Should the default disk be `9front.qcow2`, `9front.qcow2.img`, or another
   existing convention?
3. Should `--dry-run` also validate cached artifacts and installed programs?
4. Should the original fixed MAC address remain, be derived per instance, or be
   configurable before concurrent VMs are supported?
5. What minimum Python version balances modern standard-library features with
   host availability?

These do not block scaffolding. Their resolutions should be recorded in later
numbered design notes or updates to this document.
