# 9front 11554 AMD64 HJFS GMT Drawterm image

> **Build definition only.** The derivative recipe is now versioned, but no
> Drawterm-ready release asset has been published yet.

This variant starts with the immutable
[stock candidate 002](../p9qemu-9front-11554-amd64-hjfs-gmt-002/README.md),
changes it into a CPU/auth server, and suppresses the normal `bootargs` prompt.
The intended result boots without input and becomes ready for a Drawterm
connection while retaining serial-console diagnostics.

The image deliberately uses the public demonstration credential
`p9qemu-demo` for `glenda`. It is suitable only with P9QEMU's services bound to
`127.0.0.1`. Change the password before exposing an instance through bridged
networking, a non-loopback host forward, or another machine.

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

One separate security-mutation gate remains before publication:

- [ ] use a disposable overlay to change the password and prove the old
  demonstration password no longer authenticates.

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
published release candidate. The disposable password-change test still has to
pass before packaging or publication.

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
