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
