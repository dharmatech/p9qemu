# Multiple Plan 9 VMs on One Host

## Status

Future direction and feasibility study. This note does not expand the version 1
implementation scope. It records the networking options that should be
prototyped before p9qemu promises concurrent multi-VM support.

## Decision summary

Running several 9front systems in separate QEMU processes on one Windows or
Linux host is feasible without creating a host bridge or placing the guests on
the physical LAN.

The recommended target topology gives every VM two virtual network adapters:

1. a **management adapter** attached to that VM's own QEMU user-mode network;
   and
2. a **lab adapter** attached to one shared, isolated QEMU virtual Ethernet
   bus.

The management adapter provides outbound connectivity and host-to-guest port
forwards. Each VM receives a different host loopback address, allowing the same
host port numbers to be reused for every VM. The lab adapter gives the Plan 9
systems ordinary Layer 2 and IP connectivity to one another, using standard
guest service ports and without translating traffic between guests.

Conceptually:

```text
                              host
                 management endpoints on loopback
              127.77.1.11   127.77.1.12   127.77.1.13
                    |             |             |
              QEMU user/NAT  QEMU user/NAT  QEMU user/NAT
                    |             |             |
                 ether0        ether0        ether0
                 node-a        node-b        node-c
                 ether1        ether1        ether1
                    \             |             /
                     +-- isolated QEMU Ethernet --+
                         10.77.0.11/24
                         10.77.0.12/24
                         10.77.0.13/24
```

The two networks have deliberately different jobs. The user-mode networks are
private management paths; the shared Ethernet is the network being studied.
This avoids making a lab depend on host administrator privileges, TAP devices,
Windows-specific virtual switches, or the user's physical LAN.

This topology is a design recommendation, not yet a compatibility claim. The
shared Ethernet backend and loopback allocation must be exercised with real
9front guests and supported QEMU versions on both Windows and Linux.

## Current behavior and limitation

The current runtime profile creates one independent QEMU user-mode network per
VM. It binds a fixed set of forwards to `127.0.0.1` and gives every VM the same
fixed MAC address, `00:20:91:37:33:77`.

This works well for one VM. It creates two problems when several instances are
started concurrently:

- the second QEMU process cannot bind host ports already owned by the first;
  and
- the fixed MAC address becomes invalid as soon as those VMs share a Layer 2
  network.

Independent user-mode networks also do not form a common guest LAN. Their
similar default addresses are not evidence that the guests share a subnet;
each QEMU process owns a separate private network.

Concurrent support therefore requires unique VM identities, explicit network
topology, host-endpoint allocation, and conflict detection before launch.

## Goals

- Run multiple 9front VMs concurrently as an unprivileged user.
- Support Windows and Linux without changing persistent host networking.
- Give guests direct connectivity to one another on ordinary Plan 9 ports.
- Keep host access private to loopback by default.
- Preserve outbound connectivity where the lab profile calls for it.
- Permit more than one isolated lab to run at the same time.
- Print every complete QEMU command before execution.
- Keep MAC, IP, port, and bus allocation deterministic and inspectable.
- Leave room for routed, firewalled, disconnected, and overlay-network labs.

## Non-goals

- Replacing QEMU with a general VM or container manager.
- Automatically modifying host firewall, bridge, routing, or adapter settings.
- Exposing guest services to the physical LAN by default.
- Hiding QEMU networking behind arbitrary downloaded command fragments.
- Treating an IP overlay as equivalent to a shared Ethernet segment.
- Promising cross-host labs in the first implementation.

## Feasibility evidence

QEMU's official [network emulation
documentation](https://www.qemu.org/docs/master/system/devices/net.html) states
that user-mode networking requires no root privilege, that `hostfwd` can direct
host connections to a guest, and that socket backends can connect emulated
networks across QEMU processes. QEMU's [invocation
reference](https://www.qemu.org/docs/master/system/qemu-manpage.html) documents
both a multicast socket bus and the newer `dgram` multicast form, as well as
explicit host addresses for `hostfwd` rules.

Preliminary development-host checks on Windows found that QEMU 10.2 advertises
the `user`, `socket`, `stream`, `dgram`, and `hubport` network backend types.
The same TCP port was also bound successfully at the same time on
`127.0.0.2` and `127.0.0.3`. These observations support the proposed design but
do not replace a complete VM-level qualification run.

9front supports multiple IP interfaces and normal forwarding and translation
controls. Its [`ip`(3)
manual](https://git.9front.org/plan9front/plan9front/eb8fe8137b742646e9f3402149596eb8da62cc72/sys/man/3/ip/f.html)
documents `iprouting` for forwarding between interfaces and `trans` for source
address translation. This makes a Plan 9 gateway VM feasible as an optional
lab role.

## Recommended two-adapter topology

### Management adapter

Each VM retains its own QEMU user-mode network. This adapter should normally be
the default route and may use QEMU-provided DHCP, just as a single VM does now.
It provides:

- outbound TCP and UDP connectivity through QEMU;
- optional guest access to host services exposed by QEMU;
- loopback-only host forwards for drawterm and other management services; and
- failure isolation from the other VMs' management networks.

Every VM receives a distinct host loopback address. For example:

```text
127.77.1.11:564 -> node-a:564
127.77.1.12:564 -> node-b:564
127.77.1.13:564 -> node-c:564
```

The same principle applies to the complete p9qemu forward profile. A forward
may use the guest's standard port one-to-one, or retain an established p9qemu
compatibility mapping. The important property is that the mapping is identical
for every VM; the loopback address, rather than a surprising port offset,
selects the destination.

One lab instance can reserve a loopback block such as `127.77.1.0/24`, while a
second lab uses `127.77.2.0/24`. The precise range and persistence rules remain
an implementation decision. Allocation must be checked against active
listeners and saved in local lab state rather than inferred anew on every run.

Linux and Windows qualification must confirm the chosen part of `127.0.0.0/8`
can be bound without adapter configuration on supported hosts. If a supported
host restricts usable loopback addresses, deterministic port allocation remains
a fallback, but it is not the preferred user experience.

### Lab adapter

Each VM also receives a second VirtIO network adapter connected to a common
virtual Ethernet bus. The adapter requires a unique MAC address and should use
a stable static lab address initially. Static addressing avoids introducing a
DHCP-server dependency before the Ethernet transport itself is qualified.

A candidate QEMU fragment using the current `dgram` interface is:

```console
-device virtio-net-pci,netdev=lab0,mac=52:54:77:01:00:0b \
-netdev dgram,id=lab0,remote.type=inet,remote.host=239.192.77.1,remote.port=37701,local.type=inet,local.host=127.0.0.1
```

Every QEMU process in that lab would use the same multicast group and port but
a different guest MAC. The legacy `socket,mcast=...` form expresses the same
general bus model and may be useful for compatibility testing.

The bus must be constrained to the local host. A unique group/port pair is
needed for every active lab, and the backend should bind to loopback rather
than an outward-facing host interface. p9qemu must not assume that multicast
selection and loopback behavior are identical on Windows and Linux; the exact
backend is provisional until packet-level testing is complete.

If loopback multicast proves unreliable on a supported host, the public lab
model should remain unchanged while the implementation uses another
unprivileged QEMU socket arrangement or a small local user-space Ethernet
switch. The guest-visible contract is more important than committing the
manifest format to one QEMU transport.

### Guest routing

The management adapter should normally own the default route. The lab adapter
should receive only the directly connected lab subnet unless a particular lab
defines a router. For the example above:

```text
ether0: QEMU user-mode network and default route
ether1: 10.77.0.11/24, no default route
```

This keeps Internet traffic on the management path and guest-to-guest traffic
on the isolated lab Ethernet. Interface naming, MAC assignment, boot-time
configuration, ARP behavior, and route selection must be verified with the
exact published 9front image profile.

## Alternatives

### Different host port ranges for every VM

This is feasible and is the smallest change to the current runtime profile.
For example, one VM could use the existing forwards and another could add a
fixed offset.

It is useful as an explicit manual escape hatch and perhaps as a fallback on a
host with restricted loopback behavior. It is not the preferred lab interface:
users must remember a different port map for every VM, configuration becomes
harder to explain, and it does not by itself create guest-to-guest
connectivity.

### Different host loopback addresses

Assigning one loopback address per VM solves host-forward contention without
changing port numbers. It is simpler and more legible than port offsets. On its
own it still leaves the guests in separate user-mode networks, so it is best
used as the management half of the recommended two-adapter topology.

### One shared QEMU user-mode network

QEMU hubs can connect a user-mode backend and a socket backend in one QEMU
process. Other QEMU processes can then join the socket network, making one
shared user-mode LAN and NAT boundary possible.

This approach is feasible and deserves a prototype. It has attractive
properties: one DHCP service, one guest subnet, and direct communication among
the guests. It also introduces a coordinator process or coordinator VM whose
lifecycle owns the shared NAT backend and every host forward. Stable guest
addresses, boot ordering, coordinator failure, and centralized forward
configuration become part of the lab contract.

It is therefore a strong optional profile, especially for experiments that
specifically need a shared NAT segment, but it is less independent than the
recommended per-VM management networks plus a separate lab Ethernet.

### A Plan 9 gateway or firewall VM

One dual-homed Plan 9 VM can act as the host-reachable entry point and route or
translate traffic for an inner lab network. The host connects to that VM first;
from there, Plan 9 tools reach every internal system.

This is technically feasible through 9front's routing and translation
controls. It is also educational because the gateway is itself part of the Plan
9 environment. Its disadvantages are intentional operational complexity: it
adds a boot dependency, a single point of failure, routing configuration, and
an extra hop when direct host access to every VM would be more convenient.

The gateway should be offered as a lab topology, not required as p9qemu's basic
multi-VM control plane.

### Host TAP and bridged networking

A TAP adapter and host bridge can place every VM on a host-controlled or
physical network. This is the most conventional route to first-class LAN
membership and can be useful when other physical systems must reach the
guests.

It is not the portable default for p9qemu. Setup differs significantly between
Windows and Linux, often requires administrator privileges, changes host-wide
state, interacts with firewalls and VPNs, and may expose deliberately insecure
lab services beyond the host.

Bridge support can remain an advanced user-supplied profile after the
unprivileged topology is working.

### Tailscale as an overlay

Tailscale is a compelling optional IP management plane. If tailscaled is
qualified inside the target guest, the host and every VM can receive stable
overlay addresses and communicate on ordinary IP ports even across different
physical hosts.

It is complementary to, not a replacement for, the shared lab Ethernet:

- Tailscale is a Layer 3 overlay and does not reproduce Ethernet broadcast,
  ARP, or arbitrary Layer 2 experiments.
- It introduces enrollment, identity, control-plane, and software-update
  dependencies.
- Published images must never contain a developer's Tailscale node state,
  reusable authentication key, or other tailnet credentials.
- The official [Plan 9 port
  report](https://tailscale.com/blog/plan9-port) says its initial testing used
  9legacy and calls out 9front porting and `GOARCH=amd64` verification as future
  work.

For p9qemu's current 9front amd64 target, Tailscale should remain an
experimental, opt-in integration until exact Go, Tailscale, 9front, QEMU, and
host profiles have been reproduced and documented. It could later be valuable
for cross-host management even when a local QEMU Ethernet remains the data
plane.

## Comparison

| Approach | Host changes | Guest-to-guest | Direct host access | Primary use |
| --- | --- | --- | --- | --- |
| Per-VM port ranges | None | No | Yes, awkward ports | Immediate manual fallback |
| Per-VM loopback addresses | None | No | Yes, repeated port map | Management half of recommended design |
| Shared QEMU user network | None | Yes, shared LAN | Yes, centrally forwarded | Optional shared-NAT lab |
| Two adapters per VM | None | Yes, isolated Ethernet | Yes, per-VM loopback | Recommended general topology |
| Plan 9 gateway VM | None | Yes, routed | Through gateway | Firewall and routing labs |
| TAP/host bridge | Host-specific, often privileged | Yes | Yes, LAN-visible | Advanced physical-LAN integration |
| Tailscale | Guest enrollment and external control plane | Yes, IP only | Yes | Optional overlay and cross-host management |

## Identity and allocation rules

Concurrent networking cannot retain the version 1 fixed MAC as a global
default. The eventual implementation needs explicit identities for:

- each management adapter;
- each lab adapter;
- the lab Ethernet bus;
- every guest lab IP address;
- every host loopback endpoint; and
- the local lab instance itself.

Generated MAC addresses should use a locally administered unicast prefix and
be deterministic within one local lab instance. They must not be copied from a
published developer VM. Guest host names and IP addresses may come from the
versioned lab definition, while collision-prone host resources belong in local
instance state.

Two separately created copies of a lab may reuse the same guest IP plan because
their Ethernet buses are isolated. They must not accidentally join the same
bus or claim the same host loopback endpoints.

## Safety and transparency

- Host forwards bind to loopback unless the user explicitly chooses another
  profile.
- A downloaded manifest cannot contribute arbitrary QEMU arguments or shell
  text.
- Network profiles are implemented and reviewed in p9qemu source.
- Every full QEMU command is printed before its process starts.
- p9qemu checks endpoint and bus conflicts before launching any VM.
- A partial start reports exactly which processes are running.
- Guest credentials and services are considered separately from transport
  isolation; an isolated network is not a substitute for image hygiene.
- Bridge, firewall, adapter, and route changes on the host remain outside the
  default workflow.

## Prototype and qualification plan

1. Add a test-only or private command builder that can express two explicit
   `-netdev`/`-device` pairs with unique MAC addresses.
2. Start two disposable 9front VMs on a loopback-bound QEMU Ethernet bus and
   verify ARP, IP, TCP, and UDP in both directions.
3. Repeat with three VMs to prove that the backend is a bus rather than a
   point-to-point link.
4. Verify that every VM retains outbound management connectivity and that the
   default route does not migrate to the lab adapter.
5. Bind the same forward ports on different loopback addresses and connect to
   every guest independently from the host.
6. Start two copies of the topology concurrently and verify complete lab and
   host-endpoint isolation.
7. Repeat the matrix on supported Windows and Linux QEMU profiles, including
   TCG and the relevant tested accelerators.
8. Record packet loss, multicast or socket limitations, shutdown behavior, and
   any host firewall interaction before selecting the production bus backend.

The first successful experiment should use disposable disks or writable
overlays. It must not modify a cached release image.

## Relationship to downloadable labs

This document answers whether and how several VMs can communicate on one host.
It deliberately does not define how p9qemu distributes a complete environment.
Versioned manifests, role images, orchestration, lifecycle, and lab release
qualification are described separately in
[`08-downloadable-multi-vm-labs.md`](08-downloadable-multi-vm-labs.md).

## Open questions

1. Does loopback-bound `dgram` multicast behave consistently in the supported
   Windows and Linux QEMU builds?
2. Should the production backend prefer current `dgram` syntax, legacy
   `socket,mcast`, or a small local switch selected by host profile?
3. Which loopback range minimizes conflicts with VPNs and other local tools?
4. Should management forwards use one-to-one guest ports or preserve the
   existing p9qemu host-port compatibility map?
5. How should p9qemu reserve and recover host endpoints after a crash?
6. How should stable guest interface ordering be established and validated?
7. Which first lab proves the topology without coupling the transport test to
   Plan 9 authentication complexity?
8. Should a shared user-mode network be an early alternative profile or wait
   until the independent two-adapter topology is qualified?
9. What exact evidence is required before Tailscale on 9front amd64 can be
   offered as an experimental profile?
