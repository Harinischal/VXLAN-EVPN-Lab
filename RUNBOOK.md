# VXLAN-EVPN Lab — Daily Runbook

## Where everything lives

| Thing | Path / URL |
|---|---|
| Lab host | `root@192.168.236.139` |
| Topology file | `/root/VXLAN-EVPN-Containerlab/topology.clab.yml` |
| Portal app | `/root/lab-portal/app.py` |
| Portal venv | `/root/lab-portal/venv/` |
| Packet captures | `/root/lab-portal/captures/` |
| Portal UI | http://192.168.236.139:5000 |

---

## Bring lab UP (after reboot)

```bash
ssh root@192.168.236.139
cd /root/VXLAN-EVPN-Containerlab
containerlab deploy --topo topology.clab.yml
# Wait ~3 min for all 51 containers to start and BGP/EVPN to converge.
```

Check it came up healthy:

```bash
# 51 containers running?
docker ps --format '{{.Names}}' | grep -c clab-vxlan-evpn-

# BGP underlay + EVPN all Established on spine-01?
docker exec clab-vxlan-evpn-spine-01 Cli -p15 -c "show ip bgp summary"
docker exec clab-vxlan-evpn-spine-01 Cli -p15 -c "show bgp evpn summary"

# Super-spine sessions up?
docker exec clab-vxlan-evpn-super-spine-01 Cli -p15 -c "show ip bgp summary"

# Intra-pod ping (TENANT1, pod-1):
docker exec clab-vxlan-evpn-pc1 ping -c 2 10.10.10.102

# Inter-pod ping via super-spines (TENANT1, pod-1 → pod-2):
docker exec clab-vxlan-evpn-pc1 ping -c 2 10.10.10.105
```

---

## Start the web portal

```bash
ssh root@192.168.236.139
pkill -f 'python.*app.py' 2>/dev/null
cd /root/lab-portal
nohup venv/bin/python app.py > /tmp/portal.log 2>&1 &
# Browse to http://192.168.236.139:5000
```

---

## Bring lab DOWN (before shutting host)

```bash
ssh root@192.168.236.139
cd /root/VXLAN-EVPN-Containerlab
containerlab destroy --topo topology.clab.yml --cleanup
```

---

## SSH into a network device

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
ssh admin@172.20.20.91    # super-spine-01  (admin/admin)
ssh admin@172.20.20.92    # super-spine-02
ssh admin@172.20.20.101   # spine-01
ssh admin@172.20.20.102   # spine-02
ssh admin@172.20.20.103   # spine-03
ssh admin@172.20.20.104   # spine-04
ssh admin@172.20.20.111   # leaf-01
ssh admin@172.20.20.121   # border-leaf-01
ssh admin@172.20.20.250   # isp-01
ssh admin@172.20.20.251   # isp-02
ssh admin@172.20.20.252   # frr-inet (vtysh)
```

---

## Enter a host container (pc1–pc32)

```bash
docker exec -it clab-vxlan-evpn-pc1 bash
# All netshoot tools available: ping, traceroute, curl, dig, tcpdump, nmap, etc.
```

---

## Topology quick reference

```
frr-inet (AS 65999) — ~500 internet prefixes
      |
isp-01 (AS 65100)    isp-02 (AS 65200)
      |                    |
border-leaf-01 (AS 65021)  border-leaf-02 (AS 65022)
      |
spine-01,02 (AS 65000)         spine-03,04 (AS 65010)
  |  |  |  |                     |  |  |  |
leaf-01..04                   leaf-05..08
AS 65001-65004                AS 65031-65034
  (pc1-pc16)                    (pc17-pc32)

VRFs:
  TENANT1 (VLAN10+20)  10.10.10/24 + 10.20.20/24   internet ✅
  TENANT2 (VLAN30)     172.16.30/24                 internet ❌
  TENANT3 (VLAN50)     172.16.50/24                 internet ❌
```

---

## Useful one-liners

```bash
# Watch container RAM usage during deploy:
watch -n 2 'free -h | head -2; docker stats --no-stream --format "{{.Name}}\t{{.MemUsage}}" | grep clab- | sort'

# BGP summary across ALL routers at once (runs in parallel):
for n in super-spine-01 super-spine-02 spine-01 spine-02 spine-03 spine-04; do
  echo "=== $n ===" && docker exec clab-vxlan-evpn-$n Cli -p15 -c "show ip bgp summary" 2>&1 | grep -E "Estab|Idle|Active|neighbor"
done

# Check VXLAN VTEP table on a leaf:
docker exec clab-vxlan-evpn-leaf-01 Cli -p15 -c "show vxlan vtep"

# Check EVPN type-5 routes (internet in TENANT1):
docker exec clab-vxlan-evpn-leaf-01 Cli -p15 -c "show bgp evpn route-type ip-prefix"

# Live portal log:
tail -f /tmp/portal.log
```

---

## ⚠️ Always use containerlab to restart — never `docker restart`

```bash
# ✅ Correct — rewires all veth pairs between containers
containerlab deploy --topo topology.clab.yml

# ❌ Wrong — veth pairs are lost, BGP stays Idle(NoIf)
docker restart clab-vxlan-evpn-spine-01
docker start clab-vxlan-evpn-spine-01
```

---

## Save config changes

Container internal state is volatile — wiped on `containerlab destroy --cleanup`.
If you make config changes inside a cEOS node:

```bash
# Inside cEOS: save to flash
leaf-01# write memory

# Pull config back to host before destroying:
docker exec clab-vxlan-evpn-leaf-01 cat /mnt/flash/startup-config > /root/configs/leaf-01.cfg
```
