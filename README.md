# VXLAN-EVPN Lab on Containerlab

A production-realistic multi-tenant VXLAN/EVPN 5-stage Clos fabric built with [Containerlab](https://containerlab.dev/) and Arista cEOS-lab, complete with a browser-based management portal.

---

## What you get

A **5-stage Clos** fabric — two full pods joined by a shared super-spine tier, with redundant dual-ISP internet via border leaves in pod-1.

```
                        frr-inet (AS 65999) — ~500 internet prefixes
                               │
                  isp-01 (AS 65100)    isp-02 (AS 65200)
                               │               │
                        border-leaf-01    border-leaf-02        (pod-1)
                        AS 65021          AS 65022
                               │
              ── SUPER-SPINES (AS 65500, Tier-1 EVPN RR) ──
                          ss-01 (10.0.0.91)   ss-02 (10.0.0.92)
                           ╱   ╲               ╱   ╲
                          ╱     ──────────────     ╲
                    spine-01,02 (AS 65000)     spine-03,04 (AS 65010)
                    Pod-1 Tier-2              Pod-2 Tier-2
                       │ │ │ │                  │ │ │ │
                    leaf-01..04               leaf-05..08
                    AS 65001-65004            AS 65031-65034
                       │                         │
                  16 hosts (pod-1)           16 hosts (pod-2)
                   pc1 – pc16                pc17 – pc32
```

EVPN runs **hierarchically**: leaves peer with pod-spines, pod-spines peer with super-spines. Super-spines reflect EVPN routes between pods with `next-hop-unchanged`, so VXLAN data-plane forwarding goes leaf → pod-spine → super-spine → pod-spine → leaf in 5 hops.

**Specs:**

| Item | Count |
|---|---|
| Arista cEOS routers | **17** (2 super-spines + 4 spines + 8 leaves + 2 border-leaves + 2 ISPs + 1 FRR) |
| Linux host containers | **32** (`netshoot`, 4 per leaf × 8 leaves) |
| Pods | 2 (each: 2 spines + 4 leaves + 16 hosts) |
| VRFs | 3 (TENANT1 / TENANT2 / TENANT3) |
| L2 VNIs | 4 (10010 / 10020 / 10030 / 10050) |
| L3 VNIs | 3 (10000 / 20000 / 30000) |
| BGP sessions per super-spine | 8 (4 underlay + 4 EVPN, two pods × 2 spines) |
| Total containers | **51** |
| Total RAM used | ~15 GB |

**Verified behaviour:**

| Test | Expected | Result |
|---|---|---|
| Intra-pod L2 (`pc1 → pc3`, pod-1) | works | ✅ |
| Inter-pod L2 over EVPN (`pc1 → pc17`, pod-1 ↔ pod-2) | works via super-spines | ✅ |
| Inter-tenant isolation (`pc1 → pc19`, T1 → T2) | blocked | ✅ |
| Internet from TENANT1 in pod-1 (`pc1 → 8.8.8.8`) | works via border leaves | ✅ |
| Internet from TENANT1 in pod-2 (`pc17 → 8.8.8.8`) | works via SS → border leaves | ✅ |
| Internet from TENANT2 / TENANT3 | blocked (by design) | ✅ |

📐 **[docs/NETWORK_DIAGRAM.md](docs/NETWORK_DIAGRAM.md)** — full Mermaid topology diagram, logical EVPN session topology, and a `pc1 → pc17` data-plane trace.

---

## Repository layout

```
.
├── README.md                    ← this file
├── LESSONS_LEARNED.md           ← cEOS / EVPN traps that ate hours (READ THIS)
├── RUNBOOK.md                   ← daily start / stop / verify procedures
├── topology/
│   └── topology.clab.yml        ← Containerlab topology definition
├── docs/
│   └── NETWORK_DIAGRAM.md       ← Mermaid diagrams + data-plane trace
└── lab-portal/                  ← browser-based management portal
    ├── app.py                   ← Flask + WebSocket backend (runs locally on lab host)
    └── templates/
        ├── base.html            ← dark glassmorphism nav + CSS variables
        ├── index.html           ← Mermaid topology + live link states + host PCs
        ├── term.html            ← in-browser xterm.js terminal via WebSocket
        ├── lg.html              ← looking glass (run show commands on any router)
        ├── cap.html             ← packet capture via nsenter + tcpdump → .pcap
        ├── ctrl.html            ← stop / start / restart any container
        ├── peering.html         ← BGP underlay + EVPN overlay session view
        └── tenants.html         ← per-tenant VRF state across all leaves
```

---

## Quickstart (Ubuntu 22.04/24.04/26.04 with Docker + Containerlab)

### 1. Prerequisites

```bash
# Docker
curl -fsSL https://get.docker.com | sudo bash

# Containerlab
bash -c "$(curl -sL https://get.containerlab.dev)"

# Arista cEOS-lab image — register free at arista.com → Downloads → cEOS-lab
# Download cEOS64-lab-4.36.0.1F.tar.xz (~600 MB)
xz -d cEOS64-lab-4.36.0.1F.tar.xz
docker import cEOS64-lab-4.36.0.1F.tar ceos:4.36.0.1F
```

### 2. Deploy the lab

```bash
git clone https://github.com/Harinischal/VXLAN-EVPN-Lab.git
cd VXLAN-EVPN-Lab/topology
containerlab deploy --topo topology.clab.yml
# Wait ~3 min for all 51 containers to start and BGP/EVPN to converge.
```

### 3. Verify

```bash
# 51 containers running?
docker ps --format '{{.Names}}' | grep -c clab-vxlan-evpn-

# All BGP up on spine-01?
docker exec clab-vxlan-evpn-spine-01 Cli -p15 -c "show ip bgp summary"
docker exec clab-vxlan-evpn-spine-01 Cli -p15 -c "show bgp evpn summary"

# Super-spine connectivity?
docker exec clab-vxlan-evpn-super-spine-01 Cli -p15 -c "show ip bgp summary"

# Intra-pod ping (TENANT1):
docker exec clab-vxlan-evpn-pc1 ping -c 2 10.10.10.102

# Inter-pod ping across super-spines (TENANT1):
docker exec clab-vxlan-evpn-pc1 ping -c 2 10.10.10.105
```

### 4. Launch the portal

```bash
cd ../lab-portal
python3 -m venv venv
venv/bin/pip install flask flask-sock pyyaml
venv/bin/python app.py
```

Browse to **http://\<host-ip\>:5000** — click any device on the topology to open an in-browser terminal.

---

## Portal features

| Page | URL | Description |
|---|---|---|
| Topology | `/` | Mermaid.js 5-stage Clos diagram · live link colours · click any device |
| Terminal | `/term/<node>` | In-browser xterm.js terminal via WebSocket (auto-detects cEOS/FRR/Linux) |
| Looking Glass | `/lg` | Run `show` commands on any router, output in-browser |
| Packet Capture | `/cap` | `tcpdump` via `nsenter` into container netns · download `.pcap` |
| Lab Control | `/ctrl` | Stop / start / restart any container with live state |
| Peering | `/peer` | BGP underlay + EVPN overlay sessions across the fabric |
| Tenants | `/tenants` | Per-VRF state (RD, routes, interfaces) across all leaves |

---

## Topology details

### IP plan

| Plane | Subnet |
|---|---|
| Super-spine loopbacks | `10.0.0.91/32`, `10.0.0.92/32` |
| Pod-1 spine loopbacks | `10.0.0.1/32`, `10.0.0.2/32` |
| Pod-2 spine loopbacks | `10.0.0.3/32`, `10.0.0.4/32` |
| Pod-1 leaf loopbacks | `10.0.0.11–14/32` |
| Pod-2 leaf loopbacks | `10.0.0.35–38/32` |
| Border-leaf loopbacks | `10.0.0.21/32`, `10.0.0.22/32` |
| SS-01 ↔ pod-1 P2P | `10.2.1.0/24` (to SP1), `10.2.1.2/24` (to SP2) |
| SS-01 ↔ pod-2 P2P | `10.2.3.0/24` (to SP3), `10.2.3.2/24` (to SP4) |
| SS-02 ↔ pod-1 P2P | `10.2.2.0/24` (to SP1), `10.2.2.2/24` (to SP2) |
| SS-02 ↔ pod-2 P2P | `10.2.4.0/24` (to SP3), `10.2.4.2/24` (to SP4) |
| Containerlab mgmt | `172.20.20.0/24` |
| TENANT1 VLAN 10 | `10.10.10.0/24` (anycast gw `10.10.10.1`) |
| TENANT1 VLAN 20 | `10.20.20.0/24` (anycast gw `10.20.20.1`) |
| TENANT2 VLAN 30 | `172.16.30.0/24` (anycast gw `172.16.30.1`) |
| TENANT3 VLAN 50 | `172.16.50.0/24` (anycast gw `172.16.50.1`) |

### VRF / VNI table

| VRF | VLAN(s) | L2 VNI | L3 VNI | Internet? |
|---|---|---|---|---|
| TENANT1 | 10, 20 | 10010, 10020 | 10000 | ✅ via border leaves → dual ISP |
| TENANT2 | 30 | 10030 | 20000 | ❌ internal only |
| TENANT3 | 50 | 10050 | 30000 | ❌ internal only |

### AS number table

| Node(s) | AS |
|---|---|
| super-spine-01, super-spine-02 | 65500 |
| spine-01, spine-02 | 65000 |
| spine-03, spine-04 | 65010 |
| leaf-01 | 65001 |
| leaf-02 | 65002 |
| leaf-03 | 65003 |
| leaf-04 | 65004 |
| leaf-05 | 65031 |
| leaf-06 | 65032 |
| leaf-07 | 65033 |
| leaf-08 | 65034 |
| border-leaf-01 | 65021 |
| border-leaf-02 | 65022 |
| isp-01 | 65100 |
| isp-02 | 65200 |
| frr-inet | 65999 |

### Routing protocols

| Protocol | Where | Purpose |
|---|---|---|
| eBGP (unique AS/leaf) | spines ↔ leaves, spines ↔ super-spines | underlay reachability |
| eBGP-multihop loopback | spines ↔ leaves, spines ↔ super-spines | EVPN overlay |
| eBGP | border-leaves ↔ ISPs | internet handoff in VRF TENANT1 |
| eBGP | ISPs ↔ frr-inet | simulated internet DFZ |

### EVPN route types

| Type | Use |
|---|---|
| Type-2 (MAC/IP) | host MAC learning + ARP suppression across VTEPs |
| Type-3 (IMET) | BUM replication list per L2 VNI |
| Type-5 (IP prefix) | border-leaves advertise ISP routes into TENANT1 across the fabric |

---

## SSH access

Add this to your workstation's `~/.ssh/config`:

```
Host 172.20.20.*
  ProxyJump root@192.168.236.139
  User admin
  StrictHostKeyChecking no
  UserKnownHostsFile /dev/null
```

Then:

```bash
ssh admin@172.20.20.91    # super-spine-01
ssh admin@172.20.20.92    # super-spine-02
ssh admin@172.20.20.101   # spine-01
ssh admin@172.20.20.111   # leaf-01
ssh admin@172.20.20.121   # border-leaf-01
ssh admin@172.20.20.250   # isp-01
```

Host PCs don't have SSH — use:

```bash
docker exec -it clab-vxlan-evpn-pc1 bash
```

---

## ⚠️ Important — always use containerlab to restart

```bash
# ✅ Correct — rewires all veth pairs
containerlab deploy --topo topology.clab.yml

# ❌ Wrong — loses veth wiring, BGP stays Idle(NoIf)
docker restart clab-vxlan-evpn-spine-01
```

See `LESSONS_LEARNED.md` for cEOS-specific gotchas and `RUNBOOK.md` for daily procedures.

---

## Credits

- **Arista Networks** — cEOS-lab free for personal use
- **SRL Labs** — Containerlab
- **nicolaka/netshoot** — the perfect host container
- **quay.io/frrouting/frr** — FRR internet simulation
- Reference topology design: [rajeshnetwork/VXLAN-EVPN-Containerlab](https://github.com/rajeshnetwork/VXLAN-EVPN-Containerlab)

## License

MIT
