# Automated Installation and Answer Files

## Status

Future direction beyond version 1. The initial proof of concept should target
Linux and one pinned 9front release. This design does not yet add a runtime
dependency or commit to Windows automation.

## Motivation

An interactive installation can be documented with a console transcript, but
an answer-driven installation provides a stronger record. The answer file
states the intended choices before the build begins, the automation transcript
records what actually happened, and the resulting disk digest identifies the
specific artifact that was produced.

This supports two related goals:

- automate a known 9front installation without hiding the process; and
- produce an auditable provenance bundle for future downloadable post-install
  images.

The manual graphical and serial-console installers must remain available. An
automated path is an additional workflow, not a replacement for learning or
debugging the ordinary 9front installer.

## Experimental basis

The initial manual experiment booted the pinned 9front AMD64 ISO through a QEMU
serial console, interrupted 9boot, set `console=0`, started `inst/start`, and
completed an HJFS installation. QEMU recorded the complete host-side console
session. The resulting disk subsequently booted in both serial and graphical
modes.

That experiment established that:

- the AMD64 ISO can be installed entirely through a text console;
- the prompts and responses are visible to a host-side controller;
- a QEMU character-device log can preserve the session independently of the
  controller; and
- graphical configuration can be applied temporarily at boot without changing
  the installed disk's console-oriented `plan9.ini`.

The graphical installer can also be partially recorded with Plan 9 `tee`, but
it is less suitable for exact automation. `inst/start` uses stderr as its live
log through `/srv/log`, while keyboard input is echoed by the terminal rather
than written by the installer. Capturing output and input therefore requires
separate handling, and the resulting records are not naturally interleaved.
The proven serial-console transcript remains the preferred automation
transport.

## Proposed user experience

The eventual public interface could extend the existing install command:

```console
$ p9qemu install --answers hjfs-basic.toml
```

Without `--answers`, `p9qemu install` would retain its current interactive
behavior. With an answer file, it would:

1. validate the answer schema and installer profile;
2. verify the exact installation-media digest;
3. create a new target disk without replacing an existing image;
4. print the complete QEMU command;
5. drive the installer over a dedicated serial channel;
6. preserve the raw transcript and structured decisions;
7. stop safely on an unexpected prompt, timeout, or QEMU exit; and
8. write a resolved manifest and final disk metadata after success.

The command name and option remain provisional. An internal experimental
command may be preferable until the failure behavior and answer schema have
survived real installations.

## Answer-file design

The answer file should describe semantic choices, not a positional list of
keystrokes. A raw sequence such as "press Enter five times, then type hjfs" is
fragile: an added prompt could silently apply every later answer to the wrong
question.

A preliminary TOML shape might be:

```toml
schema = 1
installer_profile = "9front-11554-amd64"
iso_sha256 = "1dcfbb3ec221307329545a37d1562beeea1f4174f6df80d245e0a222893b3bb6"

[disk]
format = "qcow2"
size = "30G"

[install]
filesystem = "hjfs"
user = "glenda"
timezone = "US_Pacific"
system_name = "cirno"

[network]
method = "dhcp"
```

This is illustrative rather than a final inventory of installer settings. The
first schema should be derived from the successful release-specific transcript
and the matching `rc/bin/inst` implementation. Defaults must be explicit in the
resolved manifest even when omitted from the user-authored file.

TOML is a good candidate because Python 3.11 can read it with the standard
library's `tomllib`, it is comfortable for people to edit, and it supports
comments. JSON remains a reasonable format for generated manifests and event
records. The user-authored and generated formats do not have to be identical.

The schema should follow these rules:

- include a schema version;
- bind an installer profile to an exact release and ISO digest;
- reject unknown keys by default so misspellings do not become ignored choices;
- distinguish an omitted value from an explicitly accepted installer default;
- use semantic names rather than prompt text or task order;
- validate mutually dependent choices before QEMU starts; and
- exclude secrets where possible and identify fields requiring redaction.

## Release-specific installer profiles

Automation must be pinned to the installer it understands. Current upstream
9front source is useful for general investigation, but it may not describe an
older release ISO exactly. Each supported installer profile should therefore
be associated with:

- architecture and 9front release identifier;
- compressed and/or unpacked ISO digest;
- expected boot and installer states;
- stable prompt patterns;
- mapping from semantic answers to installer responses; and
- stage-specific timeout policy.

The profile is a strict state machine. It should wait for an expected state,
send only the response belonging to that state, and fail closed when output is
unknown. It must never recover from an unexpected prompt by blindly sending the
next answer.

The initial profile can model the observed sequence beginning with the 9boot
prompt, setting `console=0`, booting, waiting for the Plan 9 shell, and starting
`inst/start`. The installer tasks include configuration, partitioning,
filesystem setup, networking, distribution copying, timezone selection, boot
setup, and finish. These internal task names belong in the release adapter;
they should not become the public answer-file interface.

Long operations such as copying the distribution need longer timeouts than
ordinary prompts. Timeout messages should name the current stage, show the
recent console tail, preserve all logs, and leave the failed image unpublished
for inspection.

## Provenance bundle

An automated install should produce a sidecar build directory such as:

```text
p9qemu-build/
  answers.toml
  manifest.json
  console.log
  events.jsonl
```

The disk image remains ordinary instance state outside that directory. The
manifest should record at least:

- answer schema and installer-profile versions;
- original and fully resolved answers;
- answer-file digest;
- installation-media URL, names, and digests;
- `p9qemu` version and, when available, source commit;
- QEMU and `qemu-img` versions;
- exact rendered QEMU command and structured argument list;
- host operating system, architecture, and selected acceleration profile;
- start and completion times;
- success or failure state and final installer stage;
- QCOW2 virtual size and `qemu-img info` data;
- final disk-image SHA-256 digest; and
- validation results performed after installation.

`console.log` should be the raw serial transcript. `events.jsonl` may record
structured state transitions, prompt identifiers, responses, timestamps, and
errors. Responses containing secrets must be redacted from both user-facing
output and publishable provenance. A private diagnostic log must not be placed
in release assets accidentally.

The QEMU character backend should write its own log when available. This gives
the build a transcript even if the automation process crashes and prevents the
automation library from being the sole source of evidence.

## Repeatability versus bit reproducibility

This workflow aims first for a repeatable and auditable process. Two builds
from the same answer file may not produce byte-identical QCOW2 files. Guest
timestamps, filesystem metadata, allocation order, runtime-generated identity,
and other state can change the final digest.

The final image digest identifies the exact artifact produced by one run; it
does not by itself prove that a later equivalent run will have the same digest.
Claims of bit-for-bit reproducibility should be made only after it is measured
and the remaining nondeterminism is understood.

## Linux prototype with Pexpect

Linux is the preferred first platform. Pexpect's primary `spawn` API uses a
POSIX pseudoterminal and is well suited to the successful terminal workflow.
The prototype can launch QEMU as a structured executable plus argument list,
match expected output, send responses, and maintain stage-specific timeouts.

The driver should keep prompt recognition and install policy separate:

```text
TOML answer parser
        |
        v
resolved semantic installation plan
        |
        v
release-specific installer state machine
        |
        v
Pexpect/QEMU serial transport
```

Pexpect would be a new runtime dependency, while the version 1 design currently
uses only the Python standard library. The prototype should not add it to the
main package until the feature is approved for the public CLI. Later choices
include making it a normal small dependency, exposing automation through an
optional package extra, or replacing it with a compact transport built on
standard-library sockets.

If adopted, dependency changes must be made through `uv` and recorded in both
`pyproject.toml` and `uv.lock`.

## QEMU console configuration

The automation channel should be dedicated to guest serial traffic. A
multiplexed human QEMU monitor introduces control sequences and unrelated
prompts that make matching less reliable. The automation profile should
investigate an explicit character backend, a dedicated serial device, and a
disabled or separately connected monitor.

Useful QEMU facilities include:

- character-backend `logfile` and `logappend` options;
- a non-graphical display profile for the console build;
- a dedicated serial backend rather than `mon:stdio`; and
- `-no-reboot`, which makes QEMU exit when the installer's final reboot occurs.

The exact command must be validated against the pinned ISO and printed before
execution. The known working manual command remains the starting point; the
automation design should not introduce multiple QEMU changes in one test.

## Windows options

Pexpect is not completely unavailable on Windows, but its ordinary
PTY-dependent `spawn` and `run` APIs are. It provides more limited
`PopenSpawn` and `fdspawn` interfaces that may work when the controlled program
behaves correctly over pipes.

Wexpect is another option. It exposes a Pexpect-like interface by controlling a
hidden Windows console and depends on `pywin32`. That makes it plausible for a
Windows-specific implementation, but it also adds a separate execution path,
dependency set, and collection of console behaviors to test.

Wexpect should therefore not be selected merely because its name is the
Windows counterpart to Pexpect. After the Linux state machine is proven, the
Windows investigation should compare:

1. Pexpect `PopenSpawn` with the installed Windows QEMU build;
2. Wexpect with the same dedicated console profile; and
3. a QEMU serial socket controlled directly by portable Python.

The simplest reliable option should win. Windows automation should not be
announced until a complete installation, transcript, shutdown, and resulting
image boot have been tested on Windows.

## Cross-platform serial sockets

QEMU can expose a serial character device over a TCP socket. This may provide a
cleaner long-term boundary than automating a host terminal:

```text
p9qemu installer state machine
          |
          | loopback TCP
          v
QEMU serial character device
          |
          v
9front console and installer
```

A socket transport could use the same state machine on Linux and Windows and
may avoid both PTY-specific Pexpect behavior and Wexpect. It could also keep the
main implementation within the Python standard library.

The design must bind only to loopback, avoid predictable externally exposed
ports, connect before the guest begins emitting relevant output, and close the
channel reliably on every exit path. Selecting an unused port without a race,
handling QEMU startup failures, preserving logs, and defining interrupt
behavior all require explicit tests. This is a promising convergence path, not
yet a proven replacement for the Linux Pexpect prototype.

## Safety and failure behavior

Automation increases the consequences of a mistaken disk selection. The
following constraints are mandatory:

- never overwrite, resize, reformat, or reuse an existing disk implicitly;
- attach only the newly created target and known installation media;
- show the resolved disk paths before QEMU starts;
- bind the installer profile to the verified ISO digest;
- stop on an unknown prompt, timeout, EOF, or nonzero QEMU exit;
- retain failure transcripts and manifests for diagnosis;
- mark incomplete images clearly and never publish them as successful builds;
- keep secrets out of release artifacts and ordinary terminal output; and
- require explicit user action before deleting a failed image.

An interrupted build is not resumable merely because the 9front installer can
re-evaluate completed tasks. Resume semantics should be designed separately;
the first implementation should start with a fresh target.

## Validation after installation

Automation completing the final prompt is necessary but not sufficient. A
successful build should perform proportionate validation, potentially
including:

- normal QEMU process completion through the expected reboot path;
- `qemu-img info` and `qemu-img check`;
- a fresh boot without the ISO;
- confirmation of the expected root filesystem and user;
- inspection of installed `plan9.ini` settings;
- an orderly guest shutdown; and
- optional network and service checks when those features are part of the
  image profile.

Early prototypes may keep the fresh-boot checks manual, but the manifest must
say which validations were and were not performed.

## Testing strategy

Normal unit tests must not launch QEMU or create large disks. They should use
saved, sanitized transcript fragments and fake transports to cover:

- answer parsing, defaults, and validation;
- unknown and misspelled keys;
- installer-profile and ISO-digest mismatches;
- every state transition and response mapping;
- unexpected prompts, EOF, and stage-specific timeouts;
- console normalization without hiding meaningful errors;
- secret redaction;
- manifest and event-log generation;
- preservation of existing disks; and
- cleanup and diagnostic output after partial failure.

A real Linux installation should be an explicit integration test using a
temporary directory and disposable QCOW2 target. It may require QEMU, KVM,
substantial time, and network or display resources, so it should not run as
part of the ordinary fast `uv run pytest` suite. Windows integration should be
added only after a transport is selected and tested manually.

## Proposed implementation stages

1. Sanitize the successful 11554 console transcript and use it as a golden
   development fixture.
2. Define the minimal semantic answer schema and resolved manifest.
3. Build a private Linux-only Pexpect prototype for that exact ISO digest.
4. Complete, boot, inspect, and halt a disposable automated image.
5. Add transcript-driven unit tests and an opt-in Linux integration test.
6. Decide whether the public interface should be `install --answers` and how
   Pexpect is packaged.
7. Connect the provenance bundle to the downloadable-image release process.
8. Prototype a portable QEMU serial-socket transport.
9. Evaluate Pexpect's Windows interfaces and Wexpect only if the portable
   transport is insufficient.

## Relationship to downloadable images

This design supplies the build provenance requested by
[`01-downloadable-post-install-images.md`](01-downloadable-post-install-images.md).
A future official image should be published with, or traceable to, its answer
file, resolved manifest, sanitized transcript, validation record, and exact
artifact digest.

Post-install customization still needs its own recorded steps. Automating the
base installer does not by itself document later service configuration,
identity cleanup, compaction, or release preparation.

## Open questions

1. Which installer choices belong in the first semantic answer schema?
2. Should accepted installer defaults be materialized automatically or require
   explicit values for official image builds?
3. Should Pexpect be a normal dependency, an optional extra, or only a
   prototype implementation?
4. Should `p9qemu install --answers` be public before post-install validation is
   automated?
5. What artifact-directory naming and retention policy should be used?
6. Which responses, if any, contain secrets that require private logging or
   complete omission?
7. Can QEMU's TCP serial backend replace both Pexpect PTYs and Wexpect without
   losing terminal behavior needed by the 9front installer?
8. What degree of repeatability is required before an image is labeled an
   official `p9qemu` build?
9. Which validation checks must pass before an image can be published?

## References

- [Pexpect API overview and Windows limitations](https://pexpect.readthedocs.io/en/latest/overview.html)
- [Wexpect documentation](https://wexpect.readthedocs.io/)
- [QEMU system-emulator command-line documentation](https://www.qemu.org/docs/master/system/qemu-manpage.html)
- [9front installer source at the revision inspected for this design](https://github.com/9front/9front/tree/db4a6fa3843734802a6870bbd93b1a97e2c37b2b/rc/bin/inst)
