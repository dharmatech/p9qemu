# 9front 11554 AMD64 HJFS GMT Drawterm image (stable revision 001)

This stable ready image contains a standalone 9front QCOW2 that boots
unattended as a CPU/auth server for Drawterm while retaining serial-console
diagnostics. Its immutable candidate assets passed fresh public-download
acceptance on Windows and Linux before promotion.

The credential for `glenda` is the intentionally public demonstration value
`p9qemu-demo`. The supported P9QEMU runtime exposes CPU and auth only through
loopback forwards. Do not bridge the VM or expose those services beyond
`127.0.0.1` until the credential is changed.

Create an independent writable instance:

```console
p9qemu image create https://github.com/dharmatech/p9qemu/releases/download/ready-9front-11554-amd64-hjfs-gmt-drawterm-001/image.json my-9front-drawterm
```

Start it:

```console
p9qemu start --instance my-9front-drawterm
```

The archive and external manifest are immutable revision 001 assets.
Corrections will use a new revision rather than replacing either file under
this tag.
