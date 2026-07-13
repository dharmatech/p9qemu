# QEMU Diagnostics and Host Installation

## Status

Future direction beyond version 1, with a limited version 1 requirement for
missing-executable detection and actionable installation guidance.

## Motivation

The shortest path to experimenting with Plan 9 should not assume that a new
user already understands QEMU packaging, executable names, or hardware
acceleration. `p9qemu` can diagnose the host and explain the next step while
remaining transparent about commands and system changes.

Version 1 will detect missing QEMU executables and print guidance. A later
version may add a dedicated diagnostic command and optionally offer to run a
verified native package-manager command with explicit user approval.

## Version 1 behavior

Version 1 requires `qemu-img` and `qemu-system-x86_64`. Before attempting disk
creation or VM launch, `p9qemu` should discover both through `PATH` using
standard Python facilities.

When programs are missing, one error should:

- list every missing executable;
- identify the detected platform when sufficiently confident;
- show a verified installation command for a recognized supported platform, or
  link to trusted instructions when that is safer;
- explain that the programs must be available on `PATH`;
- tell the user to rerun the original `p9qemu` command afterward; and
- exit nonzero without downloading media, creating a disk, or changing the
  host.

An Ubuntu message may take this form:

```text
p9qemu: qemu-img and qemu-system-x86_64 were not found.

Install QEMU on Ubuntu with:

  sudo apt install qemu-system-x86 qemu-utils

Then run p9qemu again.
```

Package names and commands are platform data and must be verified for the
supported OS release. `p9qemu` should not guess based only on the presence of a
package-manager executable. Linux distribution detection should use established
OS metadata such as `/etc/os-release` when available.

On Windows, version 1 may link to trusted QEMU installation guidance. A
`winget` command should be recommended only when its package identifier,
installed executable layout, PATH behavior, and supported QEMU version have
been tested. macOS guidance should remain qualified until macOS support is
verified.

Version 1 must never execute `sudo`, `apt`, `winget`, Homebrew, or another host
installer.

## Proposed `doctor` command

A later release may add:

```console
$ p9qemu doctor
```

The command should be read-only by default and produce a concise report such
as:

```text
Host: Ubuntu 26.04, x86_64
qemu-system-x86_64: missing
qemu-img: missing
KVM device: available
Recommended installation:
  sudo apt install qemu-system-x86 qemu-utils
```

Depending on platform and project maturity, diagnostics may include:

- host OS, release, and architecture;
- QEMU executable discovery and versions;
- `qemu-img` availability and version;
- availability and permissions for Linux KVM;
- tested Windows acceleration backends;
- whether configured host-forward ports are already occupied;
- cache location, accessibility, and available space;
- instance disk and backing-image integrity;
- whether the selected QEMU profile is supported; and
- a final ready/warning/error summary.

The command should distinguish a missing optional accelerator from a missing
required executable. Software emulation can remain usable even when KVM or a
Windows acceleration backend is unavailable.

The initial opt-in WHPX implementation queries `qemu-system-x86_64 -accel help`
before launch and fails clearly when that QEMU build does not advertise WHPX.
A bare WHPX runtime test subsequently initialized the accelerator but hung stock
9front during LAPIC setup. The second experiment disabled the kernel irqchip,
kept every other known-working VM setting intact, and retained strict
no-fallback behavior. It eventually reached Rio but had severe input and display
latency. A responsive Agent9 comparison run confirmed that WHPX and the same
irqchip setting can work on this host; its boot log also showed a second virtual
CPU. The third experiment added only `-smp 2` to stock 9front. The second CPU
initialized, but input and display latency remained. The fourth experiment
retained that baseline and added only Agent9's explicit `-display sdl`
selection. Stock 9front then booted quickly and was highly responsive. The
fifth experiment removes `-smp 2` while retaining SDL. See
[`03-windows-whpx-experiments.md`](03-windows-whpx-experiments.md) for the full
manual test matrix and environment details.

A future `doctor` command should report both the accelerators compiled into
QEMU and, where it can be determined safely, whether the corresponding host
facility can initialize. Compiled-in support and usable host acceleration are
different states.

Machine-readable output, for example `p9qemu doctor --json`, may be useful for
support and automation later, but is not required for the first diagnostic
implementation.

## Optional installation assistance

After diagnostic reporting is established and tested, an explicit workflow may
offer to install QEMU:

```console
$ p9qemu doctor --install
```

This feature must be opt-in. Before invoking anything, it should print the exact
native command, describe that it will modify the host, and request confirmation.
It must not silently elevate privileges or silently accept package-manager
prompts.

Potential platform integrations include:

- Ubuntu/Debian through `apt`;
- Windows through a verified `winget` QEMU package;
- macOS through a detected and supported package manager; and
- other Linux distributions only after their commands are tested and
  maintainable.

Absence of a verified integration should fall back to instructions, never a
guessed command. Users must be able to copy the displayed command and run it
themselves, preserving the project's transparency principles.

## Safety and trust boundaries

Installing a system package is materially different from downloading a cached
ISO or creating an instance disk. It may require administrative privileges,
modify shared host state, install services or drivers, trigger interactive
prompts, or conflict with the user's chosen package source.

Accordingly:

- installation automation must be a separate component from discovery and
  diagnostics;
- commands must be structured argument lists and never executed through a
  shell;
- the complete command must be printed before execution;
- elevation must remain visible and controlled by the user;
- noninteractive or force flags must not be added merely for convenience;
- errors must preserve package-manager output needed for diagnosis; and
- `--dry-run` must never invoke the package manager.

Links should point to trusted project, operating-system, or package-manager
sources. Platform recipes will age and require periodic verification.

## Architecture

The implementation should keep these responsibilities separate:

```text
host identification
        |
        v
QEMU executable and capability discovery
        |
        +--> diagnostic report
        |
        +--> platform-specific installation advice
                    |
                    v
          optional confirmed installer
```

QEMU command construction should consume resolved executable paths and
capabilities, but should not know how packages are installed. This also leaves
room for future Windows acceleration findings without coupling them to the
package-management workflow.

## Testing direction

Tests should simulate host metadata, PATH contents, executable versions,
accelerator availability, and package-manager results. They must not install
real packages or require elevated privileges.

Coverage should include:

- both required programs present;
- one or both required programs missing;
- recognized Ubuntu guidance;
- Windows and unknown-platform fallback messages;
- malformed or absent OS metadata;
- installed QEMU with an unavailable optional accelerator;
- read-only `doctor` behavior;
- dry-run behavior for proposed installation assistance;
- declined confirmation;
- package-manager failure propagation; and
- command rendering without shell execution.

Real package installation belongs only in isolated, explicit integration
environments.

## Open questions

1. Which operating systems and releases receive maintained installation
   recipes?
2. Should version 1 guidance be embedded data or generated by small platform
   strategy functions?
3. When should `doctor` become a public command?
4. Which checks are fast and reliable enough to run automatically after a
   failed launch?
5. Should installation assistance require a separate command rather than
   `doctor --install`?
6. How should elevation be represented without hiding it from the user?
7. Which Windows QEMU package and acceleration backend will be the tested
   recommendation?
8. How often and by what process will platform installation recipes be
   revalidated?
