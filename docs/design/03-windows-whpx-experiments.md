# Windows QEMU Acceleration Experiments

## Purpose and scope

This document records the manual QEMU acceleration experiments performed during
version 1 development. It preserves both successful and unsuccessful profiles
so future changes have an evidence trail rather than an unexplained collection
of QEMU flags.

These results describe one development system. They are evidence for p9qemu's
defaults and further testing, not a guarantee that every Windows host, QEMU
version, or 9front build will behave identically.

## Development environment

| Component | Tested configuration |
|---|---|
| Host | Native Windows 11 |
| QEMU | QEMU 10.2.0 for Windows |
| Stock guest | `9front-11554.amd64.iso`, boot filesystem dated January 24, 2026 |
| Installed disk | 30 GiB QCOW2 using HJFS |
| Runtime memory | 2048 MiB |
| Storage | VirtIO SCSI controller and SCSI hard disk |
| Network | VirtIO NIC with QEMU user networking and localhost-only forwards |
| Display configuration | 9front VESA, 1024x768x16 |
| Test date | July 13, 2026 |

Unless a row says otherwise, WHPX tests used the same installed stock-9front
disk and the same p9qemu storage, networking, memory, and port-forwarding
settings. Explicit WHPX profiles intentionally omitted a TCG fallback, so a
guest that started could not silently be running under software emulation.

## Baseline validation

| Host and accelerator | Installation | Runtime | Networking | Result |
|---|---|---|---|---|
| Ubuntu under WSL, KVM | Completed normally | Fast and responsive | `ip/ping google.com` succeeded | Known-good Linux baseline |
| Native Windows 11, TCG | Completed; file-copy phase was noticeably slower than KVM | Responsive after installation | `ip/ping google.com` succeeded | Known-good Windows baseline and current `auto` behavior |

The Windows TCG baseline used QEMU's default display frontend and did not need
SDL. The display problem described below appeared only after enabling WHPX.

## Controlled stock-9front WHPX matrix

Each experiment changed one setting from the preceding stock-9front row. Status
describes practical usability, not merely whether QEMU accepted the command.

| Experiment | Relevant QEMU arguments | Observation | Status | Conclusion |
|---:|---|---|---|---|
| 1 | `-accel whpx` | Boot stopped for several minutes at `cpu0: lapic clock at 200MHz`. | Failed | Bare WHPX was not compatible with this stock-9front guest. |
| 2 | `-accel whpx,kernel-irqchip=off` | Passed LAPIC setup and eventually reached Rio, but boot, display updates, and mouse input were extremely slow. | Booted but unusable | Userspace irqchip avoided the initial stop but did not provide a usable default-display VM. |
| 3 | `-smp 2 -accel whpx,kernel-irqchip=off` | `cpu1` initialized, but boot, display, and mouse latency remained. | Booted but unusable | A second virtual CPU was not sufficient. |
| 4 | `-smp 2 -accel whpx,kernel-irqchip=off -display sdl` | Booted quickly; Rio, display updates, mouse input, and the desktop were highly responsive. | Passed runtime/UI test | SDL was the decisive change on this development host. |
| 5 | `-accel whpx,kernel-irqchip=off -display sdl` | Pending manual test. | Pending | Determines whether SDL is sufficient without a second virtual CPU. |

The repeated `mpintrassign: can't find bus type 12, number 0` message and a
roughly 999–1000 MHz LAPIC clock appeared in both slow stock-9front and fast
Agent9 runs. They did not correlate with the performance failure.

## Agent9 comparison

Agent9 v0.5.0 was used as an investigative reference, not as a controlled
stock-9front result. Its `run-windows.bat` configured:

```text
-accel whpx,kernel-irqchip=off -accel tcg
-smp 2
-drive if=virtio
-device virtio-rng-pci
-usb -device usb-tablet
-display sdl
```

QEMU printed `Windows Hypervisor Platform accelerator is operational`, proving
that the responsive Agent9 run used WHPX rather than its configured TCG
fallback. Agent9 booted quickly and its desktop was responsive. Its guest image,
kernel date, storage interface, network syntax, random device, USB input, and
display setup all differed from the stock-9front test, so it identified useful
candidates but did not by itself establish which flag mattered.

The controlled stock-9front sequence subsequently showed that two virtual CPUs
alone did not help, while adding SDL produced the first fast, responsive WHPX
run. VirtIO-block storage, a USB tablet, and Agent9-specific guest changes were
therefore not required to reach that result.

## Supporting automated validation

Before manual VM testing, the original 37-test suite passed on both Ubuntu and
native Windows. As WHPX capability checks and profiles were added, the suite
grew to 44 tests; the complete suite, Ruff lint, and Ruff formatting checks
passed before each experimental profile was committed.

Dry runs on Linux and Windows also verified the resolved executable paths,
acceleration labels, disk paths, QEMU arguments, and no-side-effect behavior
before each real launch. Automated checks validate command construction and
safety boundaries; they do not replace the manual boot, networking, and
responsiveness observations recorded above.

## Current conclusions

1. Windows TCG works with QEMU's default display frontend and remains the safe
   `auto` mode during version 1 development.
2. On this host, stock 9front requires `kernel-irqchip=off` to progress reliably
   under WHPX.
3. On this host, the default QEMU display frontend becomes extremely slow with
   the tested WHPX profile; explicit SDL makes the guest fast and responsive.
4. Two virtual CPUs are not sufficient to fix the problem. Experiment 5 will
   determine whether they are unnecessary once SDL is enabled.
5. WHPX remains explicit and has no silent fallback while validation continues.

Before considering WHPX for Windows `auto`, the minimal successful profile
should pass repeated startup, guest networking, clean shutdown, and a complete
installation on the development host. Testing on additional Windows machines
and QEMU versions is also desirable.
