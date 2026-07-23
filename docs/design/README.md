# Design Note Index

The design notes preserve both current architecture and the evidence that led
to it. They are not a linear implementation checklist. Read the smallest
relevant set, and confirm current behavior against code, tests, manifests, and
[the project-status snapshot](../project-status.md).

| Note | State | Read when |
| --- | --- | --- |
| [00 — Introduction and initial design](00-intro.md) | Version 1 baseline; partly historical | You need the original transparency, packaging, platform, and CLI contract. |
| [01 — Downloadable post-install images](01-downloadable-post-install-images.md) | Phased direction whose implemented boundary moved to note 10 | You need the motivation and high-level product model for downloadable images. |
| [02 — QEMU diagnostics and installation](02-qemu-diagnostics-and-installation.md) | Limited version 1 behavior plus future direction | You are changing missing-QEMU guidance, a future doctor command, or host setup assistance. |
| [03 — Windows WHPX experiments](03-windows-whpx-experiments.md) | Completed experimental evidence | You are changing Windows acceleration, display, CPU, or fallback behavior. |
| [04 — Automated installation answer files](04-automated-installation-answer-files.md) | Experimental pinned-release workflow with implemented tooling | You are changing answer files, pexpect transport, installer states, transcripts, or automated builds. |
| [05 — Multiple installation-media releases](05-multiple-installation-media-releases.md) | Future direction | A second validated 9front media release is being considered. |
| [06 — New-release qualification](06-new-release-qualification.md) | Proposed operational gate | You are evaluating new upstream media or running large Windows/WSL validation. |
| [07 — Multiple Plan 9 VMs](07-multiple-plan9-vms.md) | Future feasibility study | You are considering concurrent guests, networking isolation, or port allocation. |
| [08 — Downloadable multi-VM labs](08-downloadable-multi-vm-labs.md) | Future product direction | You are considering downloadable topologies above the single-VM layer. |
| [09 — Release-candidate promotion](09-release-candidate-promotion.md) | Implemented local packaging and verification; publication remains external | You are preparing, reviewing, packaging, or promoting a ready-image candidate. |
| [10 — Ready-image manifest and cache](10-ready-image-manifest-and-cache.md) | Current implemented schema, acquisition, cache, instance, and acceptance boundary | You are changing manifests, downloads, extraction, cache identity, overlays, instance startup, or public acceptance. |
| [11 — Private instance distribution safety](11-private-instance-distribution-safety.md) | Future direction | You are considering credentials, sensitivity inheritance, export, scanning, or publication refusal. |

## Routing principles

- User-facing command behavior belongs in the root
  [README](../../README.md).
- Current milestone state belongs in
  [docs/project-status.md](../project-status.md).
- Operational instructions belong in `docs/guides`.
- Exact media and image identity belongs in machine-readable manifests and the
  corresponding `media` or `images` directory.
- Design notes should retain unsuccessful experiments and superseded reasoning
  when they explain an implemented choice.

When a note's proposal becomes implemented, update its status or add an explicit
pointer to the later document that defines the implemented boundary. Do not
silently rewrite historical evidence as though it had always described the
final system.
