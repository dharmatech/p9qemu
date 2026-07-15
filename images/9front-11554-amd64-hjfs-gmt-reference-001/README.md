# 9front 11554 AMD64 HJFS GMT reference profile

This directory defines the canonical answer file for the first fresh
publishable-image build. It is separate from the historical
`9front-11554-amd64-hjfs-manual-001` evidence and does not revise that build's
recorded `US_Pacific` choice.

The profile deliberately keeps the familiar 9front defaults `cirno` and
`glenda`, installs HJFS on a fresh 30 GiB QCOW2 disk, uses automatic guest
networking, and selects `GMT` for geographically neutral, daylight-saving-free
timestamps. It does not configure passwords, authentication secrets, Drawterm,
or other additional remote services, and it has no post-install customization
stage.

Before promotion, disposable-overlay validation must confirm the expected
user, home, system name, persistent timezone, the pinned stock home-file baseline,
installed `plan9.ini`, required network response, and orderly shutdown. The
QEMU MAC address remains runtime configuration and is not stored in the image.

## Runtime boot profile

The automated installation deliberately leaves the installed `plan9.ini` in
the serial-console form used to build and validate the image: `console=0`,
`vgasize=text`, `monitor=ask`, and `mouseport=ask`. Consequently, an ordinary
`p9qemu start` reaches a usable text terminal rather than Rio. This is part of
the immutable candidate identified below, not a Windows or WHPX failure.

Rio can be tested without changing the base image by interrupting 9boot and
applying these temporary settings:

```text
clear console
mouseport=ps2
monitor=vesa
vgasize=1024x768x16
boot
```

The publication workflow must make an explicit product decision before using
this profile as a general ready-to-run image: either describe it as
console-first, or produce and separately identify a graphical-default image.
Changing `plan9.ini` would create a different image digest and therefore a new
candidate; the retained candidate must not be edited in place.

## Local candidate checkpoint

A fresh build from source commit
`a245a026b90e6ec75d3c10e0dfce6f76af196c3c` completed installation,
required-network immutable-overlay validation, local promotion, archive
round-trip verification, an independent public-text privacy scan, and a
clean-room Linux boot of the exact archive-extracted image.

The resulting local-only identity is
`p9qemu-9front-11554-amd64-hjfs-gmt-001`. Its QCOW2 SHA-256 is
`0bed74080dd8e3ece1d50731ef7766425e3b806c89e215ea8951cc006fbf25ca`.
The 250,532,383-byte tar-gzip SHA-256 is
`b9b778a2fe3ebbd8495d026d6ca4d1d4b73d7d422327dad58d3024a756b7e10d`.
These values identify a retained local candidate, not a published release
asset.

The exact archive subsequently passed native Windows 11 testing with QEMU
10.2.0 and the p9qemu WHPX profile (`kernel-irqchip=off` plus SDL). The default
boot reached the expected text terminal, where `glenda`, `cirno`,
`/usr/glenda`, GMT, HJFS, networking, and orderly shutdown were confirmed. A
second boot with the temporary graphical settings above reached Rio and was
fast and responsive. Both the base and writable test copy passed `qemu-img
check` afterward, and the read-only base retained the SHA-256 recorded above.

## Graphical runtime experiment

A later disposable Linux/KVM experiment established that `console=0` does not
need to be removed for a graphical runtime image. A fresh copy of this
candidate retained `console=0` while changing only:

```text
mouseport=ps2
monitor=vesa
vgasize=1024x768x16
```

With QEMU's GTK display backend under WSLg, the copy booted directly into a
responsive Rio while the dedicated COM1 channel independently reached
`term%`. A command sent by Pexpect through COM1 executed after graphical
initialization and appeared in a Rio terminal. The same serial transcript then
captured `glenda`, `cirno`, GMT, a successful Internet ping, and `fshalt` from
the graphical session.

The resulting graphical experiment disk was marked read-only and validated
headlessly through a disposable overlay without a temporary text-mode boot.
All ten checks passed, required networking succeeded, the base digest remained
unchanged, and the successful overlay was removed. This proves the intended
architecture for a future candidate, but it does not change the immutable
console-first candidate identified above.

Linux SDL under WSLg also reached Rio with the same guest settings, but its
relative mouse input was unusable. Repeating the run with GTK fixed the input
problem while changing no guest setting. This is a host display-backend result:
Linux should retain its proven GTK/default display path, while Windows WHPX
continues to require the separately proven SDL profile.
