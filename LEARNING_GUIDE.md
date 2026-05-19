# The Complete VXLAN/EVPN Learning Guide
### From Zero to World-Class Network Engineer

> This guide is built around the lab in this repository. Every concept has a matching hands-on exercise you can run right now. Theory without a lab is trivia. Lab without theory is button-pushing. You need both.

---

## How to use this guide

Work through the phases in order. Each phase builds on the last. Don't skip to EVPN before you understand BGP — you will paper over gaps that will cost you days of debugging later.

Each section has:
- **The concept** — what it is and why it exists
- **The mental model** — how to think about it
- **Lab exercise** — commands to run in your lab right now
- **Verify** — how to confirm you actually understand it

**Time estimate:** 3–6 months of consistent daily practice to reach expert level.

---

## Phase 0 — Prerequisites (Week 1–2)

Before touching the lab, make sure these are solid.

### 0.1 The OSI model — what actually matters

Forget memorising all 7 layers. What matters for data centre networking:

| Layer | Name | What you care about |
|---|---|---|
| L2 | Data Link | MAC addresses, Ethernet frames, VLANs, STP |
| L3 | Network | IP addresses, subnets, routing |
| L4 | Transport | TCP/UDP ports (for ACLs and load balancers) |

Everything in this lab is L2 over L3 — VXLAN tunnels Ethernet frames (L2) inside UDP/IP packets (L3).

### 0.2 IP subnetting — must be instant

You should be able to subnet in your head. If not, practice until:
- `/24` → 256 hosts, `/25` → 128, `/30` → 4, `/31` → 2 (point-to-point links)
- Given `10.2.1.0/31`, you immediately know: hosts are `10.2.1.0` and `10.2.1.1`

**Why it matters in this lab:** every P2P link in the fabric is a `/31`. The super-spine↔spine links are `10.2.1.0/31`, `10.2.1.2/31`, etc. You will read these constantly.

### 0.3 The MAC address table vs the routing table

The single most important distinction in networking:

| | MAC table (L2) | Routing table (L3) |
|---|---|---|
| Key | MAC address | IP prefix |
| Populated by | Flooding + learning | Routing protocols |
| Scope | Local segment / VLAN | The whole network |
| Action | Forward to port | Forward to next-hop |

**Mental model:** A switch learns MACs by watching who sends frames. A router learns prefixes by talking to other routers.

---

## Phase 1 — Ethernet and VLANs (Week 2–3)

### 1.1 Why VLANs exist

Before VLANs, a switch was one big broadcast domain. Every ARP request went to every port. As networks grew, this became a broadcast storm problem and a security problem.

VLANs split one physical switch into multiple logical switches. Traffic in VLAN 10 is invisible to VLAN 20.

**The problem VLANs don't solve:** they only work within one physical location. You can't stretch a VLAN across a WAN link without tunnelling. That's what VXLAN fixes.

### 1.2 802.1Q tagging

A VLAN tag is 4 bytes inserted into the Ethernet header. The 12-bit VLAN ID gives you VLANs 1–4094.

```
 ┌──────────────┬───────────┬──────────────────────────────────┐
 │  Dest MAC    │  Src MAC  │ 8100 │ PCP+VLAN ID │ EtherType │ Payload │
 └──────────────┴───────────┴──────────────────────────────────┘
                              ←── 802.1Q tag (4 bytes) ───→
```

**Lab exercise:**
```bash
# See VLAN configuration on leaf-01
docker exec clab-vxlan-evpn-leaf-01 Cli -p15 -c "show vlan"
docker exec clab-vxlan-evpn-leaf-01 Cli -p15 -c "show interfaces trunk"
```

---

## Phase 2 — Routing and BGP (Week 3–6)

This is the most important phase. BGP is the routing protocol for the entire internet AND for modern data centre fabrics. Understand BGP deeply.

### 2.1 Why routing exists

Switches (L2) forward by MAC address. When a frame needs to cross a network boundary (different subnet), it needs a router — a device that makes forwarding decisions by IP address.

**The key insight:** when pc1 pings pc17 (different pod, same VRF), the Ethernet frame is addressed to pc1's default gateway (leaf-01's SVI MAC), not to pc17. Leaf-01 then routes the packet based on the IP destination. This happens at every hop.

### 2.2 BGP fundamentals

BGP (Border Gateway Protocol) is a path-vector protocol. Unlike OSPF/IS-IS which flood topology information, BGP exchanges **reachability** information: "I can reach prefix X, and here's the AS path to get there."

**Key BGP concepts:**

| Concept | What it means |
|---|---|
| AS (Autonomous System) | A network under one administrative domain, identified by a number |
| eBGP | BGP between different ASes — used at internet borders and in your fabric |
| iBGP | BGP within one AS — used for EVPN overlay sessions |
| NLRI | Network Layer Reachability Information — the prefix being advertised |
| AS-PATH | The list of ASes a route has traversed — used for loop detection |
| Next-hop | The IP address of the next router to forward to |
| RIB | Routing Information Base — the full BGP table |
| FIB | Forwarding Information Base — what actually gets installed in hardware |

**Why your fabric uses eBGP everywhere (not OSPF):**

OSPF floods link-state information to every router. In a large fabric with hundreds of nodes, this becomes a scaling problem. eBGP with a unique AS per leaf gives you:
- Simple loop prevention via AS-PATH (a leaf won't accept a route with its own AS in the path)
- No flooding — BGP is incremental
- Natural summarisation boundaries at AS edges

**Lab exercise — trace a route through the fabric:**
```bash
# What does super-spine-01 know about leaf-01's loopback?
docker exec clab-vxlan-evpn-super-spine-01 Cli -p15 -c "show ip bgp 10.0.0.11/32"

# Notice the AS-PATH: 65000 65001 (pod-1 spine → leaf-01)
# Super-spine learned it from spine-01 or spine-02

# Now check what spine-01 knows about pod-2 leaf-05's loopback:
docker exec clab-vxlan-evpn-spine-01 Cli -p15 -c "show ip bgp 10.0.0.35/32"
# AS-PATH: 65500 65010 65031 (super-spine → pod-2 spine → pod-2 leaf)
```

**Verify you understand it:** Draw the AS path on paper for a route from leaf-01 to leaf-05. Then verify with the command above.

### 2.3 BGP attributes — what controls path selection

BGP has many attributes. These are the ones that matter in a fabric:

| Attribute | Scope | Purpose in your lab |
|---|---|---|
| AS-PATH | eBGP | Loop prevention. Shortest path wins. |
| LOCAL-PREF | iBGP only | Prefer one path over another within an AS |
| MED | Between ASes | Suggest entry point to a neighbouring AS |
| NEXT-HOP | Per-route | The IP to forward packets toward |
| COMMUNITY | Both | Tag routes for policy — used for EVPN route targets |

**Lab exercise:**
```bash
# See full BGP attributes for a route
docker exec clab-vxlan-evpn-spine-01 Cli -p15 -c "show ip bgp 10.0.0.91/32 detail"
```

### 2.4 BGP ECMP — load balancing in a Clos fabric

In your fabric, spine-01 has two equal paths to reach super-spine-01's loopback: directly via Ethernet7, and via... actually only one direct path. But a leaf can reach any other leaf via two spines. BGP ECMP allows using both.

```bash
# See ECMP in action — two paths to leaf-05's loopback
docker exec clab-vxlan-evpn-spine-01 Cli -p15 -c "show ip route 10.0.0.35/32"
# Should show two entries via spine-03 and spine-04 (after going through super-spines)

# On leaf-01, two paths to leaf-05 (via spine-01 and spine-02):
docker exec clab-vxlan-evpn-leaf-01 Cli -p15 -c "show ip route 10.0.0.35/32"
```

---

## Phase 3 — The Clos Fabric (Week 5–6)

### 3.1 Why Clos topology?

Traditional three-tier (core/distribution/access) had a problem: traffic between two access switches had to go through the distribution and core layers. As east-west traffic (server-to-server) grew to dominate data centres, this became a bottleneck.

**Charles Clos (1952)** invented a multi-stage switching fabric that provides non-blocking, full-mesh connectivity without actually wiring every node to every other node.

**The key property:** in a properly sized Clos fabric, any server can talk to any other server at full line rate simultaneously — no congestion.

### 3.2 Your 5-stage topology explained

```
Stage 1 (ingress):  Leaves (tier-3)        ← where servers connect
Stage 2:            Pod spines (tier-2)    ← aggregate within pod
Stage 3 (middle):   Super-spines (tier-1)  ← connect pods
Stage 4:            Pod spines (tier-2)    ← same spines, egress direction
Stage 5 (egress):   Leaves (tier-3)        ← destination leaf
```

**The folded Clos insight:** stages 1+5 are the same physical leaves, stages 2+4 are the same physical spines. "Folded" because the ingress and egress stages fold onto each other.

**Why 5-stage and not 3-stage?**
A 3-stage Clos connects all leaves within one pod. Adding super-spines gives you a 5-stage that connects multiple pods. This is how hyperscalers (Google, Facebook, Microsoft) build fabrics with tens of thousands of servers.

### 3.3 Equal-cost paths in your fabric

From leaf-01, how many equal-cost paths are there to leaf-05?

```
leaf-01 → spine-01 → super-spine-01 → spine-03 → leaf-05
leaf-01 → spine-01 → super-spine-02 → spine-03 → leaf-05
leaf-01 → spine-01 → super-spine-01 → spine-04 → leaf-05
leaf-01 → spine-01 → super-spine-02 → spine-04 → leaf-05
leaf-01 → spine-02 → super-spine-01 → spine-03 → leaf-05
leaf-01 → spine-02 → super-spine-02 → spine-03 → leaf-05
leaf-01 → spine-02 → super-spine-01 → spine-04 → leaf-05
leaf-01 → spine-02 → super-spine-02 → spine-04 → leaf-05
```

**8 equal-cost paths.** Traffic is load-balanced across all 8 via ECMP hashing on the 5-tuple (src IP, dst IP, protocol, src port, dst port).

---

## Phase 4 — VXLAN (Week 7–9)

### 4.1 The problem VXLAN solves

VLANs are limited to 4094 IDs and don't cross IP boundaries. Hyperscalers need millions of tenant networks across thousands of servers. VXLAN solves both:

1. **Scale:** 24-bit VNI = 16 million virtual networks (vs 4094 VLANs)
2. **Transport:** encapsulates L2 frames inside UDP/IP — any IP network can carry it

### 4.2 VXLAN encapsulation

When a VTEP (leaf) needs to send a frame to a host behind a remote VTEP, it wraps the original Ethernet frame in:

```
┌─────────────────────────────────────────────────────────────────────┐
│ Outer Ethernet │ Outer IP │ Outer UDP (dst:4789) │ VXLAN header │   │
│ (spine MAC)    │src:VTEP1 │                      │ VNI: 10010   │   │
│                │dst:VTEP2 │                      │              │   │
└─────────────────────────────────────────────────────────────────────┘
  ┌──────────────────────────────────────────────────────────────────┐
  │ Inner Ethernet frame (original frame from pc1 to pc17)          │
  └──────────────────────────────────────────────────────────────────┘
```

The **VNI (VXLAN Network Identifier)** is the 24-bit field that identifies which virtual network this frame belongs to. VLAN 10 maps to VNI 10010 in your lab.

### 4.3 VTEP — the gateway between worlds

A VTEP (VXLAN Tunnel Endpoint) is the device that:
1. **Encapsulates** frames from local hosts into VXLAN tunnels
2. **Decapsulates** incoming VXLAN packets and delivers to local hosts

In your lab, every leaf is a VTEP. Its VTEP source address is its Loopback0 IP.

**Lab exercise:**
```bash
# See the VTEP configuration on leaf-01
docker exec clab-vxlan-evpn-leaf-01 Cli -p15 -c "show interface Vxlan1"

# See which remote VTEPs leaf-01 knows about
docker exec clab-vxlan-evpn-leaf-01 Cli -p15 -c "show vxlan vtep"

# See MAC addresses learned via VXLAN
docker exec clab-vxlan-evpn-leaf-01 Cli -p15 -c "show vxlan address-table"
```

### 4.4 The BUM problem — what EVPN solves

Without a control plane, a VTEP doesn't know which remote VTEP has a given MAC address. When a frame arrives for an unknown MAC, it must flood it to ALL remote VTEPs (Broadcast, Unknown unicast, Multicast = BUM traffic).

This is the same problem as a dumb switch, but now across a routed fabric. It doesn't scale.

**EVPN solves this** by distributing MAC/IP bindings via BGP before any data-plane flooding is needed. When pc1 ARPs for pc17, leaf-05 has already told leaf-01 via EVPN: "pc17's MAC is X, its IP is Y, and I'm the VTEP for it."

---

## Phase 5 — EVPN (Week 9–14)

This is the heart of modern data centre networking. Take your time here.

### 5.1 What EVPN is

EVPN (Ethernet VPN) is a BGP address family (AFI 25, SAFI 70) that carries Ethernet reachability information — MAC addresses, IP addresses, and VPN membership — the same way BGP carries IP prefixes.

Think of it this way: BGP already distributes IP reachability information across the internet. EVPN extends BGP to also distribute Ethernet (MAC) reachability and VPN topology information.

### 5.2 EVPN route types

EVPN defines 5 route types. Your lab uses 3 of them:

#### Type-2: MAC/IP Advertisement Route

Purpose: Distribute MAC address and IP address bindings across the fabric.

```
Key: [2]:[EthTag]:[MAC length]:[MAC]:[IP length]:[IP]
Example: [2]:[0]:[48]:[aa:bb:cc:dd:ee:ff]:[32]:[10.10.10.101]

Attributes:
  - Next-hop: VTEP IP of the advertising leaf (e.g. 10.0.0.11 for leaf-01)
  - VNI: L2 VNI (e.g. 10010 for VLAN 10) in PMSI
  - Route Target: identifies which VRF/VLAN this belongs to
```

**What it does:** When pc1 connects to leaf-01, leaf-01 learns pc1's MAC+IP and advertises a Type-2 to its pod-spine. The pod-spine reflects it to super-spines. Super-spines reflect it to pod-2 spines. Pod-2 spines push it to pod-2 leaves. Now leaf-05 knows pc1's MAC without any flooding.

**Lab exercise:**
```bash
# See Type-2 routes on leaf-01
docker exec clab-vxlan-evpn-leaf-01 Cli -p15 -c "show bgp evpn route-type mac-ip"

# Pick a specific MAC and see its full attributes
docker exec clab-vxlan-evpn-leaf-01 Cli -p15 -c "show bgp evpn route-type mac-ip detail" | head -80
```

#### Type-3: Inclusive Multicast Ethernet Tag (IMET)

Purpose: Build the BUM replication list — which VTEPs participate in a given L2 VNI.

When leaf-01 starts with VNI 10010, it advertises a Type-3 saying "I am a VTEP for VNI 10010." Every other leaf receiving this Type-3 adds leaf-01 to its BUM replication list for that VNI.

```bash
# See Type-3 routes — one per VTEP per VNI
docker exec clab-vxlan-evpn-leaf-01 Cli -p15 -c "show bgp evpn route-type imet"
```

#### Type-5: IP Prefix Route

Purpose: Distribute routed IP prefixes across VRFs — used for internet access and inter-DC routing.

This is how internet routes from frr-inet reach TENANT1 hosts in pod-2:
1. frr-inet advertises internet prefixes to isp-01/02
2. ISPs advertise to border-leaf-01/02 via eBGP in VRF TENANT1
3. Border leaves generate Type-5 EVPN routes for those prefixes
4. Type-5 propagates via spines and super-spines to pod-2 leaves
5. Pod-2 leaf-05 installs the route in its TENANT1 VRF, pointing to the border-leaf VTEP

```bash
# See Type-5 routes (internet prefixes in TENANT1)
docker exec clab-vxlan-evpn-leaf-01 Cli -p15 -c "show bgp evpn route-type ip-prefix"

# See internet routes in TENANT1 RIB
docker exec clab-vxlan-evpn-leaf-01 Cli -p15 -c "show ip route vrf TENANT1"
```

### 5.3 Route Targets — the policy language of EVPN

A Route Target (RT) is a BGP extended community (8 bytes) that acts as a tag: "this route belongs to tenant X."

**Import/Export model:**
- When a VTEP **exports** a route, it stamps it with its RT: `65001:10010`
- When a VTEP **imports** routes, it only accepts routes matching its configured import RT
- This is what keeps TENANT1 and TENANT2 isolated — they have different RTs

```bash
# See route target configuration on leaf-01
docker exec clab-vxlan-evpn-leaf-01 Cli -p15 -c "show running-config section router bgp"
# Look for: route-target import/export lines under each address-family
```

### 5.4 Symmetric vs Asymmetric IRB

**Asymmetric IRB** (simpler, doesn't scale):
- The ingress VTEP does both L2 and L3 forwarding
- Every VTEP must have every VLAN — doesn't scale to thousands of VLANs

**Symmetric IRB** (what your lab uses):
- The ingress VTEP routes from the source VLAN into the L3 VNI (VRF tunnel)
- The packet travels across the fabric as an L3 packet in the L3 VNI
- The egress VTEP routes from the L3 VNI into the destination VLAN

**L3 VNI:** A special VNI assigned to a VRF (not a VLAN). In your lab:
- TENANT1 → L3 VNI 10000
- TENANT2 → L3 VNI 20000
- TENANT3 → L3 VNI 30000

**Mental model:** Think of the L3 VNI as a "virtual wire" connecting all VTEPs that participate in a given VRF. Routed traffic between leaves travels through this wire.

```bash
# Trace symmetric IRB in action
# 1. Check the L3 VNI on leaf-01
docker exec clab-vxlan-evpn-leaf-01 Cli -p15 -c "show running-config interfaces Vxlan1"

# 2. Ping from pc1 (pod-1) to pc17 (pod-2), same VRF
docker exec clab-vxlan-evpn-pc1 ping -c 3 10.10.10.105

# 3. See the route that makes it work on leaf-01
docker exec clab-vxlan-evpn-leaf-01 Cli -p15 -c "show ip route vrf TENANT1 10.10.10.105/32"
# Should show: via VTEP 10.0.0.35 (leaf-05), VNI 10000 (L3 VNI)
```

### 5.5 ARP suppression

Without ARP suppression, every ARP request from a host would be flooded to all VTEPs for that VNI. In a large fabric with thousands of VMs this is a lot of traffic.

With EVPN, VTEPs learn IP-to-MAC bindings from Type-2 routes and can **answer ARP requests locally** without flooding.

```bash
# See the ARP suppression cache on leaf-01
docker exec clab-vxlan-evpn-leaf-01 Cli -p15 -c "show vxlan arp-cache"
```

### 5.6 The hierarchical EVPN model — your super-spines

In a single-pod fabric, spines are the EVPN route reflectors (RRs). Every leaf peers EVPN with its pod-spines. The spines reflect routes between leaves.

Your lab adds a second tier: super-spines act as RRs for the inter-pod sessions. The pod-spines peer EVPN with super-spines. Super-spines reflect pod-1 routes to pod-2 spines and vice versa.

**The `next-hop-unchanged` rule:** when super-spines reflect EVPN routes between pods, they preserve the original VTEP next-hop. Without this, pod-2 leaves would try to VXLAN encapsulate to the super-spine's IP — wrong. With it, they encapsulate directly to the originating leaf's VTEP IP.

```bash
# See EVPN sessions on super-spine-01
docker exec clab-vxlan-evpn-super-spine-01 Cli -p15 -c "show bgp evpn summary"

# Verify next-hop-unchanged: a Type-2 from leaf-01 (10.0.0.11) arriving at leaf-05
# should still show next-hop 10.0.0.11, NOT super-spine's IP
docker exec clab-vxlan-evpn-leaf-05 Cli -p15 -c "show bgp evpn route-type mac-ip detail" | grep -A5 "10.0.0.11"
```

---

## Phase 6 — Multi-tenancy (Week 13–15)

### 6.1 VRF — Virtual Routing and Forwarding

A VRF is a separate routing table within a single router. Traffic in VRF TENANT1 can never reach VRF TENANT2 unless explicitly configured — they are completely isolated RIBs.

**Mental model:** Think of a VRF as a router within a router. Each VRF has its own:
- Routing table
- Forwarding table
- BGP table
- ARP table
- Interface assignments

### 6.2 Tenant isolation in your lab

```
TENANT1: VLAN10 (10.10.10.0/24) + VLAN20 (10.20.20.0/24) + internet access
TENANT2: VLAN30 (172.16.30.0/24)                          + no internet
TENANT3: VLAN50 (172.16.50.0/24)                          + no internet
```

**Lab exercise — verify isolation:**
```bash
# This SHOULD work (same tenant, different pods)
docker exec clab-vxlan-evpn-pc1 ping -c 2 10.10.10.105    # pc1 → pc17, TENANT1

# This SHOULD FAIL (different tenants — TENANT1 → TENANT2)
docker exec clab-vxlan-evpn-pc1 ping -c 2 172.16.30.101   # pc1 → pc9

# Confirm why: no route exists
docker exec clab-vxlan-evpn-leaf-01 Cli -p15 -c "show ip route vrf TENANT1 172.16.30.0/24"
```

### 6.3 Internet access — EVPN Type-5 end-to-end

This is the most complex flow in your lab. Trace it fully:

```
frr-inet → originates ~500 prefixes (8.8.8.8/32, 1.1.1.1/32, etc.)
         → advertises to isp-01 and isp-02 via eBGP
         
isp-01/02 → advertises 0.0.0.0/0 + specific prefixes to border-leaf-01/02
           → this happens in VRF TENANT1 on the border leaves

border-leaf-01/02 → redistributes the learned BGP routes into EVPN Type-5
                  → Type-5: "0.0.0.0/0 is reachable via my VTEP (10.0.0.21)"
                  
Type-5 propagates: border-leaf → spine-01/02 → super-spine-01/02 → spine-03/04 → leaf-05..08

leaf-05 → installs "0.0.0.0/0 via VTEP 10.0.0.21, L3 VNI 10000" in TENANT1 VRF

pc17 → pings 8.8.8.8
     → leaf-05 routes: not local → default route → encap VXLAN to border-leaf-01
     → border-leaf-01 → routes to isp-01 → to frr-inet → returned
```

**Lab exercise:**
```bash
# Verify internet from pod-2 (the harder path — must cross super-spines)
docker exec clab-vxlan-evpn-pc17 ping -c 3 8.8.8.8

# Trace the path
docker exec clab-vxlan-evpn-pc17 traceroute -n 8.8.8.8

# See the default route on leaf-05 in TENANT1
docker exec clab-vxlan-evpn-leaf-05 Cli -p15 -c "show ip route vrf TENANT1 0.0.0.0/0"
```

---

## Phase 7 — Troubleshooting Methodology (Week 15–17)

A world-class engineer's value is not knowing the answer immediately — it's **finding the answer systematically**.

### 7.1 The divide-and-conquer method

When something doesn't work, divide the problem in half:

```
Problem: pc1 can't ping pc17

Step 1: Can leaf-01 ping leaf-05 (underlay)?
  YES → underlay is fine, problem is in VXLAN/EVPN overlay
  NO  → underlay is broken, check BGP, interfaces, P2P links

Step 2 (if underlay OK): Does leaf-01 have a VXLAN route to pc17's subnet?
  YES → problem is in VXLAN data plane
  NO  → problem is in EVPN control plane (missing Type-2 or Type-5)

Step 3 (if EVPN OK): Can you VXLAN ping the far-end VTEP?
  YES → problem is at the host or the last-hop leaf
  NO  → VXLAN encap/decap issue
```

### 7.2 The OSI layer troubleshooting ladder

**Always start at L1/L2 and work up.**

```bash
# L1/L2 — is the interface up?
docker exec clab-vxlan-evpn-spine-01 Cli -p15 -c "show interfaces status"

# L3 — do I have an IP and is the route there?
docker exec clab-vxlan-evpn-spine-01 Cli -p15 -c "show ip interface brief"
docker exec clab-vxlan-evpn-spine-01 Cli -p15 -c "show ip route"

# BGP — are sessions established?
docker exec clab-vxlan-evpn-spine-01 Cli -p15 -c "show ip bgp summary"

# EVPN — are overlay sessions up and are routes present?
docker exec clab-vxlan-evpn-spine-01 Cli -p15 -c "show bgp evpn summary"
docker exec clab-vxlan-evpn-spine-01 Cli -p15 -c "show bgp evpn"

# VXLAN — are VTEPs known? Is the address table populated?
docker exec clab-vxlan-evpn-leaf-01 Cli -p15 -c "show vxlan vtep"
docker exec clab-vxlan-evpn-leaf-01 Cli -p15 -c "show vxlan address-table"

# Config sanity — does cEOS flag any known misconfigurations?
docker exec clab-vxlan-evpn-leaf-01 Cli -p15 -c "show vxlan config-sanity"
```

### 7.3 Packet capture — seeing what actually happens

Use your lab's packet capture page (`/cap`) or do it manually:

```bash
# Capture on leaf-01's uplink to spine-01 — see VXLAN-encapsulated traffic
# From the portal: http://192.168.236.139:5000/cap
# Or via CLI:
nsenter -t $(docker inspect -f '{{.State.Pid}}' clab-vxlan-evpn-leaf-01) \
  -n tcpdump -i eth1 -nn udp port 4789 -c 20 -w /tmp/vxlan.pcap

# Capture ARP to see if suppression is working
nsenter -t $(docker inspect -f '{{.State.Pid}}' clab-vxlan-evpn-leaf-01) \
  -n tcpdump -i eth3 arp -c 10
```

### 7.4 Classic failure scenarios — practise breaking things

**Exercise 1: Kill a spine, watch reconvergence**
```bash
# Stop spine-01
docker stop clab-vxlan-evpn-spine-01

# Watch BGP reconverge on leaf-01 (should lose 4 sessions, re-route via spine-02)
docker exec clab-vxlan-evpn-leaf-01 Cli -p15 -c "show ip bgp summary"

# Does traffic still work? (it should — ECMP re-routes)
docker exec clab-vxlan-evpn-pc1 ping -c 5 10.10.10.105

# Restore
docker exec ... # use portal /ctrl page or containerlab
```

**Exercise 2: Kill a super-spine, watch inter-pod convergence**
```bash
# Stop super-spine-01
docker stop clab-vxlan-evpn-super-spine-01

# Inter-pod traffic should still work via super-spine-02
docker exec clab-vxlan-evpn-pc1 ping -c 5 10.10.10.105

# How long did reconvergence take?
# (Measure by timing how many pings were lost)
```

**Exercise 3: Trace a BGP flap**
```bash
# Watch BGP events in real time on spine-01
docker exec clab-vxlan-evpn-spine-01 Cli -p15 -c "show bgp evpn summary"
# Then kill a leaf and watch the count change
```

---

## Phase 8 — Advanced Topics (Month 3–4)

### 8.1 ECMP hashing — why some flows are slower

ECMP distributes flows across multiple paths by hashing the 5-tuple. The hash is deterministic — the same flow always takes the same path.

**The polarisation problem:** if all your flows have the same source/destination port (e.g. all NFS traffic on port 2049), they all hash to the same path. One link is saturated, others are empty.

**Solutions:**
- **UDP source port entropy** (your lab uses this) — VXLAN outer UDP source port is hashed from inner 5-tuple, adding randomness
- **ECMP with consistent hashing** — used in modern fabric ASICs

### 8.2 BGP timer tuning — convergence speed

Default BGP hold timer is 90 seconds with keepalive every 30s. A dead peer isn't detected for 90 seconds — unacceptably slow for a data centre.

Modern fabrics use:
- **BFD (Bidirectional Forwarding Detection):** sub-second failure detection (300ms typical)
- **Aggressive BGP timers:** keepalive 3s, hold 9s
- **BGP fast-external-fallover:** immediately withdraw routes when a BGP peer's interface goes down

```bash
# See what timers your fabric uses
docker exec clab-vxlan-evpn-spine-01 Cli -p15 -c "show running-config section bgp" | grep -i timer
```

### 8.3 Route reflectors — why they're needed at scale

In iBGP, every peer must connect to every other peer (full mesh). With N routers, that's N(N-1)/2 sessions. For 100 routers: 4950 sessions. Unmanageable.

**Route reflectors** break the full-mesh requirement: all iBGP peers connect to the RR, and the RR reflects routes between them. This is exactly what your pod-spines and super-spines do for EVPN sessions.

```bash
# See RR configuration on spine-01
docker exec clab-vxlan-evpn-spine-01 Cli -p15 -c "show running-config section bgp" | grep -i "route-reflector\|cluster"
```

### 8.4 VXLAN multi-homing (EVPN Type-1 and Type-4)

Your current lab has single-homed hosts (each PC connects to one leaf). In production, servers connect to two leaves for redundancy (active/active multi-homing).

EVPN handles this with:
- **Type-1 (Ethernet Auto-Discovery):** advertise the Ethernet Segment (ES) — the pair of leaves
- **Type-4 (Ethernet Segment Route):** elect a Designated Forwarder (DF) for BUM traffic

This prevents duplicate frames when both leaves forward at the same time.

### 8.5 MPLS and Segment Routing — what comes after VXLAN

VXLAN is UDP-based and relies on IP routing for transport. The next evolution:

- **MPLS:** uses label stacks instead of IP encapsulation. Still used heavily in service provider networks.
- **Segment Routing (SR-MPLS):** simplifies MPLS by encoding the path in the packet header. No per-flow state in the network.
- **SRv6:** Segment Routing over IPv6 — uses IPv6 extension headers. The current frontier for hyper-scale networks.

These are phase 2 of your learning journey, after mastering this lab.

---

## Phase 9 — Becoming World-Class (Month 4–6+)

### 9.1 Build, break, fix — the only real curriculum

The engineers who become truly exceptional all have one thing in common: they break things intentionally, diagnose them systematically, and fix them precisely. Run every exercise in this guide at least 3 times:
1. First time: follow the instructions
2. Second time: predict what you'll see before running each command
3. Third time: break something first, then use the diagnostic commands to find and fix it without looking at the guide

### 9.2 Structured exercises to run in this lab

**Week 1 of practice:**
- [ ] Draw the full topology from memory, with IP addresses and AS numbers
- [ ] Verify every BGP session: underlay and EVPN, all nodes
- [ ] Trace a packet from pc1 to pc17 step by step, then capture it and verify

**Week 2:**
- [ ] Kill spine-01, measure convergence, restore, verify
- [ ] Kill super-spine-02, verify inter-pod traffic, measure reconvergence
- [ ] Kill border-leaf-01, verify internet still works via border-leaf-02

**Week 3:**
- [ ] Find all Type-2 routes for pc1 on every spine and super-spine
- [ ] Explain why the next-hop is different on pod-1 spines vs pod-2 spines
- [ ] Verify ARP suppression is working (no ARP floods crossing the fabric)

**Week 4:**
- [ ] Add a static route on leaf-01 in TENANT1 and trace how it propagates as Type-5
- [ ] Explain the difference between a Type-2 and a Type-5 route
- [ ] Write down the full import/export RT policy for TENANT2 from memory

### 9.3 Read these — no shortcuts

| Book | Why |
|---|---|
| **Computer Networks** — Tanenbaum | The foundation. Read chapters 4 and 5 twice. |
| **TCP/IP Illustrated Vol. 1** — Stevens | The definitive deep-dive into protocols. Own it. |
| **BGP in the Data Centre** — Dinesh Dutt (free PDF) | Exactly what your lab implements. Read it alongside this guide. |
| **EVPN in the Data Centre** — Dinesh Dutt | Part 2 of the above. The theory behind everything EVPN. |
| **RFC 7432** — BGP MPLS-Based Ethernet VPN | The EVPN specification. Read it once you understand the concepts. |
| **RFC 8365** — EVPN for VXLAN | How EVPN maps to VXLAN. Short and essential. |

### 9.4 Certifications — what actually matters

Certifications prove you can study. Experience proves you can do the work. Get both.

| Cert | Level | Worth it? |
|---|---|---|
| Cisco CCNA | Foundation | Yes — forces you to learn fundamentals properly |
| Arista ACE-A / ACE-L2 | Associate / Professional | Yes — directly relevant to this lab |
| Cisco CCNP ENCOR | Advanced | Yes — comprehensive routing and switching |
| Cisco CCIE (Data Centre or SP) | Expert | The gold standard. Gruelling. Worth it if you want to be the best. |
| Juniper JNCIE-DC | Expert | Equivalent to CCIE for Juniper shops |

**The honest truth:** A CCIE who has never built a real fabric is less valuable than someone without any certs who has operated one at scale. The cert gets you in the door. Your lab work is what you talk about in the interview.

### 9.5 What separates good from world-class

**Good engineers** know the commands. They can configure a feature if you tell them what to configure.

**Great engineers** understand why. They can look at a broken network and know which layer to check first, which output tells them what, and which change will fix it without breaking anything else.

**World-class engineers** can design from first principles. They can take a set of requirements ("10,000 servers, 3 tenants, sub-100ms failover, dual-ISP internet") and design the fabric, choose the protocols, write the configuration templates, and predict the failure modes — before touching a single device.

To get there:
1. **Build something and explain it to someone else.** If you can't explain it simply, you don't understand it.
2. **Read vendor blogs and RFCs.** The Arista EOS Central blog, the IETF BESS working group drafts — this is where the industry is going.
3. **Contribute.** Write about what you've built. File GitHub issues. Help others debug. Teaching is the fastest way to find the gaps in your own knowledge.
4. **Work at scale.** A lab teaches you protocols. Operating a network with real traffic, real failures, and real consequences teaches you engineering judgement. Seek environments where things break.

---

## Quick reference — commands you will run every day

```bash
# ── Underlay health ──────────────────────────────────────────────────
show ip interface brief                    # Are my interfaces up with IPs?
show ip bgp summary                        # Are underlay BGP peers up?
show ip route                              # Is the routing table correct?
show ip route 10.0.0.X/32                 # Can I reach that loopback?

# ── EVPN overlay health ──────────────────────────────────────────────
show bgp evpn summary                      # Are EVPN peers up?
show bgp evpn                              # Full EVPN table
show bgp evpn route-type mac-ip            # Type-2 routes
show bgp evpn route-type imet              # Type-3 routes (BUM lists)
show bgp evpn route-type ip-prefix         # Type-5 routes (internet/L3)

# ── VXLAN data plane ─────────────────────────────────────────────────
show vxlan vtep                            # Remote VTEPs
show vxlan address-table                   # MAC-to-VTEP mappings
show vxlan arp-cache                       # ARP suppression cache
show vxlan config-sanity                   # Common misconfiguration check
show interface Vxlan1                      # VTEP interface state

# ── VRF / tenant ─────────────────────────────────────────────────────
show ip route vrf TENANT1                  # Tenant routing table
show vrf TENANT1                           # VRF state and RD
show ip arp vrf TENANT1                    # Tenant ARP table

# ── Config audit ─────────────────────────────────────────────────────
show running-config interfaces Vxlan1      # VNI config — check for conflicts
show running-config section bgp            # Full BGP config
show running-config section router bgp    # Confirm EOS actually accepted it
```

---

## Glossary

| Term | Definition |
|---|---|
| **ARP suppression** | VTEP answers ARP requests locally using EVPN MAC/IP bindings, preventing fabric-wide flooding |
| **AS** | Autonomous System — a network identified by a number, administrated as a unit |
| **BFD** | Bidirectional Forwarding Detection — sub-second link failure detection |
| **BGP** | Border Gateway Protocol — the routing protocol for the internet and modern data centre fabrics |
| **BUM** | Broadcast, Unknown unicast, Multicast — traffic that must be flooded when the destination is unknown |
| **Clos** | A non-blocking multi-stage switching topology invented by Charles Clos in 1952 |
| **DF** | Designated Forwarder — the elected leaf that forwards BUM traffic for a multi-homed segment |
| **ECMP** | Equal-Cost Multi-Path — load balancing across multiple equal-cost routes |
| **EVPN** | Ethernet VPN — a BGP address family that carries Ethernet reachability information |
| **FIB** | Forwarding Information Base — the table used by hardware to forward packets |
| **IMET** | Inclusive Multicast Ethernet Tag — EVPN Type-3 route, builds BUM replication lists |
| **IRB** | Integrated Routing and Bridging — routing between VLANs at a VTEP |
| **L3 VNI** | A VNI assigned to a VRF for routed VXLAN traffic between VTEPs (symmetric IRB) |
| **NLRI** | Network Layer Reachability Information — the prefix in a BGP update |
| **RD** | Route Distinguisher — makes routes from different VRFs globally unique in BGP |
| **RIB** | Routing Information Base — the full routing table |
| **RR** | Route Reflector — a BGP peer that reflects routes between iBGP clients (eliminates full mesh) |
| **RT** | Route Target — BGP extended community used to control EVPN route import/export policy |
| **Symmetric IRB** | Both ingress and egress VTEPs route; traffic crosses the fabric in the L3 VNI |
| **VNI** | VXLAN Network Identifier — 24-bit field identifying a virtual network (like a VLAN, but 16M scale) |
| **VRF** | Virtual Routing and Forwarding — a separate routing table within a router |
| **VTEP** | VXLAN Tunnel Endpoint — the device that encapsulates/decapsulates VXLAN (your leaves) |
| **VXLAN** | Virtual Extensible LAN — encapsulates L2 Ethernet frames in UDP/IP for transport over L3 networks |
