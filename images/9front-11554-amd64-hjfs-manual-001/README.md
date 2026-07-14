# 9front 11554 AMD64 HJFS manual reference build 001

## Status

Experimental provenance reference. This is a successful manual installation,
not yet an official downloadable `p9qemu` base image. It exists to derive and
test the first release-specific answer-file profile.

The QCOW2 image and installation ISO are deliberately not committed to Git.
Their exact byte streams are identified by SHA-256 below.

## Result

The 9front 11554 AMD64 ISO was booted through a QEMU serial console on Ubuntu
under WSL2. The installer completed onto a new 30 GiB QCOW2 disk using HJFS.
The post-install snapshot subsequently passed `qemu-img check`, booted through
the serial console, and booted into the graphical environment after temporary
9boot display overrides.

| Property | Value |
| --- | --- |
| Build identifier | `9front-11554-amd64-hjfs-manual-001` |
| Architecture | AMD64 |
| Disk format and virtual size | QCOW2, 30 GiB |
| Filesystem | HJFS |
| Partition table | MBR |
| System name | `cirno` |
| User | `glenda` |
| Final timezone | `US_Pacific` |
| Network configuration | Automatic |

## Source installation media

Canonical media record:
[`9front-11554-amd64`](../../media/9front-11554-amd64/README.md).

The archive was originally downloaded from the 9front project at:

```text
https://9front.org/iso/9front-11554.amd64.iso.gz
```

The experiment used the byte-identical mirror at:

```text
https://github.com/dharmatech/9front-notes/releases/download/v0.0.1/9front-11554.amd64.iso.gz
```

| File | Size | SHA-256 |
| --- | ---: | --- |
| `9front-11554.amd64.iso.gz` | 243,859,187 bytes | `5aaf54327b4bb73a17e192488dc3e65d9d8e526728732e2fdf402bccb8c60236` |
| `9front-11554.amd64.iso` | 491,550,720 bytes | `1dcfbb3ec221307329545a37d1562beeea1f4174f6df80d245e0a222893b3bb6` |

The intended long-term home is a dedicated, non-semver `p9qemu` media release,
separate from both application releases and post-install image releases. That
release has not been created yet. Once it exists and its uploaded digest has
been verified, the application media URL and this recipe can be updated
together.

## Installation choices

[`answers.toml`](answers.toml) is a semantic reconstruction of the final choices
recorded by the manual transcript. It is a draft input for the proposed answer
schema, not yet a supported public `p9qemu` interface.

The complete prompt/response sequence is summarized in
[`transcripts/install-decisions.md`](transcripts/install-decisions.md). During
the experiment, the timezone prompt initially accepted `US_Eastern` because
the option list was awkward in the terminal. The `tzsetup` task was then rerun
and the final installed timezone was set to `US_Pacific`. The answer file
records the resolved final state; the raw transcript preserves both actions.

## Resulting image

The clean post-install snapshot was named:

```text
9front-console-target.qcow2.img-000-post-install
```

Its SHA-256 digest is:

```text
197ae5efc2dc60a9cc593fad3d407e35d7046824a8fbc201320a9caa6577029e
```

The local file occupied 559,022,080 bytes and represented a 32,212,254,720-byte
virtual disk. It is retained outside this Git repository. A future published
asset must be hashed again after any compression or release preparation; the
digest above identifies the uncompressed QCOW2 snapshot from this experiment.

## Environment

- QEMU and `qemu-img`: 6.2.0 (`1:6.2+dfsg-2ubuntu6.30`)
- Host kernel: `5.15.167.4-microsoft-standard-WSL2`
- Host architecture: `x86_64`
- Acceleration: KVM
- Related clean `p9qemu` checkout: `a3688f28144b904d337b156d42c4bf3ef885c0a8`

This build was driven with QEMU directly. The `p9qemu` commit is recorded as
development context and must not be interpreted as the program having
performed the installation.

## Evidence and validation

- `transcripts/install.raw.log` is the byte-for-byte QEMU character-backend log.
- `transcripts/first-boot.raw.log` records the successful serial boot, installed
  HJFS root, `plan9.ini`, final PDT date, and orderly `fshalt`.
- `transcripts/graphical-boot.raw.log` records the temporary 9boot display
  overrides. Successful Rio startup was observed manually because graphical
  output does not continue through the serial log.
- `validation/qemu-img-check.txt` reports no image errors.
- `validation/qemu-img-info.json` records a clean QCOW2 image with no dirty or
  corrupt flag.

The raw logs contain terminal reset, clear-screen, carriage-return, backspace,
and overstrike sequences. They are retained without modification as primary
evidence. [`transcripts/install-decisions.md`](transcripts/install-decisions.md)
is the current human-readable derivative; a deterministic terminal-stream
renderer can be added before this transcript becomes an automation fixture.

## Files

| File | Purpose |
| --- | --- |
| `answers.toml` | Draft semantic answer file reconstructed from the transcript |
| `manifest.json` | Machine-readable build and artifact metadata |
| `qemu-command.txt` | Reconstructed structured QEMU invocation used by the experiment |
| `transcripts/` | Raw evidence and a readable installer-decision summary |
| `validation/` | Original image inspection output and validation record |
| `SHA256SUMS` | Digests for the external artifacts and committed raw logs |
