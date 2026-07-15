# Image Release-Candidate Promotion

## Status

Experimental local workflow beyond version 1. The implementation creates and
verifies a release-candidate directory and archive, but it cannot upload an
asset, create a GitHub release, or make an image available to the public CLI.

## Purpose

The automated installer produces a candidate disk and private diagnostic
evidence. Disposable-overlay validation proves that the disk boots and satisfies
the expected guest checks without changing the base-image digest. Promotion is
the separate operation that turns those inputs into a portable, reviewable
release candidate.

Keeping promotion separate prevents an installation or validation run from
publishing its own output. A human can inspect the exact local bundle and its
hashes before any external state changes.

## Promotion boundary

The local tool accepts:

- an immutable image and its complete build identity;
- the exact answer file, raw installation transcript, and successful private
  installation manifest;
- a successful disposable-overlay validation manifest and its evidence; and
- explicit confirmation that the guest image contents received a hygiene
  review.

Promotion fails unless:

- the image ID and build ID are immutable, filename-safe values rather than a
  moving name such as `latest`;
- the source revision is a complete 40-character Git commit;
- the installation manifest binds that source revision to the supplied answer
  file, verified media, transcript, clean QCOW2, and resulting image digest;
- the validation manifest records the same source revision, preventing a
  candidate from mixing installer and validator implementations;
- the supplied image and answer digests match the validation manifest;
- validation status is `passed`, every required guest check passed, and no
  validation error was recorded;
- the base digest was unchanged and the successful overlay was removed;
- the image is a clean QCOW2 with a positive virtual size;
- every selected validation artifact matches its recorded size and digest; and
- copied public text passes the host-path and common-secret scan.

The required guest checks currently cover serial boot, HJFS root, user, home,
system name, persistent timezone, the release-pinned stock user-home inventory, installed
`plan9.ini`, networking, and orderly shutdown. A result with an optional
environmental failure is useful diagnostic evidence but is not promotable under
this first conservative policy.

## Public and private evidence

The original installation and validation manifests are private build evidence.
They deliberately contain absolute paths, the host kernel, and exact QEMU
commands. Those fields help reproduce a build or failure but are inappropriate
for a portable public artifact.

Promotion verifies the private installation manifest and writes an allow-listed
public installation record. It retains timestamps, p9qemu and QEMU versions,
the exact source commit, resolved answers, media/transcript/image digests,
path-free QCOW2 metadata, host class, acceleration, and memory. It omits host
paths, the kernel, and the path-bearing QEMU command while retaining the digest
of the private source manifest.

Promotion writes an allow-listed public validation manifest. It retains:

- timestamps, the exact source commit, p9qemu and QEMU versions, and a limited
  host description;
- resolved answers and their digest;
- path-free QCOW2 metadata and the immutable image digest;
- acceleration, memory, guest checks, and network-check mode; and
- the SHA-256 digest of the original private validation manifest.

It omits the original image and overlay paths, QEMU arguments, host kernel, and
path-bearing `qemu-img` filename. The bundle includes verified copies of the raw
install transcript, validation boot transcript, and before/after `qemu-img
check` results. The private event log is not copied because its diagnostic
messages contain build-host paths; the corresponding path-free check results
remain in the public validation manifest.

The text scanner is intentionally a guardrail rather than a complete secret
detector. It rejects common user-home paths, private-key markers, common token
prefixes, and inline password/token/secret assignments. It cannot inspect the
guest filesystem inside the QCOW2. The required hygiene confirmation records
that the image itself was separately reviewed for credentials, personal state,
temporary files, and machine-specific identity.

## Local output

For identity `9front-11554-amd64-hjfs` and build `001`, the new output directory
has this shape:

```text
release-candidate-001/
  p9qemu-9front-11554-amd64-hjfs-001/
    p9qemu-9front-11554-amd64-hjfs-001.qcow2
    answers.toml
    runtime.toml
    install.raw.log
    RUNNING.md
    manifest.json
    installation/
      manifest.json
    preparation/
      manifest.json
      preparation.raw.log
      plan9.ini.before.txt
      plan9.ini.after.txt
      qemu-img-check-input.txt
      qemu-img-check-output.txt
    validation/
      manifest.json
      boot.raw.log
      qemu-img-check-before.txt
      qemu-img-check-after.txt
  p9qemu-9front-11554-amd64-hjfs-001.tar.gz
  p9qemu-9front-11554-amd64-hjfs-001.tar.gz.sha256
  verification.json
```

`RUNNING.md` treats the bundled QCOW2 as immutable. Until the future cached-base
and overlay workflow exists, it instructs manual users to make a writable copy
before using `p9qemu start` or the documented direct QEMU commands. It contains
the tested Linux KVM, Windows WHPX plus SDL, and Windows TCG profiles.

The release manifest uses only bundle-relative paths. It records the source
commit and media digest, image identity and hash, runtime profile, hygiene state,
artifact inventory, and an explicit `local-only`/`uploaded: false` publication
state.

## Atomicity and archive verification

The tool never writes directly to the requested final directory. It constructs
the candidate in a uniquely named sibling temporary directory and removes that
directory after an error or interruption. The final directory must not already
exist and is published with one rename only after all checks pass.

The tar-gzip writer uses sorted entries and normalized timestamps, ownership,
and permissions. This makes archiving deterministic for identical bundle bytes.
It does not transform or compact the QCOW2.

Before publishing the local directory, the tool safely extracts the archive to
a temporary location. It rejects absolute paths, `..`, duplicate entries, links,
special files, unexpected archive roots, and files appearing before their parent
directory. It then verifies the manifest-bound size and digest of every file,
rejects unlisted files, and confirms that the extracted QCOW2 digest equals the
original immutable image digest. The temporary extraction is removed after
success.

## Experimental command

The internal Linux command is:

```console
$ uv run python tools/build_release_candidate.py \
    --image-id 9front-11554-amd64-hjfs \
    --build-id 001 \
    --source-commit COMMIT \
    --disk RUN/target.qcow2 \
    --answers RUN/answers.toml \
    --runtime-profile RUN/runtime.toml \
    --install-log RUN/console.raw.log \
    --install-manifest RUN/install-manifest.json \
    --preparation-manifest RUN/preparation/manifest.json \
    --validation-manifest RUN/overlay-validation/manifest.json \
    --output-dir RUN/release-candidate-001 \
    --confirm-image-hygiene-reviewed
```

`--dry-run` performs the expensive source hashes, validation binding, evidence
verification, and text scan without copying the image or creating output.

After a candidate has been built and reviewed, the separate local manifest
generator streams the exact archive and derives its external `image.json`
without extracting the QCOW2. That consumer-facing metadata step is documented
in [`10-ready-image-manifest-and-cache.md`](10-ready-image-manifest-and-cache.md).
It remains non-publishing and cannot weaken or replace this promotion boundary.

## Publication remains separate

A locally successful candidate is not automatically a release. The publication
step must later:

1. review the bundle contents and provenance limitation statements;
2. verify licensing and attribution;
3. upload the exact archive without replacing an existing tag or asset;
4. download the asset into a clean cache and verify its archive digest;
5. extract and verify the image digest;
6. boot that exact download on the supported Linux and Windows profiles; and
7. add catalog metadata only after those checks pass.

A rebuild receives a new build ID even when all inputs appear identical. Moving
channels such as `latest` may resolve to an immutable identity later, but are
never themselves build identities.

## First reference candidate

The retained 11554 automated HJFS image is appropriate for exercising the local
workflow, but it remains a development artifact. The first publishable image
should be produced by a fresh build whose source commit, storage preflight,
installation, validation, hygiene review, promotion, and clean-room host tests
are captured as one deliberate release run.

### Retained-image exercise (2026-07-14)

The first complete local exercise used the retained automated 11554 HJFS image.
The dry run initially failed the privacy scan because the private event log
contained `/home/<user>` build paths. That result established that copying
the event log conflicted with the public-evidence policy. Promotion was changed
to omit it while preserving its source-manifest digest and the path-free check
results.

The second dry run passed without creating output. The complete promotion then
finished in approximately 79 seconds and established:

- candidate identity `p9qemu-9front-11554-amd64-hjfs-001`;
- image size 559,022,080 bytes and SHA-256
  `a2e42e099d65b563c41d54deecfc58354708f712b5fca171429b1a8c419feaac`;
- tar-gzip size 250,527,194 bytes and SHA-256
  `afe404dde7effe1d6238c421bf46f78b9b645048a5214c3d13ae69bece550400`;
- nine round-trip-verified files, counting the release manifest;
- matching independent hashes for the original and bundled QCOW2;
- a successful independent `sha256sum -c` archive check;
- no retained temporary construction or extraction directory; and
- no GitHub upload or other publication action.

The output occupies approximately 773 MiB and remains in the retained run's
`release-candidate-001` directory for review.
The Windows free-space postflight remained above 173 GB and the WSL VHDX file
length remained 219,465,908,224 bytes. This exercise proves the local mechanism;
the explicit development-artifact limitation above still applies.

That retained exercise predates the required installation-manifest input. It
remains valid historical evidence for the mechanism tested at the time, but it
cannot be rebuilt by the stricter current promotion command without a genuine
source-bound installation manifest.

## First fresh reference profile

The first deliberate fresh build uses installer profile
`9front-11554-amd64-hjfs-gmt-v1`. It preserves the conventional 9front system
name `cirno` and user `glenda`, selects HJFS on a fresh 30 GiB QCOW2 target,
uses automatic networking, and selects the exact 9front timezone name `GMT`.
GMT is geographically neutral and has no daylight-saving transitions; users
can change `/adm/timezone/local` after installation.

The profile performs no post-install customization. It does not configure a
password, authentication secret, Drawterm, or any additional remote service.
The QEMU MAC address is runtime configuration rather than guest-image identity.
Validation proves that `/adm/timezone/local` matches `/adm/timezone/GMT` and
that `/usr/glenda` contains only the pinned 11554 stock files plus the known
boot-time temporary patterns. The explicit promotion hygiene confirmation
remains necessary because those bounded checks cannot prove the absence of
every possible secret elsewhere in the guest filesystem.

### Fresh GMT candidate exercise (2026-07-14)

The first deliberate fresh run exposed two useful validation mistakes before
promotion. Plan 9's serial terminal echoed the `cmp` command, so a helper that
expected literally empty output rejected an otherwise successful GMT
comparison. The next live check showed that a stock install correctly populates
`/usr/glenda`; an empty-home policy would have rejected distribution files as
personal state. PR #13 changed the validator to remove only an exact echoed
command line and replaced the empty-home assumption with a release-pinned stock
inventory plus known boot-time temporary patterns.

The non-canonical diagnostic QCOW2 and its failed overlays were deleted after
the evidence was retained. The authoritative build then started again from a
fresh target and used merged source commit
`a245a026b90e6ec75d3c10e0dfce6f76af196c3c` for both installation and
validation.

The authoritative run established:

- answer-file SHA-256
  `c0a2ab375a50a22cebda4d45dbb6481630d236d6aaff6c58cb77481fd81c294e`;
- verified installation-media SHA-256
  `1dcfbb3ec221307329545a37d1562beeea1f4174f6df80d245e0a222893b3bb6`;
- raw installation-transcript SHA-256
  `72dffd5c2a57deb0c86ea56a8222673311e08b010615fc6a1012559c38a267c2`;
- private installation-manifest SHA-256
  `f4daaa9946b9a20a5f13740486b9176a9455e13e2c54924df2de57f1f892462d`;
- QCOW2 size 559,022,080 bytes, virtual size 32,212,254,720 bytes, and
  SHA-256
  `0bed74080dd8e3ece1d50731ef7766425e3b806c89e215ea8951cc006fbf25ca`;
- private validation-manifest SHA-256
  `74d218d404114a14916ba432675a355ad8bd5c6d648367a5008340158e547eed`;
- ten passing guest checks: serial boot, HJFS root, user, home, system name,
  GMT, stock-home inventory, `plan9.ini`, required networking, and orderly
  shutdown; and
- unchanged base-image hashes and removal of every successful overlay.

Local promotion produced candidate
`p9qemu-9front-11554-amd64-hjfs-gmt-001`. The bundle manifest has SHA-256
`5d0d4ae8f5fcb10834979e78977633b456be1136109e9dc9cad0f5cb8271cecb`.
The deterministic tar-gzip is 250,532,383 bytes with SHA-256
`b9b778a2fe3ebbd8495d026d6ca4d1d4b73d7d422327dad58d3024a756b7e10d`.
Its checksum, ten-file archive inventory, manifest-bound artifact digests, and
independent public-text privacy scan all passed.

For a clean-room Linux check, the exact archive was independently extracted,
its answer, manifest, and QCOW2 hashes were rechecked, and the extracted image
passed `qemu-img check` plus the same required-network immutable-overlay boot.
The disposable extracted QCOW2 was then deleted while the validation evidence
was retained.

The Windows free-space postflight was 173,548,670,976 bytes. The Ubuntu WSL
VHDX remained 219,465,908,224 bytes, WSL reported 886,240,436,224 bytes
available, the authoritative run occupied approximately 1.3 GiB, and the
retained diagnostic evidence occupied 164 KiB. No `.part` file or validation
overlay remained. This is still a local-only candidate: nothing was uploaded.

#### Exact-candidate Windows WHPX gate (2026-07-14)

The exact 250,532,383-byte archive was copied from the authoritative WSL run to
a dedicated native-Windows test directory. Its SHA-256 matched
`b9b778a2fe3ebbd8495d026d6ca4d1d4b73d7d422327dad58d3024a756b7e10d`
before extraction. The extracted manifest and QCOW2 matched their recorded
digests, and `qemu-img check` found no errors. The base was then marked
read-only and a separate writable copy was used for both boots.

The dry run from the current project checkout selected the exact working copy
and rendered `-accel whpx,kernel-irqchip=off -display sdl`, with no silent TCG
fallback. Native Windows 11 and QEMU 10.2.0 then produced these results:

| Check | Result |
| --- | --- |
| Default boot | Passed as the profile's expected text-console boot |
| User, system, and home | `glenda`, `cirno`, and `/usr/glenda` |
| Persistent timezone | `/adm/timezone/local` matched `/adm/timezone/GMT`; `date` reported GMT |
| Networking | `ip/ping -n 1 google.com` received a reply |
| Installed boot settings | `mouseport=ask`, `monitor=ask`, `vgasize=text`, and `console=0` |
| Temporary graphical boot | `clear console`, `mouseport=ps2`, `monitor=vesa`, and `vgasize=1024x768x16` reached Rio |
| WHPX graphical behavior | Boot and Rio were fast and responsive with SDL |
| Shutdown | `fshalt` completed and QEMU exited; forwarding ports were released |
| Postflight integrity | Both QCOW2 files passed `qemu-img check`; the read-only base digest was unchanged |

The first boot also exposed a release-contract gap rather than an acceleration
failure. The answer-driven installer intentionally persists a console-oriented
`plan9.ini`, so a plain `p9qemu start` does not open Rio by default. Temporary
9boot overrides prove that the same guest and WHPX profile support Rio, but
they are not the intended one-command onboarding experience. Promotion of a
general ready-to-run graphical image should therefore require one of two
explicit outcomes:

1. label and document this immutable candidate as console-first; or
2. create a new, separately identified candidate whose recorded release
   preparation sets graphical boot defaults, then repeat Linux and Windows
   clean-room tests against its exact archive.

The existing candidate must not be modified in place because its image and
archive digests are already part of the provenance record. Windows free space
after the test was 172,179,492,864 bytes, and the Ubuntu WSL VHDX remained
219,465,908,224 bytes.

#### Graphical runtime with retained serial console (2026-07-15)

The next experiment tested whether a future graphical-default image could keep
the serial channel required by automation. It did not mutate candidate `001`.
Two fresh disposable copies started with the exact candidate image SHA-256
`0bed74080dd8e3ece1d50731ef7766425e3b806c89e215ea8951cc006fbf25ca`.
A recorded serial preparation changed only these persistent `plan9.ini`
values:

```text
mouseport=ask              -> mouseport=ps2
monitor=ask                -> monitor=vesa
vgasize=text               -> vgasize=1024x768x16
console=0                     console=0
```

The preparation driver verified the original, staged, and installed files from
inside 9front, completed `fshalt`, and preserved its QEMU command, raw serial
log, before/after files, hashes, and `qemu-img` evidence. The different output
hashes from the two equivalent preparations reinforced that this process is
auditable rather than bit-reproducible.

The controlled display matrix produced:

| Host profile | Guest result | Serial result | Input result | Outcome |
| --- | --- | --- | --- | --- |
| Linux KVM plus SDL under WSLg | Rio started | HJFS and `init` were recorded; a later `term%` appeared | Button events arrived, but relative pointer motion and selection were unusable | Failed host display profile; QEMU was closed without guest shutdown and the disk still passed `qemu-img check` |
| Linux KVM plus GTK under WSLg | Rio started directly | Post-graphics `term%` appeared and a Pexpect marker command executed | Mouse, menus, terminals, and windows behaved normally | Passed |

The successful GTK serial transcript captured both automation and graphical
session activity. It recorded the Pexpect marker, active user `glenda`, system
name `cirno`, GMT, a successful `ip/ping -n 1 google.com`, and `done halting`.
The screenshot of the marker in a Rio terminal confirmed that the serial input
path remained usable after graphical initialization rather than merely
receiving boot output.

The successful graphical experiment disk was then marked read-only at SHA-256
`17b67ed3edfb90f881a33f79a4a52010da3d8a3496c0566d96d2901f9c247e4d`.
The ordinary headless KVM validator attached only a disposable overlay and
expected the graphical runtime `plan9.ini`. Without any temporary
`vgasize=text` override, it passed all ten checks: serial boot, HJFS root,
user, home, system name, GMT, stock-home inventory, graphical boot settings,
required networking, and orderly shutdown. The read-only base hash was
unchanged and the successful overlay was removed.

Three development-only drivers preserve the exact procedure:

- `tools/prepare_graphical_experiment.py` performs the explicitly confirmed,
  hash-gated guest-side preparation and records the before/after evidence;
- `tools/run_graphics_serial_experiment.py` runs the selected host display with
  a dedicated logged serial channel and probes the post-graphics shell; and
- `tools/validate_graphical_experiment.py` applies the normal guest-validation
  state machine to a read-only graphical base through a disposable overlay.

They are evidence-producing development interfaces, not public CLI commands or
the final candidate `002` build design. The preparation and run drivers refuse
non-experiment disk names and require exact input hashes. The validator refuses
a writable base and removes only a successful overlay.

This establishes a simpler candidate `002` architecture:

```text
same immutable graphical image
├── user boot: host graphical display + console=0 -> Rio and COM1 term%
└── validator boot: -nographic + console=0 -> the same COM1 term%
```

No final switch from serial to graphics and no validation-only text override
is required. The serial console and VGA framebuffer are independent; terminal
startup calls `screenrc`, and the earlier Rio failure resulted from
`vgasize=text`, not from `console=0`.

The local experiment directory occupied approximately 1.1 GiB before optional
failed-disk cleanup. Windows free space was 172,645,380,096 bytes, the Ubuntu
WSL VHDX remained 219,465,908,224 bytes, and WSL reported 884,565,340,160 bytes
available. No QEMU process, forwarded-port listener, `.part` file, or validation
overlay remained after postflight. The failed SDL QCOW2 was then removed after
its command, logs, hashes, manifest, and structural checks had been preserved;
the remaining successful disk and all evidence occupied approximately 529 MiB.

#### First-class candidate 002 preparation boundary (2026-07-15)

The proven experiment is now represented by a strict runtime profile rather
than by special validation expectations. The answer file continues to describe
the console-driven installer. `runtime.toml` separately binds that installer
profile to these exact source and target states:

```text
source: mouseport=ask, monitor=ask, vgasize=text, console=0
target: mouseport=ps2, monitor=vesa, vgasize=1024x768x16, console=0
```

`tools/prepare_release_image.py` preserves the installed image, creates a new
copy, verifies that every selected source setting occurs exactly once, applies
only the changed values, verifies the staged and installed target state, and
halts cleanly. Its private preparation manifest binds the answer file, runtime
profile, source commit, input image, output image, QEMU environment, serial
transcript, before/after files, and structural checks.

The ordinary `tools/validate_image.py` accepts `--runtime-profile`; it no longer
needs an experiment-only replacement of expected `plan9.ini` values. Candidate
promotion requires all three private stages and enforces this digest chain:

```text
installation manifest image SHA-256
  == preparation input SHA-256
preparation output SHA-256
  == validation base SHA-256
  == promoted candidate image SHA-256
```

The public bundle includes a sanitized preparation manifest and selected
path-free evidence. Candidate `001` remains unchanged. The successful
experimental disk remains proof only and is not eligible to be relabeled as
candidate `002`.

#### Fresh graphical candidate 002 exercise (2026-07-15)

Source commit `778563fe26358c1acc76aedd72966cbca7807f78` produced a fresh
automated GMT/HJFS installation. The installation completed every qualified
11554 state through `finish.rebooting` and recorded:

- installed image SHA-256
  `60aa524ba33e903086eb708b96f843ba21270664476e9b1484d5c23c877d7103`;
- answer SHA-256
  `c0a2ab375a50a22cebda4d45dbb6481630d236d6aaff6c58cb77481fd81c294e`;
- runtime-profile SHA-256
  `44a7413cffb9374c9114d8543b107a7d27a0d53dd2dd814db2a28022f3011adb`;
  and
- installation transcript SHA-256
  `e3a526a5833d26e2f894c4d506f0261437735c700271ae9ae8da1011289f8df6`.

Release preparation copied rather than mutated the installed artifact,
verified every source and target setting exactly, retained `console=0`, halted
cleanly, and produced image SHA-256
`1ef80c81a3f2dd09d2f173ff7dfa93d07ecee2ba453fc0f0964190adb6ee44a8`.
The installed source digest remained unchanged. Standard validation consumed
the checked-in runtime profile, passed all ten required checks including
networking and orderly shutdown, removed its successful overlay, and left the
prepared base unchanged.

Promotion dry-run verified the complete digest chain, eight selected public
evidence files, and the public-text privacy scan. Local promotion then created
and round-trip verified
`p9qemu-9front-11554-amd64-hjfs-gmt-002.tar.gz` at SHA-256
`ddf9086ab7925e891ea6d577474f70a6eccd91dccc85d5fc29b0d3acf29b6c4d`.
Nothing was uploaded.

An independent clean-room extraction verified the archive checksum, exact
image checksum, and QCOW2 structure, then repeated the required-network
immutable-overlay validation successfully. A human Linux/KVM boot through
ordinary `p9qemu start` automatically passed through 9boot when left
untouched, then presented the stock `bootargs` and `user[glenda]:` prompts.
Accepting both defaults reached responsive Rio with working mouse input.
`fshalt` closed QEMU, all forwarding listeners were released, the disposable
graphical overlay passed `qemu-img check`, and the exact extracted base retained
its recorded digest.

The first graphical launch attempt used an incorrectly detached WSL shell and
QEMU received host-side SIGHUP when that shell exited. Its overlay remained
clean. Keeping the Windows `wsl.exe` host process alive for the QEMU lifetime
fixed the test harness; this was not a guest or candidate failure.

Rio displayed stats plus two terminal windows. Stock 9front's `riostart`
launches one ordinary terminal and conditionally launches `window -scroll
console` when `console=0` is configured. The second terminal is therefore the
intentional visible COM1 channel rather than guest customization or leaked
validation input.

#### Exact graphical candidate 002 Windows gate (2026-07-15)

The exact candidate archive was copied from the authoritative WSL run into a
dedicated native-Windows test directory. The 250,529,927-byte copy retained
SHA-256
`ddf9086ab7925e891ea6d577474f70a6eccd91dccc85d5fc29b0d3acf29b6c4d`.
Every extracted public artifact matched its manifest size and digest, the
QCOW2 passed `qemu-img check`, and its SHA-256 was
`1ef80c81a3f2dd09d2f173ff7dfa93d07ecee2ba453fc0f0964190adb6ee44a8`.
The extracted base was marked read-only. Separate QCOW2 overlays isolated the
WHPX and TCG tests.

The host ran Windows 11 25H2 build 26200.8655 and QEMU 10.2.0. Tests used the
current p9qemu checkout at commit
`3623b9da788f2ecbbc0478d3766c7fc01c24421c`; all 134 repository tests passed
before the gate. Explicit dry runs proved that neither test could silently use
the other accelerator.

| Host profile | Boot and Rio | Input and network | Shutdown | Outcome |
| --- | --- | --- | --- | --- |
| `-accel whpx,kernel-irqchip=off -display sdl` | Automatic 9boot, the expected `bootargs` and `user[glenda]:` prompts, then responsive Rio | Mouse, menus, terminal input, and `ip/ping -n 1 google.com` passed | `fshalt` closed QEMU | Passed; QEMU reported that WHPX was operational |
| `-accel tcg` with QEMU's default display | The window appeared after a noticeable startup delay; automatic 9boot, both prompts, and responsive Rio then passed | Mouse, menus, terminal input, and the same ping passed | `fshalt` closed QEMU | Passed as the portable software-emulation fallback |

Both live runs exposed exactly the seven configured forwarding listeners from
their QEMU process. After each shutdown, QEMU and all listeners were gone.
Both overlays and the base passed `qemu-img check`; the read-only base and the
Windows archive copy retained their exact digests. Rio showed the expected
stats window and two terminals in both profiles. Nothing was uploaded.

The native gate occupied 811,049,742 bytes before cleanup. After preserving
9,983 bytes of commands, environment metadata, live-state records, structural
checks, and results, the copied archive, extracted base, and both overlays were
removed. Windows free space was 172,549,128,192 bytes. The Ubuntu WSL VHDX
remained 219,465,908,224 bytes and WSL reported 883,190,566,912 bytes
available; the authoritative WSL candidate was retained.
