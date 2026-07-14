# 9front 11554 AMD64 HJFS GMT reference profile

This directory defines the canonical answer file for the first fresh
publishable-image build. It is separate from the historical
`9front-11554-amd64-hjfs-manual-001` evidence and does not revise that build's
recorded `US_Pacific` choice.

The profile deliberately keeps the familiar 9front defaults `cirno` and
`glenda`, installs HJFS on a fresh 30 GiB QCOW2 disk, uses automatic guest
networking, and selects `GMT` for geographically neutral, daylight-saving-free
timestamps. It does not configure passwords, authentication secrets, Drawterm,
or other additional remote services, and it has no post-install customization
stage.

Before promotion, disposable-overlay validation must confirm the expected
user, home, system name, persistent timezone, the pinned stock home-file baseline,
installed `plan9.ini`, required network response, and orderly shutdown. The
QEMU MAC address remains runtime configuration and is not stored in the image.

## Local candidate checkpoint

A fresh build from source commit
`a245a026b90e6ec75d3c10e0dfce6f76af196c3c` completed installation,
required-network immutable-overlay validation, local promotion, archive
round-trip verification, an independent public-text privacy scan, and a
clean-room Linux boot of the exact archive-extracted image.

The resulting local-only identity is
`p9qemu-9front-11554-amd64-hjfs-gmt-001`. Its QCOW2 SHA-256 is
`0bed74080dd8e3ece1d50731ef7766425e3b806c89e215ea8951cc006fbf25ca`.
The 250,532,383-byte tar-gzip SHA-256 is
`b9b778a2fe3ebbd8495d026d6ca4d1d4b73d7d422327dad58d3024a756b7e10d`.
These values identify a retained local candidate, not a published release
asset. Windows testing of the exact candidate remains outstanding.
