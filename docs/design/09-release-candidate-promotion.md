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
    install.raw.log
    RUNNING.md
    manifest.json
    installation/
      manifest.json
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
    --install-log RUN/console.raw.log \
    --install-manifest RUN/install-manifest.json \
    --validation-manifest RUN/overlay-validation/manifest.json \
    --output-dir RUN/release-candidate-001 \
    --confirm-image-hygiene-reviewed
```

`--dry-run` performs the expensive source hashes, validation binding, evidence
verification, and text scan without copying the image or creating output.

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
overlay remained. This is still a local-only candidate: nothing was uploaded,
and Windows boot testing of the exact candidate remains a separate publication
gate.
