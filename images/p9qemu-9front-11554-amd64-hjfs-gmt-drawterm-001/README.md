# 9front 11554 AMD64 HJFS GMT Drawterm image

> **Prerelease candidate.** Candidate 001 is immutable and has completed fresh
> public-download acceptance on Linux and Windows. It remains a prerelease
> pending an explicit promotion decision.

This variant starts with the immutable
[stock candidate 002](../p9qemu-9front-11554-amd64-hjfs-gmt-002/README.md),
changes it into a CPU/auth server, and suppresses the normal `bootargs` prompt.
The intended result boots without input and becomes ready for a Drawterm
connection while retaining serial-console diagnostics.

The image deliberately uses the public demonstration credential
`p9qemu-demo` for `glenda`. It is suitable only with P9QEMU's services bound to
`127.0.0.1`. Change the password before exposing an instance through bridged
networking, a non-loopback host forward, or another machine.

## Quick start

Install P9QEMU from the public repository:

```console
uv tool install git+https://github.com/dharmatech/p9qemu.git
```

Create a writable instance from the exact candidate manifest:

```console
p9qemu image create https://github.com/dharmatech/p9qemu/releases/download/ready-9front-11554-amd64-hjfs-gmt-drawterm-001/image.json my-9front-drawterm
```

Start the instance:

```console
p9qemu start --instance my-9front-drawterm
```

The VM boots without input and keeps the QEMU process running while it waits
for Drawterm. In another terminal, connect to CPU port `17019` and auth port
`17567` on `127.0.0.1` as `glenda` with `p9qemu-demo`.

Linux example:

```sh
PASS=p9qemu-demo drawterm \
    -h 'tcp!127.0.0.1!17019' \
    -a 'tcp!127.0.0.1!17567' \
    -u glenda
```

Windows PowerShell example, after setting `DRAWTERM` to the installed
executable:

```powershell
$env:PASS = 'p9qemu-demo'
& $env:DRAWTERM `
    -h 'tcp!127.0.0.1!17019' `
    -a 'tcp!127.0.0.1!17567' `
    -u glenda
```

The demonstration credential is public. Keep the default loopback-only
networking, or change it with `auth/wrkey` before broadening access.

## Transparent build inputs

- [Manual and automated build procedure](BUILD.md)
- [Machine-readable post-install recipe](postinstall.json)
- [Immutable parent ready-image manifest](../manifests/p9qemu-9front-11554-amd64-hjfs-gmt-002.json)

The Python preparation driver consumes `postinstall.json` directly. `BUILD.md`
shows the manual equivalent of every persistent guest change, including the
interactive `auth/wrkey` answers. Tests bind the document's important values
to the machine-readable profile so that the two representations cannot drift
silently.

## Acceptance gates

The exact derivative has passed the core cold-boot and Drawterm gate:

- [x] cold boot without a `bootargs` or user prompt;
- [x] retained serial boot messages and diagnostics;
- [x] loopback-only CPU and auth listeners through the documented host ports;
- [x] real Drawterm authentication using the demonstration credential;
- [x] the expected `glenda`, `cirno`, GMT, HJFS, `plan9.ini`, and networking
  state; and
- [x] clean `fshalt`, QEMU exit, `qemu-img check`, listener teardown, and an
  unchanged derivative digest.

The separate security-mutation gate has also passed:

- [x] use a disposable overlay to change the password, cold boot that overlay,
  prove the old demonstration password no longer authenticates, and prove the
  generated replacement password does authenticate.

All automated gates and fresh end-user acceptance against the public
prerelease are complete. Candidate 001 remains a prerelease pending an explicit
promotion decision.

## Local preparation checkpoint (2026-07-15)

The builder at source commit
`b867220cf5af46af8f59e614fd696ccccbd8c884` successfully prepared a
local-only derivative from the exact stock candidate. The immutable parent
retained SHA-256
`1ef80c81a3f2dd09d2f173ff7dfa93d07ecee2ba453fc0f0964190adb6ee44a8`;
the post-install profile SHA-256 was
`b04af90924fdb79838e9159995014e2428ec0ba23a9ab25efb71b494bee81e5f`;
and the resulting QCOW2 SHA-256 was
`7ff689b7b614f6884bf0a1ac525fca10b750934d99640e744823f450d28ff6b8`.

The recorded run verified the parent manifest and standalone QCOW2, checked
the exact source `plan9.ini`, confirmed `/dev/sd00/nvram`, staged each bounded
serial command independently, configured NVRAM through `auth/wrkey`, installed
only the three qualified boot settings, completed `fshalt`, and passed
`qemu-img check` before and after preparation. The cleartext demonstration
password did not appear in the serial transcript. The failed development
attempts that exposed shell-readiness and serial-line-length constraints were
discarded before this fresh, source-bound build.

This digest identifies a prepared and core-validated derivative, not yet a
published release candidate.

## Local cold-boot and Drawterm checkpoint (2026-07-15)

Source commit
`f9d9f9901a550d7b56274913b0773ef5ac81e596` validated the exact derivative
SHA-256
`7ff689b7b614f6884bf0a1ac525fca10b750934d99640e744823f450d28ff6b8`
on Ubuntu 22.04 with KVM and QEMU 6.2.0. The host Drawterm executable came
from source commit `8a88fb5b8c75450d2e20ae1c7839d823bb1f6fad` and had SHA-256
`f808e2eedebdf7ea19bccaeac84d4d7cdd424279912d2efeeab3ba0cefa35a78`.

The run sent no serial input. It observed an unattended HJFS boot, loopback CPU
and auth listeners, real authentication, the qualified guest identity and GMT
timezone, a successful ping, and the exact target `plan9.ini`. The final
Drawterm command used the
[FQA 7.2.1](https://fqa.9front.org/fqa7.html#7.2.1) non-console recipe to bind
`#S` over `/dev` and mount `/dev/sd00/9fat`, then ran `fshalt`. The CPU-server
serial path reported `hjfs: ending` before QEMU exited. Both the immutable
derivative and disposable overlay passed `qemu-img check`; the derivative
digest remained unchanged; the overlay was removed; and neither host forward
accepted connections afterward.

All fourteen recorded checks passed, and every recorded artifact digest was
re-verified. The cleartext demonstration password did not occur anywhere in
the evidence bundle. The first CPU connection needed one bounded retry while
the p9any protocol became ready; the remaining five acceptance commands
authenticated on their first attempts.

### Acceptance development findings

| Observation | Resulting qualification |
| --- | --- |
| One long Drawterm `-c` command stopped before all markers. | Use independently authenticated guest commands shorter than 128 characters. |
| A TCP listener could accept before p9any negotiation was ready. | Retry only the observed pre-authentication `p9any ... hung up` condition, with a fixed bound. |
| A Drawterm CPU namespace did not initially expose `/dev/sd00/9fat`. | Follow FQA 7.2.1: `bind -b '#S' /dev`, then pass `/dev/sd00/9fat` explicitly to `9fs`. |
| `9fs 9fat` posts `/srv/dos`, keeping the command session alive. | Inspect 9fat in the final authenticated command and immediately run `fshalt`. |
| CPU-server shutdown reached QEMU EOF after `hjfs: ending` without serial `done halting`. | Accept either qualified shutdown transcript, always followed by QEMU exit and image checks. |
| A bind probe saw TCP `TIME_WAIT` after QEMU exited. | Require that the ports stop accepting connections; keep the stricter bind test before launch. |

## Local password-rotation checkpoint (2026-07-15)

Source commit
`392b3c3a3d330251923d5289e2b6a9838583a90c` ran the separate
security-mutation gate against the exact derivative SHA-256
`7ff689b7b614f6884bf0a1ac525fca10b750934d99640e744823f450d28ff6b8`.
The host Drawterm executable was still the source-bound binary from commit
`8a88fb5b8c75450d2e20ae1c7839d823bb1f6fad`, with SHA-256
`f808e2eedebdf7ea19bccaeac84d4d7cdd424279912d2efeeab3ba0cefa35a78`.

The Linux-only gate generated a 24-character lowercase hexadecimal password in
memory, authenticated with the public demonstration credential, supplied the
fixed `auth/wrkey` answers through unrecorded Drawterm stdin, and halted. It
then cold-booted the same disposable overlay without serial input. The old
credential received the release-qualified authentication rejection, while the
generated replacement credential completed an authenticated marker command.
That positive control prevents an unavailable server or generic transport
failure from being mistaken for successful password rejection.

All eight recorded checks passed: the NVRAM write marker, mutation session
exit, mutation shutdown, old-password rejection, new-password acceptance,
independent verification boot, verification shutdown, and port release. The
overlay and immutable derivative passed `qemu-img check`; the derivative
digest remained unchanged; and the overlay was removed. All eighteen artifact
digests were independently rechecked. Neither password appeared in the event
log, process output, serial transcripts, commands, artifacts, or manifest.

### Password-rotation development findings

| Observation | Resulting qualification |
| --- | --- |
| Linux kept the forwarded CPU port in `TIME_WAIT` after the mutation QEMU exited. | Require both ports to stop accepting connections, then wait up to 90 seconds for strict bindability before the verification QEMU starts. |
| This release rejects the obsolete key as `cannot read authenticator`, rather than Drawterm's alternate `password mismatch`/`wrong password` pair. | Accept only the pinned release signature and only after the generated replacement credential succeeds in the same cold boot. |

## Local candidate packaging checkpoint (2026-07-15)

The source-bound packager at commit
`1349ece2c9c40403c219e3a1aac413f524607253` built and round-trip verified the
exact derivative as
`p9qemu-9front-11554-amd64-hjfs-gmt-drawterm-001.tar.gz`.

The immutable measurements are:

- archive bytes: `250535781`;
- archive SHA-256:
  `2760028a2b13a844d33436f2d85140f6798ee84546c8668f1ffaa3bd135cb24f`;
- archive inventory: 22 members, 17 files, and 559043403 expanded file bytes;
- internal manifest SHA-256:
  `3a139b189dd55bf538d1011e06676c25df07a265caef87bf1f4ce811fbc06d2c`;
- QCOW2 bytes: `559022080`, with 32212254720 virtual bytes; and
- QCOW2 SHA-256:
  `7ff689b7b614f6884bf0a1ac525fca10b750934d99640e744823f450d28ff6b8`.

The 1472-byte external
[`image.json`](../manifests/p9qemu-9front-11554-amd64-hjfs-gmt-drawterm-001.json)
has SHA-256
`d04b06e49c5357cd95b8a8dd47457cd2e935b89d8e49117032b29206874aead2`.
It was generated by streaming the completed archive and binds all measurements
above to the immutable tag
`ready-9front-11554-amd64-hjfs-gmt-drawterm-001`.

The archive contains the complete standalone QCOW2, the exact post-install
profile, the parent external manifest, path-free preparation and validation
records, selected image-check evidence, and the public password-rotation
result. Raw boot, Drawterm, and password-mutation transcripts are deliberately
excluded. The internal manifest binds their retained private source manifests
by SHA-256.

Publication is authorized only as a non-Latest prerelease with exactly two
assets: `image.json` and the archive above. Neither asset may be replaced under
the tag; a correction requires candidate 002.

## Public prerelease checkpoint (2026-07-15)

Candidate 001 was published as a non-Latest GitHub prerelease under the
immutable tag
[`ready-9front-11554-amd64-hjfs-gmt-drawterm-001`](https://github.com/dharmatech/p9qemu/releases/tag/ready-9front-11554-amd64-hjfs-gmt-drawterm-001).
The lightweight tag points exactly to commit
`a654cafbe670c3fd8b58255c02ed8a71acec4465`, which contains the external
manifest and publication record.

The release was constructed as a draft and verified before publication.
GitHub's server metadata independently reported exactly two uploaded assets:

| Asset | Bytes | Server SHA-256 |
| --- | ---: | --- |
| `image.json` | 1472 | `d04b06e49c5357cd95b8a8dd47457cd2e935b89d8e49117032b29206874aead2` |
| `p9qemu-9front-11554-amd64-hjfs-gmt-drawterm-001.tar.gz` | 250535781 | `2760028a2b13a844d33436f2d85140f6798ee84546c8668f1ffaa3bd135cb24f` |

After publication, an anonymous download reproduced the exact manifest bytes.
The public archive endpoint returned its exact content length and byte-range
support. An isolated native-Windows `p9qemu image create --dry-run` fetched and
verified only the public manifest, printed the pinned archive and image
digests, and created neither an archive nor an instance.

The required fresh-cache end-user workflow has now completed on Windows and
Linux, as recorded below. The release remains a candidate until an explicit
promotion decision is made.

## Public Windows end-user acceptance (2026-07-15)

The native-Windows gate started in an empty dedicated VM directory with
P9QEMU 0.1.0 installed from public repository commit
`fc98dc0a42ba09bdf96ca134c2c68b192c65beaa`. The default cache did not contain
this Drawterm candidate.

The public `p9qemu image create` workflow fetched and verified the 1472-byte
manifest, downloaded the 250535781-byte archive, matched both published
SHA-256 digests, verified and cached the standalone read-only QCOW2, and
created `instance-whpx` as a writable overlay with the exact cached base as its
QCOW2 backing file.

`p9qemu start --instance instance-whpx --dry-run --accel whpx` fully reverified
the instance and rendered the qualified Windows profile
`-accel whpx,kernel-irqchip=off -display sdl`. The real start reached the
unattended CPU/auth service. A graphical native-Windows Drawterm connection
started Rio with the public demonstration credential. Inside Rio, the user was
`glenda`, the system name was `cirno`, the working directory was
`/usr/glenda`, the persistent timezone matched GMT, and a ping to `google.com`
succeeded.

A separate command-line Drawterm connection printed the exact marker and
identity:

```text
P9QEMU_CLI_OK
glenda
cirno
```

The native Drawterm executable had SHA-256
`746938acdef38625505389886481965d68fc2b91215eee265a46eb6502d4df0a`.
Its adjacent source checkout was clean at commit
`8958bdbc84f56f4a05df586e92a8d85e7ca29f07`; the executable was not rebuilt as
part of this gate, so the acceptance record binds the binary digest rather than
claiming a reproducible source build.

Command-line `fshalt` disconnected the graphical session and closed QEMU. The
post-halt P9QEMU verifier passed again. Both the 786432-byte overlay and the
559022080-byte cached base passed `qemu-img check`, reported clean QCOW2 dirty
flags, and retained the exact QCOW2 backing relationship. The base remained
read-only and its SHA-256 remained
`7ff689b7b614f6884bf0a1ac525fca10b750934d99640e744823f450d28ff6b8`.
No QEMU or Drawterm process remained, and neither loopback service port was
listening.

After acceptance, Windows reported 178942939136 free bytes. The Ubuntu WSL
VHDX remained exactly 219465908224 bytes. The verified Windows cache and small
acceptance overlay are retained pending the cross-platform cleanup decision.

## Public Linux end-user acceptance (2026-07-16)

The Linux gate started in an empty dedicated WSL VM directory with P9QEMU
0.1.0 installed from public repository commit
`796cfa7af534164f8d2297b349321017997151f0`. The command set
`XDG_CACHE_HOME` to a cache beneath that directory so the gate could prove a
cold public download without reusing or modifying the user's normal P9QEMU
cache. This test-only override is not required for ordinary use.

The public `p9qemu image create` workflow fetched and verified the 1472-byte
manifest, downloaded the 250535781-byte archive, matched both published
SHA-256 digests, verified and cached the standalone read-only QCOW2, and
created `instance-kvm` as a writable overlay with that exact cached base as its
QCOW2 backing file.

`p9qemu start --instance instance-kvm --dry-run --accel kvm` fully reverified
the instance and rendered the qualified Linux profile `-cpu host -accel kvm`.
The real start reached the unattended CPU/auth service. Native Windows
Drawterm connected graphically across the WSL loopback forwards and started
Rio with the public demonstration credential. Inside Rio, the user was
`glenda`, the system name was `cirno`, the working directory was
`/usr/glenda`, the persistent timezone matched GMT, and a ping to `google.com`
succeeded.

A separate native-Linux command-line Drawterm connection printed the exact
marker and identity:

```text
P9QEMU_CLI_OK
glenda
cirno
```

The Linux Drawterm executable had SHA-256
`f808e2eedebdf7ea19bccaeac84d4d7cdd424279912d2efeeab3ba0cefa35a78`.
Its adjacent source checkout was at commit
`8a88fb5b8c75450d2e20ae1c7839d823bb1f6fad` and contained untracked build
products. The executable was not rebuilt as part of this gate, so this
acceptance record binds the binary digest rather than claiming a reproducible
source build.

Command-line `fshalt` closed QEMU. The post-halt P9QEMU verifier passed again.
Both the 851968-byte overlay and the 559022080-byte cached base passed
`qemu-img check`, reported clean QCOW2 dirty and corruption flags, and retained
the exact QCOW2 backing relationship. The base remained mode `0444`, and its
SHA-256 remained
`7ff689b7b614f6884bf0a1ac525fca10b750934d99640e744823f450d28ff6b8`.
The acceptance overlay had SHA-256
`9b0b52bc8d10e51da3c1bb55856a6a92b6377f1d5a6b52f0f2cace5931eedb5c`.
No QEMU or Drawterm process remained, and neither loopback service port was
listening.

After the Linux gate, Windows reported 175899619328 free bytes. The dedicated
WSL acceptance tree used 773 MiB, while the writable overlay itself remained
under 1 MiB. The Ubuntu WSL VHDX remained exactly 219465908224 bytes, unchanged
from the Windows acceptance checkpoint. The isolated cache and acceptance
overlay are retained pending the promotion and cleanup decisions.
