# Lessons Learned — VXLAN/EVPN Containerlab Build

**From: building a 51-node multi-tenant VXLAN/EVPN lab on cEOS-lab 4.36.0.1F via Containerlab.**

The lab works end-to-end: 2 super-spines + 4 spines + 8 leaves + 2 border-leaves + dual ISP + FRR internet + 32 hosts across 3 VRFs, with internet propagation via EVPN Type-5. These are the non-obvious things that ate hours along the way.

---

## 🚨 cEOS gotcha #1 — same VNI can't be both VLAN-mapped and VRF-mapped

The longest-burning trap. cEOS silently rejects the second of these two without printing an error:

```text
interface Vxlan1
   vxlan vlan 4001 vni 10000      ← L3-VNI treated as a normal L2 VNI
   vxlan vrf TENANT1 vni 10000    ← ALSO mapping to VRF — silently dropped
```

When that happens, `show bgp evpn` never produces any Type-5 routes (only Type-2 + Type-3) and you wonder why internet doesn't propagate from border leaves.

**Correct pattern for symmetric IRB:**

```text
interface Vxlan1
   vxlan source-interface Loopback0
   vxlan udp-port 4789
   vxlan vlan 10 vni 10010    ! L2 VNIs only
   vxlan vlan 20 vni 10020
   vxlan vlan 30 vni 10030
   vxlan vlan 50 vni 10050
   vxlan vrf TENANT1 vni 10000  ! L3 VNI — NO matching vxlan vlan line
   vxlan vrf TENANT2 vni 20000
   vxlan vrf TENANT3 vni 30000
```

**How to detect it:**
```
show running-config interfaces Vxlan1
show vxlan config-sanity
```
If you see `vxlan vlan X vni Y` AND `vxlan vrf Z vni Y` for the same Y — the vrf line was dropped. Remove the vlan line.

---

## 🚨 cEOS gotcha #2 — `redistribute connected` alone in VRF BGP is silently ignored

```text
router bgp 65021
   vrf TENANT1
      address-family ipv4
         redistribute connected   ! accepted by parser, Type-5 never appears
```

**Correct — must include a route-map:**

```text
route-map PERMIT_ALL permit 10
!
router bgp 65021
   vrf TENANT1
      address-family ipv4
         redistribute connected route-map PERMIT_ALL
         redistribute static    route-map PERMIT_ALL
```

The route-map can be empty (just `permit 10` with no match clauses) — but it MUST be present. Without it, EOS silently ignores the redistribution.

---

## 🚨 Containerlab — never use `docker start` / `docker restart` on individual containers

`docker restart` brings the container back but the veth pairs connecting it to its neighbours are **not recreated**. BGP peers will show `Idle(NoIf)` because the Ethernet interfaces simply don't exist.

```bash
# ✅ Always use containerlab
containerlab deploy --topo topology.clab.yml

# ❌ These lose the veth wiring
docker restart clab-vxlan-evpn-spine-01
docker start   clab-vxlan-evpn-spine-01
```

**How to detect it:**
```bash
docker exec clab-vxlan-evpn-super-spine-01 Cli -p15 -c "show interfaces status"
# Only Management0 shows — all Ethernet interfaces are missing
```

---

## 🧠 cEOS silent-rejection cheat-sheet

| You typed | EOS quietly did |
|---|---|
| `redistribute connected` (alone in VRF AF) | accepted by parser, ignored at runtime |
| `vxlan vrf X vni Y` (when Y already on `vxlan vlan`) | silently dropped |
| `address-family ipv4 unicast` | rejected on 4.36 (`unicast` keyword not allowed) |
| `address-family ipv4` (no unicast) | accepted ✅ |
| `network 0.0.0.0/0` under vrf AF | sometimes silently dropped |

**Defensive practice:** after pushing config always run:
```
show running-config section bgp
show running-config interfaces Vxlan1
show vxlan config-sanity
```
Diff what EOS stored against what you wrote.

---

## 🏗️ Architectural principles confirmed

- **Spines NEVER have VRFs.** They are pure underlay + EVPN reflectors. Tenant identity lives in VNIs (32-bit field in the VXLAN header). Only VTEPs (leaves and border-leaves) carry VRFs.
- **Super-spines use `next-hop-unchanged`** on EVPN sessions toward pod-spines so the VTEP next-hop is preserved end-to-end and the VXLAN data plane bypasses them.
- **L3 VNI is per-VRF**, used for inter-VTEP routing of routed tenant traffic.
- **Anycast gateway** — same SVI IP + MAC (`ip virtual-router mac-address`) on every leaf hosting a VLAN. Hosts always default-route to a gateway that follows them.
- **Cross-VRF isolation is automatic** — each VRF has its own RIB/FIB and L3 VNI. Overlapping IP space across tenants is by design.

---

## 🐛 Other things that bit us

### Containerlab conflicting deploy processes

Running two `containerlab deploy` commands simultaneously (e.g. from two terminal sessions or background jobs) causes only a partial set of containers to start. Always check `ps aux | grep containerlab` before deploying, and kill any stale processes first.

### Ubuntu 26.04 — PEP 668 blocks global pip

Ubuntu 26.04 marks the system Python as externally managed. `pip install flask` fails.

```bash
# ✅ Always use a venv
python3 -m venv venv
venv/bin/pip install flask flask-sock pyyaml
venv/bin/python app.py
```

---

## 🧰 Diagnostic commands worth memorising

```bash
# === On the Ubuntu lab host ===

# Full lab status
containerlab inspect --all

# Quick health
docker ps --format '{{.Names}}\t{{.Status}}' | grep clab-
docker stats --no-stream --format '{{.Name}}\t{{.MemUsage}}' | grep clab-

# === Inside any cEOS node ===

# Underlay BGP
show ip bgp summary
show ip interface brief

# EVPN overlay
show bgp evpn summary
show bgp evpn
show bgp evpn route-type ip-prefix   # Type-5 — internet routes

# VXLAN data plane
show vxlan vtep
show vxlan address-table
show vxlan config-sanity             # catches VNI conflicts

# Tenant view
show ip route vrf TENANT1
show vrf TENANT1

# Catch silent config drops
show running-config interfaces Vxlan1
show running-config section bgp
```

---

## 🪜 If you rebuild this lab from scratch

1. **Image:** cEOS-lab 4.36.0.1F or newer. Import: `docker import cEOS64-lab-4.36.0.1F.tar ceos:4.36.0.1F`
2. **Topology in containerlab** — `kind: arista_ceos` for routers, `kind: linux` + `nicolaka/netshoot` for hosts, `kind: linux` + `quay.io/frrouting/frr` for FRR.
3. **Per-leaf config** — L2 VNIs only under `interface Vxlan1`. L3 VNI only via `vxlan vrf X vni Y`. SVIs for L3 transit VLAN with `no autostate`.
4. **BGP vrf** — always `redistribute connected route-map PERMIT_ALL` (empty permit-all map).
5. **Deploy** → wait 3 min → `show bgp evpn summary` on a spine → if all Estab, fabric is up.
6. **Verify inter-pod** with `docker exec clab-vxlan-evpn-pc1 ping 10.10.10.105` (pc17's IP).

Full rebuild takes under 15 minutes once the topology and configs are correct.
