# Private Instance Distribution Safety

## Status

Future direction. This document records a possible safety model; current
`p9qemu` instance metadata and commands do not implement it.

## Motivation

A private development instance may contain SSH private keys, access tokens,
shell history, proprietary source, or other workstation-specific data. Copies
of that instance inherit the same material. A future workflow that exports or
publishes ready-to-run images must make accidentally distributing such an
instance difficult.

This protection is particularly important because a development instance may
look technically similar to a release candidate even though their provenance
and security properties are different.

## Fail-closed default

Ordinary writable instances should be ineligible for distribution by default.
Creating or copying an instance must not silently make it publishable. A future
export or publication command should require an explicit, separately recorded
distribution decision.

Instance metadata could eventually include a classification such as:

```json
{
  "security": {
    "distribution": "forbidden",
    "sensitive": true,
    "reasons": [
      "ssh-private-key"
    ]
  }
}
```

The exact schema remains open. A conspicuous file such as
`PRIVATE-DO-NOT-PUBLISH.txt` could supplement the structured metadata for
humans, but the structured metadata should be the enforceable source of truth.

## Inheritance and irreversible sensitivity

Security classification should follow instance lineage:

- a clone or checkpoint inherits all sensitivity declarations from its source;
- adding a credential can change an instance from clean to sensitive;
- an instance that has contained a credential must not be marked clean in
  place; and
- deleting a credential is not sufficient to establish a clean history.

This is intentionally monotonic: sensitivity can be added but not casually
removed. A public demonstration image should be rebuilt from a known-clean base
using a reproducible recipe in which the credential never appears. It should
not be produced by sanitizing a private development image after the fact.

## Future export and publication gate

A future `p9qemu image export` or publication workflow should operate on a
sealed export bundle rather than upload a writable development instance
directly. It should refuse to proceed unless all required conditions hold:

1. The instance lineage is eligible for distribution and contains no sensitive
   ancestor.
2. The user makes an explicit public-distribution attestation.
3. Required provenance, build, validation, and shutdown evidence is complete.
4. Automated checks find no likely private keys, tokens, credentials, history,
   local paths, or other prohibited material.
5. The resulting standalone image and bundle pass the normal integrity and
   release-candidate checks.

Automated scanning is defense in depth, not proof that an image is safe. The
clean-lineage requirement remains the primary protection.

## Intended threat model

This design prevents mistakes made through normal `p9qemu` workflows. It does
not try to prevent a user from bypassing `p9qemu`, editing local metadata, or
uploading a disk with another tool. The metadata is a safety interlock and an
auditable declaration, not a security boundary against a malicious local user.

## Example development workflow

A private Caml9 development seed containing an SSH key would be classified as
distribution-forbidden. Every experiment copied from it would inherit that
classification. If a Caml9 demonstration image were later needed, it would be
built independently from the clean Drawterm-ready base, acquire public source
without a private key, undergo the distribution checks, and only then become a
release candidate.

## Open design questions

- Whether classification belongs only in `instance.json` or also in a visible
  marker file.
- Which reasons and distribution states should be standardized.
- How a future managed clone or checkpoint command records lineage.
- Which guest-aware checks can be automated reliably across supported 9front
  filesystems.
- What evidence and explicit confirmation are required before producing a
  publishable export.
