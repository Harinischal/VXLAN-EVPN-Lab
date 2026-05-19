# VXLAN/EVPN Lab

A 5-stage Clos fabric built with [Containerlab](https://containerlab.dev/) using Arista cEOS, complete with a browser-based management portal.

## Topology

```
Internet (frr-inet · AS 65999)
      |
  ISP-01 (AS 65100)   ISP-02 (AS 65200)
      |                    |
  border-leaf-01       border-leaf-02
        \                 /
    spine-01   spine-02   spine-03   spine-04
       |   \  /   |          |   \  /   |
    leaf-01..04 (Pod-1)   leaf-05..08 (Pod-2)
       |                        |
   pc1-pc16 (32 hosts)      pc17-pc32
```

**Devices:** 2 super-spines · 4 spines · 8 leaves · 2 border-leaves · 2 ISPs · 1 FRR internet router · 32 Linux host PCs

**Tenants:** TENANT1 (internet egress) · TENANT2 · TENANT3

## Requirements

- [Containerlab](https://containerlab.dev/) v0.75+
- Arista cEOS image `ceos:4.36.0.1F` imported into Docker
- Python 3.10+ with venv
- Ubuntu 22.04/24.04/26.04

## Quick Start

```bash
# 1. Deploy the lab
cd topology
containerlab deploy --topo topology.clab.yml

# 2. Set up the portal
cd ../lab-portal
python3 -m venv venv
venv/bin/pip install flask flask-sock pyyaml
venv/bin/python app.py
```

Portal runs on **http://localhost:5000**

## Portal Features

| Page | URL | Description |
|---|---|---|
| Topology | `/` | Mermaid.js Clos diagram · click any device for terminal |
| Terminal | `/term/<node>` | In-browser xterm.js terminal via WebSocket |
| Looking Glass | `/lg` | Run show commands on any router |
| Packet Capture | `/cap` | tcpdump via nsenter · download .pcap |
| Lab Control | `/ctrl` | Stop / start / restart any container |
| Peering | `/peer` | BGP underlay + EVPN overlay sessions |
| Tenants | `/tenants` | Per-VRF state across all leaves |

## Import cEOS Image

```bash
docker import cEOS-lab-4.36.0.1F.tar ceos:4.36.0.1F
```

## Important

Always use `containerlab deploy` to start or restart the lab — plain `docker start` loses the veth wiring between containers.
