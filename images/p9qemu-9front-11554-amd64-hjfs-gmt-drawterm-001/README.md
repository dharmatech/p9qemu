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

## Planned acceptance gate

Before publication, the exact derivative must pass all of the following:

1. a cold boot without a `bootargs` or user prompt;
2. retained serial boot messages and diagnostics;
3. loopback-only CPU and auth listeners through the documented host ports;
4. a real Drawterm login using the documented demonstration credential;
5. the expected `glenda`, `cirno`, GMT, HJFS, and networking state;
6. clean `fshalt`, `qemu-img check`, and immutable-parent digest checks; and
7. a disposable password-change test proving the old demonstration password
   no longer authenticates.

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

This digest identifies a preparation result, not a release candidate. It must
still pass unattended cold boot, actual host-side Drawterm authentication,
guest-state and networking checks, clean shutdown, and the disposable
password-change test before packaging or publication.
