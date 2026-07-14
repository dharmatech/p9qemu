# New 9front Release Qualification

## Status

Proposed operational gate for every new 9front installation-media release.
The 11554 amd64 media and its observed installer behavior form the initial
baseline. This process becomes active when another release is considered for
the p9qemu media catalog or automated-image builds.

## Decision summary

Automated installation support is always qualified for an exact combination
of:

- immutable installation-media identity and digests;
- installer-profile revision;
- answer-schema revision and supported capabilities; and
- validated host and QEMU profiles.

p9qemu must not assume that automation written for one release supports later
9front installers merely because their prompts appear similar. An unfamiliar
media digest is rejected until the project explicitly qualifies it. Installer
profile inheritance is never implicit or wildcard-based.

Making media available, declaring an automated build path supported, and
promoting a release as the default are three separate decisions. This permits
a new release to remain opt-in while its installer or resulting image receives
additional testing.

## Motivation

9front installer behavior is part of the input to every automated image build.
A later release might preserve the existing sequence, change prompt text or
defaults, reorder tasks, or add a choice such as GEFS. Even an additive change
deserves review: the existing HJFS answer path may remain compatible without
implying that p9qemu supports the new filesystem choice.

This qualification process provides a repeatable way to answer:

- which exact media was inspected;
- what changed in the installer;
- whether an existing answer set still has the same meaning;
- whether a profile can be certified for the new digest or must be revised;
- which manual and automated validations completed; and
- why a release is available, supported for automation, or recommended.

Current upstream source is valuable context, but it is not authoritative for
an older release image. Comparisons must use the installer implementation from
the exact media or its corresponding release source whenever that relationship
can be established.

## Scope of a support claim

Qualification may cover a deliberate subset of installer capabilities. For
example, a release whose installer offers HJFS, CWFS, and GEFS can be qualified
for the HJFS answer path while GEFS remains unsupported. The qualification
record must say so explicitly.

The useful unit of support is therefore not simply "release 11554" or "the
9front installer." It is a tuple such as:

```text
(media digest, installer profile, answer schema, capability set, host profile)
```

This distinction prevents a successful baseline install from becoming an
accidental claim that every new option, architecture, or host combination has
been tested.

## Qualification artifacts

The media provenance directory should contain a concise qualification summary
for each qualified release. Large or run-specific build artifacts should stay
outside Git and be represented by checksums and links.

One possible layout is:

```text
media/9front-<release>-amd64/
  README.md
  media-manifest.json
  qualification/
    qualification.json
    installer-diff.md
    baseline-answers.toml
    manual-console.sha256
    automated-console.sha256
    validation.json
```

Small, sanitized console logs may live in the related image provenance
directory so the build remains inspectable. Disposable disk images, caches,
and other large run artifacts do not belong in ordinary Git history; record
their checksums and publish intentional release assets separately. Reference
image bundles and their logs are described in
[`04-automated-installation-answer-files.md`](04-automated-installation-answer-files.md).

The machine-readable qualification record should include at least:

- media identifier, architecture, URLs, filenames, sizes, and digests;
- installer-profile and answer-schema revisions;
- supported answer capabilities and explicitly unsupported choices;
- exact source revision or installer-file hashes when available;
- predecessor profile and comparison result;
- manual and automated run identifiers and log digests;
- validation results, host details, QEMU version, and acceleration profile;
- qualification state, review date, and relevant p9qemu commit; and
- known limitations or follow-up work.

## Qualification states

A release moves through explicit states:

1. **Candidate:** immutable media and provenance are recorded.
2. **Manually observed:** a console installation and its transcript have been
   reviewed.
3. **Automation classified:** the existing profile is compatible, a new
   profile is required, or the release is not yet supported.
4. **Automation validated:** a disposable automated build and post-install
   checks have succeeded for the claimed capability and host set.
5. **Available:** users may select the release explicitly.
6. **Recommended:** the source-controlled default points to the release.

A release may stop at any state. In particular, availability does not imply
automated-install support, and automation validation does not automatically
make a release recommended.

## Disk-space preflight

Every substantial WSL/QEMU qualification run must begin with a storage
preflight. WSL's reported free space alone is insufficient because its virtual
disk can continue growing on the Windows host.

Record:

- free space on the Windows volume that contains the WSL virtual disk;
- the actual physical size of the Ubuntu `ext4.vhdx` file;
- WSL filesystem free space as secondary diagnostic information;
- allocated, not merely virtual, size of existing QCOW2 candidates; and
- allocated size of the proposed run directory and retained artifacts.

The operator should estimate peak temporary use for the cached archive,
unpacked ISO, target QCOW2 allocation, logs, and any post-install copy. The run
must use a unique disposable directory and refuse to reuse an existing target
disk implicitly.

Shared, checksum-verified installation-media caches should normally be kept.
Failed and superseded test images should be removed once they no longer have
diagnostic value. A successful candidate may be retained for review. Deleting
files inside WSL does not necessarily reduce the physical VHDX size;
compaction is a separate, explicit host-maintenance operation and must never be
performed automatically by p9qemu.

## Qualification procedure

### 1. Acquire and identify immutable media

Obtain the archive from the documented upstream location. Record compressed
and decompressed sizes and SHA-256 digests. If p9qemu mirrors it, publish the
same bytes under a new immutable media tag; changed bytes require a new tag or
identifier.

The candidate must not enter automation through a mutable URL, a guessed
release label, or a checksum inherited from a prior release.

### 2. Identify the exact installer implementation

Associate the media with its release source when possible. Record the release
commit or tag and hashes of the installer task scripts that affect the build.
If the mapping cannot be proven, extract and inspect the installer files from
the media or installed environment and record that limitation.

The current 9front main branch may help explain implementation concepts, but
it must not substitute for the release-specific evidence.

### 3. Compare against the previous profile

Perform a static and semantic comparison of:

- task names, order, and completion behavior;
- prompt identifiers and accepted input;
- choices, defaults, validation rules, and newly added options;
- partitioning, filesystem, network, timezone, boot, and finish steps;
- console setup and logging behavior; and
- reboot, shutdown, or installer-exit behavior.

Summarize the material differences in `installer-diff.md`. Pure wording changes
still require the state machine's prompt matching to be checked.

### 4. Run a manual console installation

Until experience justifies a narrower policy, perform one manual serial-console
installation for every new media release. Use an answer set semantically
equivalent to an already qualified baseline when possible. Preserve the raw
console transcript, its digest, and a human-readable decision summary.

The manual run establishes the actual prompt sequence and catches differences
that static source comparison may miss. It is also the evidence used to update
saved transcript fixtures before changing automation.

### 5. Classify compatibility

Classify the result into one of three categories:

- **Compatible:** the prior semantic answers and state transitions retain the
  same meaning. The existing profile implementation may be explicitly
  certified for the new media digest.
- **Capability addition:** the baseline path still works, but the installer
  offers a new choice. Qualify only the tested subset or deliberately extend
  the answer schema and profile.
- **Breaking change:** prompts, states, meanings, or safety assumptions changed.
  Create a new installer-profile revision or release adapter.

Even the compatible classification requires a new certification record. It
must never result from automatically accepting a release-number range.

### 6. Run an automated disposable installation

Run the strict installer state machine against a fresh QCOW2 target. It must
verify the expected media digest before QEMU starts and fail closed on an
unknown prompt, timeout, EOF, or QEMU failure. It must not fall back to sending
the next answer speculatively.

Retain the resolved answers, manifest, raw transcript, normalized event log,
and failure diagnostics. An incomplete target is never treated as a reference
image.

### 7. Validate the resulting image

At minimum, perform and record:

- `qemu-img info` and `qemu-img check`;
- a fresh boot without the installation ISO;
- confirmation of the expected filesystem, user, and `plan9.ini` settings;
- an orderly guest shutdown; and
- basic networking when it is part of the image profile.

Validation should use the supported host/QEMU profiles appropriate to the
release. A result observed only under Linux KVM does not by itself certify the
Windows WHPX profile, and vice versa.

### 8. Perform cleanup and storage postflight

After the run, record the same Windows free-space, WSL VHDX, WSL filesystem,
QCOW2 allocation, and run-directory measurements collected during preflight.
Keep the logs and manifests needed for review. Remove disposable failed or
superseded images only by exact, verified path; retain verified shared media
caches.

The qualification report should make retained large artifacts visible so they
can be reviewed or cleaned deliberately later.

### 9. Publish and promote separately

After review, add the release as available for explicit selection. Promotion
to the recommended default is a separate change after installation, startup,
networking, and relevant host-profile testing meet the project's current bar.
Follow the catalog and promotion rules in
[`05-multiple-installation-media-releases.md`](05-multiple-installation-media-releases.md).

## Compatibility matrix

The project should maintain a compact view of the actual support claims. For
example:

| Media ID | Installer profile | Answer schema | Qualified paths | Linux KVM | Windows profile | State |
| --- | --- | --- | --- | --- | --- | --- |
| `11554-amd64` | `9front-11554-v1` | `v1` | HJFS baseline | validated | pending automation | baseline |
| `<new>-amd64` | `<profile>` | `<schema>` | `<capabilities>` | `<result>` | `<result>` | candidate |

Digest values remain in the media manifest rather than being abbreviated into
the matrix. Each matrix row must link to its qualification record.

## Requalification triggers

Repeat the affected parts of this process when any of the following changes:

- installation-media bytes, including a repacked asset with the same label;
- installer profile, state machine, or prompt normalization;
- answer schema or the meaning of an existing answer;
- QEMU serial transport, storage topology, or boot configuration;
- automation dependency or host interaction mechanism; or
- a supported host, QEMU version family, or acceleration profile in a way that
  could change the observed install.

Not every code or documentation change requires a new manual install. The
qualification record should identify which evidence remains valid and which
checks were repeated.

## Failure policy

- Unknown media digests are rejected before QEMU starts.
- Unexpected installer output fails the automated run closed.
- A newly visible option is recorded even when the baseline selection remains
  valid.
- Failed transcripts are preserved, but failed disks are never published.
- No profile is silently reused because a release is newer or similarly named.
- Qualification exceptions and reduced support claims are explicit and
  reviewable.

## Initial checklist

For the next 9front release:

1. Complete and record the disk-space preflight.
2. Mirror and verify immutable media and its provenance.
3. Inspect the exact release installer and write the static comparison.
4. Run one manual console install with the 11554 baseline semantics.
5. Classify profile and answer-schema compatibility.
6. Update transcript fixtures and profile code if required.
7. Run one fresh automated install and post-install validation.
8. Complete storage postflight and clean unneeded images.
9. Review the qualification record and capability matrix.
10. Make the release available, then consider default promotion separately.
