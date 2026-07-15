# Reproduce the Drawterm-ready variant

This is the human-readable form of [`postinstall.json`](postinstall.json), the
versioned profile consumed by P9QEMU's preparation automation. The procedure
starts from the exact stock image
`p9qemu-9front-11554-amd64-hjfs-gmt-002`, whose QCOW2 SHA-256 is
`1ef80c81a3f2dd09d2f173ff7dfa93d07ecee2ba453fc0f0964190adb6ee44a8`.

The procedure is reproducible as a sequence of inputs and checks. Guest
filesystem timestamps and allocation details mean separate builds are not
promised to produce byte-identical QCOW2 files.

## Safety model

The configured password is `p9qemu-demo`. It is public, intentionally simple,
and intended only for a demonstration VM whose QEMU forwards remain bound to
`127.0.0.1`. Do not expose this credential through bridged networking or a
non-loopback listener.

Preserve a copy or writable overlay of the parent. Never modify the immutable
cached stock image directly.

## Automated preparation

The internal Linux builder parses this directory's `postinstall.json`, verifies
the exact parent manifest and standalone QCOW2 digest, creates a new full copy,
drives the interaction through the retained serial console, and records the
before/after files, QEMU command, image checks, hashes, and console transcript:

```sh
uv run python tools/prepare_drawterm_image.py \
    --postinstall-profile images/p9qemu-9front-11554-amd64-hjfs-gmt-drawterm-001/postinstall.json \
    --parent-manifest /path/to/verified/image.json \
    --input-disk /path/to/verified/stock.qcow2 \
    --output-disk /path/to/new-drawterm.qcow2 \
    --output-dir /path/to/new-evidence \
    --source-commit FULL_40_CHARACTER_GIT_SHA \
    --accel kvm \
    --confirm-create-drawterm-copy
```

Use `--dry-run` first. The builder never mutates the input disk and refuses to
replace either output path.

## 1. Verify the starting state

Boot a writable copy of the parent image, accept its default `bootargs` and
`user[glenda]:` prompts, and open a 9front terminal. Mount the boot partition:

```text
9fs 9fat
```

Inspect `/n/9fat/plan9.ini`. It must contain exactly one of each selected line:

```ini
bootfile=9pc64
bootargs=local!/dev/sd00/fs -m 147
mouseport=ps2
monitor=vesa
vgasize=1024x768x16
console=0
```

It must not already contain `nobootprompt`, `nvram`, or `service`. Stop if a
selected line is missing, duplicated, or has a different value.

Confirm that `/dev/sd00/nvram` exists. The recipe is intentionally bound to
P9QEMU's qualified VirtIO-SCSI disk layout.

## 2. Apply the persistent boot settings

Append these exact lines to `/n/9fat/plan9.ini`:

```ini
nobootprompt=local!/dev/sd00/fs -m 147
nvram=#S/sd00/nvram
service=cpu
```

For example, from `rc`:

```text
echo 'nobootprompt=local!/dev/sd00/fs -m 147' >>/n/9fat/plan9.ini
echo 'nvram=#S/sd00/nvram' >>/n/9fat/plan9.ini
echo 'service=cpu' >>/n/9fat/plan9.ini
```

Inspect the complete file again. No pre-existing line should have changed,
and every selected setting must occur exactly once.

`nobootprompt` is deliberately preferred over `bootloop`: it attempts the
pinned root once without prompting instead of retrying indefinitely.

## 3. Configure NVRAM

Run:

```text
auth/wrkey
```

Answer the prompts exactly as follows:

```text
authid: glenda
authdom: 9front
secstore key: [press Enter]
password: p9qemu-demo
confirm password: p9qemu-demo
enable legacy p9sk1[no]: [press Enter]
```

The password fields do not echo. A blank final response accepts `no`, so the
legacy p9sk1 key is not enabled.

Shut down cleanly:

```text
fshalt
```

The automated builder also stops here. It starts a separate cold boot for
acceptance testing instead of relying on an in-guest reboot.

## 4. Validate an unattended boot

Start the prepared image without sending any keystrokes. It should use the
pinned HJFS root without a `bootargs` or user prompt, start as `service=cpu`,
and retain boot messages on the serial/QEMU console. Rio is expected through
Drawterm rather than in the QEMU display.

P9QEMU's standard runtime profile maps the guest CPU service to host port
`17019` and the guest authentication service to host port `17567`, both on
`127.0.0.1`.

On Windows PowerShell, with a locally built Drawterm executable:

```powershell
$env:PASS = 'p9qemu-demo'
& 'C:\Users\dharm\src\drawterm\build\msvc\drawterm.exe' `
    -h 'tcp!127.0.0.1!17019' `
    -a 'tcp!127.0.0.1!17567' `
    -u glenda `
    -c 'plumber; rio'
```

On Linux:

```sh
PASS=p9qemu-demo drawterm \
    -h 'tcp!127.0.0.1!17019' \
    -a 'tcp!127.0.0.1!17567' \
    -u glenda \
    -c 'plumber; rio'
```

The absolute Windows executable path above is a development-machine example;
end-user documentation will use the user's installed Drawterm path.

After checking the user, system name, GMT timezone, HJFS root, and networking,
run `fshalt`. Confirm that QEMU exits, the image passes `qemu-img check`, and
the immutable parent's digest did not change.

## Recovery

To restore an interactive `bootargs` question for troubleshooting, interrupt
9boot with Space and enter:

```text
clear nobootprompt
boot
```

This changes only the current boot environment; it does not rewrite
`plan9.ini`.
