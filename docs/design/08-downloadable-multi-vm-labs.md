# Downloadable Multi-VM Labs

## Status

Future direction beyond downloadable single-VM images and concurrent multi-VM
networking. This note records a possible p9qemu product layer; it does not add a
version 1 command or commit the project to the provisional CLI names below.

## Decision summary

It is feasible for p9qemu to publish a complete Plan 9 lab as versioned release
assets and let a user create and run it on Windows or Linux with a small number
of commands.

The lab should not be one opaque mutable bundle. It should be a versioned,
checksummed manifest that composes immutable role images, source-controlled
network profiles, and local writable overlays. p9qemu should be the
cross-platform orchestrator so the project does not have to maintain separate
Bash and PowerShell implementations of the same lifecycle.

This design depends on two earlier directions:

- [`01-downloadable-post-install-images.md`](01-downloadable-post-install-images.md)
  defines verified immutable base images and per-instance writable overlays.
- [`07-multiple-plan9-vms.md`](07-multiple-plan9-vms.md) evaluates concurrent
  networking and recommends per-VM management adapters plus one isolated lab
  Ethernet.

The lab feature should be implemented only after those lower-level workflows
are sufficiently stable and the multi-VM network has passed Windows and Linux
qualification.

## Proposed experience

The eventual workflow could be as small as:

```console
$ mkdir plan9-grid
$ cd plan9-grid
$ p9qemu lab create three-node-grid@1
$ p9qemu lab start
```

`lab create` would resolve an immutable lab version, download and verify its
referenced images, create one writable overlay per node, allocate local network
resources, and write inspectable local state. `lab start` would print every
QEMU command, start the nodes in dependency order, perform bounded readiness
checks, and display the addresses and drawterm instructions for each node.

Possible companion commands are:

```console
$ p9qemu lab status
$ p9qemu lab stop
$ p9qemu lab describe
$ p9qemu lab reset --node node-b
```

The vocabulary and persistence semantics remain provisional. In particular,
`create`, `start`, `stop`, and `reset` must have precise failure and data-loss
rules before becoming a public interface.

## Goals

- Let a user reproduce a documented multi-system Plan 9 environment.
- Keep immutable release artifacts separate from user-owned changes.
- Work on Windows and Linux without administrator-level host networking.
- Make the lab topology, guest roles, identities, and services inspectable.
- Provide direct host access to each VM without unique port arithmetic.
- Permit several independent copies of the same lab on one host.
- Preserve p9qemu's exact-command and dry-run transparency.
- Validate that the running environment matches its documented purpose.
- Make lab release provenance as reviewable as image-build provenance.

## Non-goals

- A general-purpose declarative interface for arbitrary QEMU commands.
- Executing scripts or command fragments supplied by an untrusted manifest.
- A cluster scheduler, remote hypervisor controller, or cloud service.
- Silently enrolling published images into an external identity provider.
- Hiding lab credentials or security limitations behind network isolation.
- Replacing ordinary single-VM `install` and `start` workflows.

## Artifact model

A conceptual release could contain:

```text
three-node-grid-v1/
  lab-manifest.json
  README.md
  SHA256SUMS
  node-a.qcow2.zst
  node-b.qcow2.zst
  node-c.qcow2.zst
```

The assets may live in one GitHub release while remaining individually named
and checksummed. The manifest should refer to immutable asset identities and
digests, not a moving `latest` URL.

At first, publishing one complete compressed image per role is preferable to a
clever multi-level backing chain. It duplicates common bytes but makes
download, verification, extraction, relocation, and recovery easier to reason
about. A later optimized layout could use:

```text
verified common base
        |
        +--> verified immutable role layer
                         |
                         +--> user's writable node overlay
```

That optimization should wait until p9qemu can safely manage backing-file
paths, cache garbage collection, and multi-level dependency checks. It must not
make a lab fragile when the cache or lab directory moves.

## Manifest boundaries

The downloaded lab manifest describes the desired guest environment. It must
not be a generic QEMU configuration language. Security-sensitive and
host-specific behavior remains in reviewed p9qemu profiles.

A manifest needs at least:

- schema version, immutable lab ID, and lab release version;
- human-readable title, description, and documentation asset;
- minimum compatible p9qemu and network-profile versions;
- an exact image identifier and digest for every node;
- node name, role, memory requirement, guest architecture, and boot priority;
- stable guest lab address and service expectations;
- dependency relationships among nodes;
- requested management-forward profile;
- readiness and end-to-end validation identifiers; and
- build and qualification provenance.

An illustrative shape is:

```json
{
  "schema": 1,
  "id": "three-node-grid",
  "version": "1",
  "minimum_p9qemu": "<future-version>",
  "network_profile": "isolated-ethernet-with-management-v1",
  "networks": {
    "lab": {
      "ipv4": "10.77.0.0/24"
    }
  },
  "nodes": [
    {
      "name": "node-a",
      "role": "server",
      "image": "9front-<release>-lab-node@1",
      "lab_ipv4": "10.77.0.11",
      "memory_mib": 1024,
      "boot_priority": 10,
      "management_forwards": "plan9-default-v1",
      "readiness": ["plan9-cpu-service"]
    },
    {
      "name": "node-b",
      "role": "client",
      "image": "9front-<release>-lab-node@1",
      "lab_ipv4": "10.77.0.12",
      "memory_mib": 1024,
      "boot_priority": 20,
      "depends_on": ["node-a"]
    }
  ]
}
```

This example is intentionally incomplete and is not a committed schema. Fields
such as `readiness` select a source-controlled p9qemu implementation; they do
not contain a shell command downloaded from the release.

Host loopback addresses, QEMU process IDs, log paths, socket or multicast
ports, and generated local MAC addresses do not belong in the immutable lab
definition. They are allocated for one local copy and recorded in local state.

## Local lab layout

After creation, an ordinary lab directory might contain:

```text
plan9-grid/
  lab.json
  nodes/
    node-a/
      disk.qcow2
    node-b/
      disk.qcow2
    node-c/
      disk.qcow2
  logs/
  run/
```

`lab.json` records the resolved immutable lab and image versions, backing-image
digests, generated node identities, guest addresses, selected host profile,
and locally allocated endpoint block. It is user-owned instance state and must
not be silently regenerated in a way that changes identities.

`run/` contains ephemeral process and control information. A stale PID file is
not proof that a process belongs to the lab; p9qemu must validate process
identity before signaling anything. Logs and durable creation metadata remain
after the VMs stop.

The shared cache contains verified, immutable base images. Guest writes always
land in the node overlays under the lab directory. Neither `lab start` nor a
failed create operation may modify a cached base.

## Network composition

The default lab profile should use the two-adapter topology from
[`07-multiple-plan9-vms.md`](07-multiple-plan9-vms.md):

- one private QEMU user-mode management network per node;
- one loopback address per node for repeated host-forward ports; and
- one shared, host-local QEMU Ethernet bus for lab traffic.

The immutable manifest defines the guest subnet and stable guest addresses.
The local lab instance chooses collision-prone host resources. For example:

```text
lab copy 1: host endpoints 127.77.1.11-13, private bus allocation A
lab copy 2: host endpoints 127.77.2.11-13, private bus allocation B
```

Both copies may use `10.77.0.11-13` inside their lab Ethernet because the buses
are isolated. They must use different bus and host-endpoint allocations.

The manifest names a semantic network profile rather than `dgram`, multicast,
or hub arguments. This allows p9qemu to use a different qualified transport on
Windows and Linux while presenting the same guest network. The resolved QEMU
commands still expose the exact backend to the user.

Optional later profiles could describe:

- a completely disconnected Ethernet;
- a shared QEMU NAT segment;
- a dual-homed Plan 9 gateway and protected inner subnet;
- user-supplied bridged networking; or
- a qualified Tailscale management overlay.

These are explicit profiles, not arbitrary manifest-supplied arguments. A lab
that requires an external account or host modification must declare that fact
before p9qemu changes any state.

## Orchestrator responsibilities

A Python implementation inside p9qemu should perform the portable work once
for both Windows and Linux:

1. resolve the exact lab manifest and every referenced image;
2. validate schema, compatibility, asset names, sizes, and digests;
3. download, decompress, and atomically publish immutable cache artifacts;
4. create node overlays without overwriting existing disks;
5. allocate and persist unique MACs, loopback endpoints, and a lab bus;
6. check QEMU capabilities, ports, paths, memory, and active-lab conflicts;
7. render and print every complete QEMU command;
8. launch nodes in explicit dependency order;
9. capture per-node logs and process identity;
10. run bounded readiness and end-to-end checks;
11. print a concise connection table and lab-specific instructions; and
12. stop or report partially started environments safely.

The orchestrator should not obscure manual operation. `--dry-run` should show
the resolved artifacts, local allocations, start order, and QEMU commands
without downloading, creating overlays, reserving endpoints, or launching
processes. A user should be able to copy the printed commands and understand
the topology.

## Startup, readiness, and shutdown

Boot ordering is necessary for labs with an authentication, file, or CPU
service dependency. Ordering alone is not readiness. p9qemu should start a
dependency, wait for a bounded source-controlled health check, and only then
start dependent nodes.

Health checks should be specific enough to prove the documented service is
available but should not require a general remote command-execution language
in the manifest. Examples include establishing a TCP connection on a known
loopback forward, completing a Plan 9 protocol handshake, or checking a
dedicated serial/QMP signal implemented by the image profile.

On failure, p9qemu reports:

- which nodes are running;
- which dependency or health check failed;
- where each log lives;
- which host endpoints remain allocated; and
- the exact stop or retry command.

Stopping should prefer an orderly guest shutdown so the Plan 9 file systems
are halted cleanly. The exact control path must be designed and qualified; a
QEMU process exit is not automatically evidence of a clean guest shutdown.
Forceful termination must be explicit, limited to validated lab processes, and
described as potentially unsafe for writable overlays.

## Identity, authentication, and secrets

Networking is easier than safely cloning a distributed Plan 9 environment.
Every published lab must define how it handles:

- Plan 9 user and authentication domains;
- auth-server keys and databases;
- file- and CPU-server service identities;
- default passwords or other teaching credentials;
- per-machine host names and keys;
- generated MAC addresses and local lab IDs; and
- optional external overlay identities.

Two acceptable release policies are:

1. **Disposable teaching lab.** It has published demo credentials, binds host
   access only to loopback, is clearly labeled untrusted, and is intended to be
   reset or discarded.
2. **First-run provisioned lab.** Creation generates or asks for credentials
   and produces unique local identity material before services are exposed.

The second is safer for reusable environments but requires a reliable
provisioning channel. The first is valuable for a reproducible tutorial if its
limitations are impossible to miss.

Release images must never contain personal credentials, private developer
keys, access tokens, Tailscale machine state, reusable Tailscale auth keys, or
other pre-enrolled external identities. If Tailscale is offered later, every
node enrolls after creation under the user's control.

## Initial lab sequence

The first lab should prove orchestration without making distributed
authentication the first debugging problem.

### Milestone 1: three-node network smoke lab

Publish three general-purpose 9front nodes with:

- unique names, MAC addresses, and static lab IPs;
- the same documented management-forward profile;
- direct IP connectivity on the isolated Ethernet;
- at least one simple service used by an automated end-to-end check;
- host connection instructions for every node; and
- no required external account.

This validates downloads, overlays, multi-process QEMU, interface setup,
host-forward reuse, concurrent lab copies, startup, logs, and shutdown.

### Milestone 2: Plan 9 service-role lab

After the transport is reliable, publish a lab that demonstrates Plan 9's
distributed design. A candidate topology could contain an authentication/file
service node, a CPU service node, and a client or terminal node. Its
documentation should explain the trust relationships, namespaces, mounts, and
normal connection flow, and the validation suite should exercise them rather
than merely pinging each guest.

Keeping these milestones separate makes failures diagnosable: Ethernet and
orchestrator bugs are established before authentication and service topology
are added.

## Release provenance

Every lab version needs a human-readable build record and machine-readable
qualification result. In addition to the image provenance required by
[`01-downloadable-post-install-images.md`](01-downloadable-post-install-images.md),
record:

- the exact lab-manifest digest;
- every referenced image ID and digest;
- guest configuration and provisioning procedure for each role;
- expected network interfaces, addresses, routes, and services;
- credential policy and sanitization evidence;
- p9qemu and QEMU versions used to qualify the lab;
- Windows and Linux host-profile results;
- readiness and end-to-end validation results; and
- known limitations and required external dependencies.

The automated installation and resolved-build records in
[`04-automated-installation-answer-files.md`](04-automated-installation-answer-files.md)
are a useful foundation. A new 9front media release should not silently become
a lab image; media qualification, image production, lab composition, and lab
promotion are separate reviewable gates.

## Validation matrix

Unit tests should use tiny synthetic fixtures and mocked process execution.
They should cover:

- strict manifest parsing and rejection of unknown or unsafe fields;
- immutable lab and image version resolution;
- digest failures and interrupted artifact publication;
- overlay creation without base modification;
- deterministic identity and address generation;
- endpoint and bus collision detection;
- two concurrent copies of the same lab;
- dependency sorting and cycle rejection;
- partial-start reporting and safe PID validation;
- dry-run immutability;
- prevention of arbitrary QEMU or shell fragments; and
- reset behavior that never removes an unintended disk.

Explicit integration qualification on Windows and Linux should prove:

1. creation from an empty cache;
2. cache reuse without re-downloading or mutating bases;
3. all three guests boot with unique identities;
4. lab ARP, IP, TCP, and UDP communication in every required direction;
5. direct host access to every guest on repeated port numbers;
6. outbound management connectivity when declared by the profile;
7. isolation between two simultaneous copies of the lab;
8. documented Plan 9 services and mounts work end to end;
9. an orderly stop and clean subsequent boot; and
10. useful recovery behavior after one node or readiness check fails.

Large image downloads and real QEMU processes belong in opt-in integration or
release-qualification jobs, not ordinary unit tests.

## Implementation stages

1. Qualify the multi-VM transport in
   [`07-multiple-plan9-vms.md`](07-multiple-plan9-vms.md) with disposable
   manually prepared guests.
2. Implement reusable network-profile and unique-identity command construction
   without adding release manifests.
3. Complete the single-image immutable-base and writable-overlay lifecycle.
4. Define and validate a minimal source-controlled lab schema with synthetic
   images.
5. Add local lab creation, dry-run, start, status, and safe stop behavior.
6. Build and qualify the three-node network smoke lab.
7. Publish it as an opt-in prerelease and collect host-compatibility results.
8. Add the first distributed Plan 9 service-role lab only after the transport
   and lifecycle are stable.

This ordering keeps the feature additive and ensures each layer is useful and
testable before release distribution is introduced.

## Open questions

1. Should labs have a separate catalog from single-image releases, or should
   one catalog contain both artifact types?
2. Should the first release contain full images per node or reuse one image
   plus a deterministic first-run role provisioner?
3. What exact local state is portable when a user moves a lab directory?
4. Which health-check mechanism is reliable on graphical and headless QEMU
   profiles?
5. How should an orderly guest shutdown be requested and confirmed?
6. Which fields belong in a lab manifest versus a reviewed p9qemu profile?
7. How should compatible p9qemu, QEMU, image, and lab versions be expressed
   without making upgrades brittle?
8. Which credential policy should the first public teaching lab use?
9. How should cache cleanup prove that no lab overlay depends on a base or role
   layer?
10. Should Tailscale remain entirely user-managed or eventually become an
    experimental provisioning profile after 9front amd64 qualification?
