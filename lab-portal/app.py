#!/usr/bin/env python3
"""
Lab portal — VXLAN/EVPN containerlab dashboard.
Runs directly on the lab host (no SSH hop).
"""
import fcntl, json, os, pty, select, shlex, signal, struct, subprocess
import termios, threading, time, uuid, ipaddress
import json as _json
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, render_template, request, jsonify, send_file
from flask_sock import Sock

app  = Flask(__name__)
sock = Sock(app)

PREFIX  = "clab-vxlan-evpn-"
CAP_DIR = "/root/lab-portal/captures"

# ─── Lab inventory ────────────────────────────────────────────────────────────
NODES = {
    "super-spines": [
        ("super-spine-01", "172.20.20.91",  "AS 65500"),
        ("super-spine-02", "172.20.20.92",  "AS 65500"),
    ],
    "pod-1 spines": [
        ("spine-01", "172.20.20.101", "AS 65000"),
        ("spine-02", "172.20.20.102", "AS 65000"),
    ],
    "pod-1 leaves": [
        ("leaf-01", "172.20.20.111", "AS 65001"),
        ("leaf-02", "172.20.20.112", "AS 65002"),
        ("leaf-03", "172.20.20.113", "AS 65003"),
        ("leaf-04", "172.20.20.114", "AS 65004"),
    ],
    "pod-1 border leaves": [
        ("border-leaf-01", "172.20.20.121", "AS 65021"),
        ("border-leaf-02", "172.20.20.122", "AS 65022"),
    ],
    "pod-2 spines": [
        ("spine-03", "172.20.20.103", "AS 65010"),
        ("spine-04", "172.20.20.104", "AS 65010"),
    ],
    "pod-2 leaves": [
        ("leaf-05", "172.20.20.131", "AS 65031"),
        ("leaf-06", "172.20.20.132", "AS 65032"),
        ("leaf-07", "172.20.20.133", "AS 65033"),
        ("leaf-08", "172.20.20.134", "AS 65034"),
    ],
    "external": [
        ("isp-01",   "172.20.20.250", "AS 65100"),
        ("isp-02",   "172.20.20.251", "AS 65200"),
        ("frr-inet", "172.20.20.252", "AS 65999 (Internet)"),
    ],
}

HOSTS = [(f"pc{i}", "TENANT1" if i in list(range(1,9)) + list(range(17,25)) else
                    "TENANT2" if i in list(range(9,13)) + list(range(25,29)) else
                    "TENANT3", f"leaf-0{(i-1)%4+1}" if i<=16 else f"leaf-0{(i-17)%4+5}")
         for i in range(1, 33)]

ROUTERS = [n for group in NODES.values() for (n, _, _) in group]

LG_COMMANDS = [
    "show ip bgp summary",
    "show bgp evpn summary",
    "show bgp evpn",
    "show ip route",
    "show ip route vrf TENANT1",
    "show ip route vrf TENANT2",
    "show ip route vrf TENANT3",
    "show vxlan vtep",
    "show vxlan address-table",
    "show vxlan config-sanity",
    "show interfaces description",
    "show ip interface brief",
    "show running-config interfaces Vxlan1",
    "show running-config section bgp",
    "show version",
    "show platform fap counters",
]

# ─── Local execution helper ───────────────────────────────────────────────────
def local_run(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "") + (("\n[STDERR]\n" + r.stderr) if r.stderr else "")
    except subprocess.TimeoutExpired:
        return f"[timeout after {timeout}s]"
    except Exception as e:
        return f"[error: {e}]"


def is_router(name):
    return name in ROUTERS


def is_host(name):
    return name.startswith("pc") and name[2:].isdigit() and 1 <= int(name[2:]) <= 32


# ─── Link states ─────────────────────────────────────────────────────────────
TOPO_LINKS = [
    ("frr-inet", "isp-01"),
    ("frr-inet", "isp-02"),
    ("isp-01", "border-leaf-01"),
    ("isp-01", "border-leaf-02"),
    ("isp-02", "border-leaf-01"),
    ("isp-02", "border-leaf-02"),
    ("border-leaf-01", "spine-01"),
    ("border-leaf-01", "spine-02"),
    ("border-leaf-02", "spine-01"),
    ("border-leaf-02", "spine-02"),
    ("spine-01", "super-spine-01"),
    ("spine-01", "super-spine-02"),
    ("spine-02", "super-spine-01"),
    ("spine-02", "super-spine-02"),
    ("spine-03", "super-spine-01"),
    ("spine-03", "super-spine-02"),
    ("spine-04", "super-spine-01"),
    ("spine-04", "super-spine-02"),
    ("spine-01", "leaf-01"), ("spine-01", "leaf-02"),
    ("spine-01", "leaf-03"), ("spine-01", "leaf-04"),
    ("spine-02", "leaf-01"), ("spine-02", "leaf-02"),
    ("spine-02", "leaf-03"), ("spine-02", "leaf-04"),
    ("spine-03", "leaf-05"), ("spine-03", "leaf-06"),
    ("spine-03", "leaf-07"), ("spine-03", "leaf-08"),
    ("spine-04", "leaf-05"), ("spine-04", "leaf-06"),
    ("spine-04", "leaf-07"), ("spine-04", "leaf-08"),
]

_state_cache = {"ts": 0, "states": {}}


def container_states(max_age=5):
    now = time.time()
    if now - _state_cache["ts"] < max_age and _state_cache["states"]:
        return _state_cache["states"]
    raw = local_run("docker ps -a --format '{{.Names}} {{.State}}'", timeout=8)
    states = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith(PREFIX):
            continue
        parts = line.split(None, 1)
        if len(parts) == 2:
            states[parts[0][len(PREFIX):]] = parts[1].strip()
    _state_cache["ts"] = now
    _state_cache["states"] = states
    return states


def link_states():
    states = container_states()
    up, down = [], []
    for i, (a, b) in enumerate(TOPO_LINKS):
        if states.get(a) == "running" and states.get(b) == "running":
            up.append(i)
        else:
            down.append(i)
    return up, down


# ─── Peering data ─────────────────────────────────────────────────────────────
FRR_IFACE_IPS = {
    "10.99.0.1":   ("frr-inet", "eth1"),
    "10.99.0.5":   ("frr-inet", "eth2"),
    "10.99.99.99": ("frr-inet", "Loopback0"),
}

NODE_ASN = {n: asn.replace("AS ", "").split()[0]
            for grp in NODES.values() for (n, _ip, asn) in grp}


def _strip_json(raw):
    if not raw:
        return None
    i = raw.find('{')
    j = raw.find('[')
    cands = [x for x in (i, j) if x >= 0]
    if not cands:
        return None
    body = raw[min(cands):].split('\n[STDERR]')[0].strip()
    try:
        return _json.loads(body)
    except Exception:
        return None


def cli_json(node, cmd, timeout=12):
    container = PREFIX + node
    quoted = shlex.quote(cmd + " | json")
    return _strip_json(local_run(f"docker exec {container} Cli -p15 -c {quoted}", timeout))


def vtysh_json(node, cmd, timeout=10):
    container = PREFIX + node
    quoted = shlex.quote(cmd + " json")
    return _strip_json(local_run(f"docker exec {container} vtysh -c {quoted}", timeout))


def _collect_one(node):
    if node == "frr-inet":
        return {"node": node, "kind": "frr",
                "underlay": vtysh_json(node, "show ip bgp summary"),
                "evpn": None, "vnis": None, "vtep": None,
                "vxlan_intf": None, "ipif": None, "interfaces": None}
    return {
        "node": node, "kind": "ceos",
        "interfaces": cli_json(node, "show interfaces description"),
        "underlay":   cli_json(node, "show ip bgp summary"),
        "evpn":       cli_json(node, "show bgp evpn summary"),
        "vnis":       cli_json(node, "show vxlan vni"),
        "vtep":       cli_json(node, "show vxlan vtep"),
        "vxlan_intf": cli_json(node, "show interface Vxlan 1"),
        "ipif":       cli_json(node, "show ip interface brief"),
    }


def build_ip_index(raw_nodes):
    idx = dict(FRR_IFACE_IPS)
    for n, d in raw_nodes.items():
        for ifname, ent in ((d.get("ipif") or {}).get("interfaces") or {}).items():
            addr = ((ent.get("interfaceAddress") or {}).get("ipAddr") or {}).get("address")
            if addr and addr != "0.0.0.0":
                idx[addr] = (n, ifname)
    return idx


def _local_iface_for_peer(data, peer_ip):
    if not data or not data.get("ipif"):
        return None
    try:
        p = ipaddress.ip_address(peer_ip)
    except Exception:
        return None
    for ifname, ent in ((data["ipif"]).get("interfaces") or {}).items():
        ipa = ((ent.get("interfaceAddress") or {}).get("ipAddr") or {})
        addr, ml = ipa.get("address"), ipa.get("maskLen")
        if not addr or not ml:
            continue
        try:
            if p in ipaddress.ip_network(f"{addr}/{ml}", strict=False):
                return ifname
        except Exception:
            pass
    return None


def _frr_local_iface(peer_ip):
    try:
        p = ipaddress.ip_address(peer_ip)
    except Exception:
        return None
    for ip, (node, ifname) in FRR_IFACE_IPS.items():
        if node != "frr-inet" or ifname == "Loopback0":
            continue
        try:
            if p in ipaddress.ip_network(f"{ip}/30", strict=False):
                return ifname
        except Exception:
            pass
    return None


def normalize_node(name, data, ip_index):
    out = {"node": name, "kind": data.get("kind", "?"),
           "asn": NODE_ASN.get(name, "?"),
           "loopback": None, "vtep_src": None,
           "underlay_peers": [], "evpn_peers": [],
           "vnis": [], "remote_vteps": []}

    if data.get("kind") == "frr":
        ipv4 = (data.get("underlay") or {}).get("ipv4Unicast") or {}
        out["loopback"] = ipv4.get("routerId")
        for peer_ip, p in (ipv4.get("peers") or {}).items():
            pn, pi = ip_index.get(peer_ip, (None, None))
            out["underlay_peers"].append({
                "peer_ip": peer_ip, "peer_node": pn, "peer_iface": pi,
                "local_iface": _frr_local_iface(peer_ip),
                "peer_asn": str(p.get("remoteAs", "?")),
                "state": p.get("state", "?"),
                "prefixes_rx": p.get("pfxRcd"),
                "prefixes_tx": p.get("pfxSnt"),
                "desc": p.get("description", ""),
            })
        return out

    ipif = (data.get("ipif") or {}).get("interfaces") or {}
    lo_addr = ((ipif.get("Loopback0") or {}).get("interfaceAddress") or {}).get("ipAddr", {})
    out["loopback"] = lo_addr.get("address")

    udl_peers = (((data.get("underlay") or {}).get("vrfs") or {})
                 .get("default") or {}).get("peers") or {}
    for peer_ip, p in udl_peers.items():
        pn, pi = ip_index.get(peer_ip, (None, None))
        out["underlay_peers"].append({
            "peer_ip": peer_ip, "peer_node": pn, "peer_iface": pi,
            "local_iface": _local_iface_for_peer(data, peer_ip),
            "peer_asn": str(p.get("asn", "?")),
            "state": p.get("peerState", "?"),
            "prefixes_rx": p.get("prefixAccepted"),
            "prefixes_tx": p.get("prefixAdvertised"),
            "desc": p.get("description", ""),
        })

    evpn_peers = (((data.get("evpn") or {}).get("vrfs") or {})
                  .get("default") or {}).get("peers") or {}
    for peer_ip, p in evpn_peers.items():
        pn, _ = ip_index.get(peer_ip, (None, None))
        out["evpn_peers"].append({
            "peer_ip": peer_ip, "peer_node": pn,
            "peer_asn": str(p.get("asn", "?")),
            "state": p.get("peerState", "?"),
            "prefixes_rx": p.get("prefixAccepted"),
            "prefixes_tx": p.get("prefixAdvertised"),
            "desc": p.get("description", ""),
        })

    vx1 = ((data.get("vxlan_intf") or {}).get("interfaces") or {}).get("Vxlan1") or {}
    out["vtep_src"] = vx1.get("srcIpAddr")

    bindings = (((data.get("vnis") or {}).get("vxlanIntfs") or {})
                .get("Vxlan1") or {}).get("vniBindings") or {}
    for vni_str, b in bindings.items():
        try:
            vni_int = int(vni_str)
        except ValueError:
            vni_int = vni_str
        out["vnis"].append({
            "vni": vni_int, "vlan": b.get("vlan"),
            "interfaces": list((b.get("interfaces") or {}).keys()),
        })
    out["vnis"].sort(key=lambda v: (v.get("vlan") or 0))

    rvteps = ((((data.get("vtep") or {}).get("interfaces") or {})
               .get("Vxlan1") or {}).get("vteps") or [])
    for ip in rvteps:
        n, _ = ip_index.get(ip, (None, None))
        out["remote_vteps"].append({"ip": ip, "node": n})

    return out


def aggregate_sessions(normalized):
    udl, evp = {}, {}
    for n, d in normalized.items():
        for p in d.get("underlay_peers", []):
            pn = p.get("peer_node")
            if not pn or pn == n:
                continue
            key = tuple(sorted((n, pn)))
            entry = udl.setdefault(key, {
                "a": key[0], "b": key[1],
                "a_iface": None, "b_iface": None,
                "state": p.get("state", "?")})
            if n == key[0]:
                entry["a_iface"] = entry["a_iface"] or p.get("local_iface")
                entry["b_iface"] = entry["b_iface"] or p.get("peer_iface")
            else:
                entry["b_iface"] = entry["b_iface"] or p.get("local_iface")
                entry["a_iface"] = entry["a_iface"] or p.get("peer_iface")
        for p in d.get("evpn_peers", []):
            pn = p.get("peer_node")
            if not pn or pn == n:
                continue
            key = tuple(sorted((n, pn)))
            evp.setdefault(key, {"a": key[0], "b": key[1],
                                 "state": p.get("state", "?")})
    return {"underlay": list(udl.values()), "evpn": list(evp.values())}


_peer_cache = {"ts": 0, "data": None}


def refresh_peer_cache():
    raw = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {n: ex.submit(_collect_one, n) for n in ROUTERS}
        for n, f in futs.items():
            try:
                raw[n] = f.result(timeout=30)
            except Exception as e:
                raw[n] = {"node": n, "kind": "error", "error": str(e)}
    ip_index = build_ip_index(raw)
    nodes = {n: normalize_node(n, d, ip_index) for n, d in raw.items()}
    payload = {
        "ts": time.time(),
        "ts_human": time.strftime("%Y-%m-%d %H:%M:%S"),
        "nodes": nodes,
        "sessions": aggregate_sessions(nodes),
    }
    _peer_cache["ts"] = payload["ts"]
    _peer_cache["data"] = payload
    return payload


# ─── Tenants ──────────────────────────────────────────────────────────────────
TENANTS = [
    {
        "name": "TENANT1",
        "vrf":  "TENANT1",
        "vlans": [
            {"vlan": 10, "vni": 10010, "subnet": "10.10.10.0/24", "gw": "10.10.10.1"},
            {"vlan": 20, "vni": 10020, "subnet": "10.20.20.0/24", "gw": "10.20.20.1"},
        ],
        "internet": True,
        "blurb": "Two VLANs · dual-homed to both ISPs via border-leaves · only tenant with internet egress",
    },
    {
        "name": "TENANT2",
        "vrf":  "TENANT2",
        "vlans": [{"vlan": 30, "vni": 10030, "subnet": "172.16.30.0/24", "gw": "172.16.30.1"}],
        "internet": False,
        "blurb": "Single VLAN · isolated tenant · no internet egress",
    },
    {
        "name": "TENANT3",
        "vrf":  "TENANT3",
        "vlans": [{"vlan": 50, "vni": 10050, "subnet": "172.16.50.0/24", "gw": "172.16.50.1"}],
        "internet": False,
        "blurb": "Single VLAN · isolated tenant · no internet egress",
    },
]
TENANT_LEAVES = ["leaf-01","leaf-02","leaf-03","leaf-04",
                 "leaf-05","leaf-06","leaf-07","leaf-08",
                 "border-leaf-01","border-leaf-02"]


def tenant_hosts(tname):
    return [{"pc": h[0], "leaf": h[2]} for h in HOSTS if h[1] == tname]


def _per_node_tenant(node, tname):
    vrf = cli_json(node, f"show vrf {tname}")
    rt  = cli_json(node, f"show ip route vrf {tname} summary")
    v = ((vrf or {}).get("vrfs") or {}).get(tname) or {}
    r = ((rt  or {}).get("vrfs") or {}).get(tname) or {}
    bgp = (r.get("bgpCounts") or {}).get("bgpTotal", 0)
    return {
        "rd":        v.get("routeDistinguisher", ""),
        "state":     v.get("vrfState", "missing"),
        "ifaces":    len(v.get("interfacesV4", []) or []),
        "connected": r.get("connected", 0),
        "bgp":       bgp,
    }


_tenant_cache = {"ts": 0, "data": None}


def collect_tenant_state():
    state = {t["name"]: {} for t in TENANTS}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = []
        for t in TENANTS:
            for n in TENANT_LEAVES:
                futs.append((t["name"], n, ex.submit(_per_node_tenant, n, t["name"])))
        for tname, n, f in futs:
            try:
                state[tname][n] = f.result(timeout=15)
            except Exception as e:
                state[tname][n] = {"error": str(e)}
    _tenant_cache["ts"] = time.time()
    _tenant_cache["data"] = state
    return state


# ─── WebSocket terminal ───────────────────────────────────────────────────────
def _set_winsize(fd, rows, cols):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))


@sock.route('/ws/<container>')
def terminal(ws, container):
    try:
        img = subprocess.run(
            ['docker', 'inspect', '--format', '{{.Config.Image}}', container],
            capture_output=True, text=True, timeout=5
        ).stdout.strip().lower()
    except Exception:
        img = ''

    if 'ceos' in img or 'arista' in img:
        cmd = ['docker', 'exec', '-it', container, 'Cli']
    elif container.endswith('frr-inet') or 'frr' in img:
        cmd = ['docker', 'exec', '-it', container, 'vtysh']
    else:
        cmd = ['docker', 'exec', '-it', container, '/bin/sh']

    master, slave = pty.openpty()
    try:
        proc = subprocess.Popen(
            cmd, stdin=slave, stdout=slave, stderr=slave,
            close_fds=True, preexec_fn=os.setsid
        )
        os.close(slave)
    except Exception as e:
        try: os.close(slave)
        except: pass
        os.close(master)
        ws.send(f'\r\nFailed to open terminal: {e}\r\n')
        return

    done = threading.Event()

    def reader():
        while not done.is_set():
            try:
                r, _, _ = select.select([master], [], [], 0.05)
                if r:
                    data = os.read(master, 4096)
                    if not data:
                        break
                    ws.send(data.decode('utf-8', errors='replace'))
            except OSError:
                break
        done.set()

    threading.Thread(target=reader, daemon=True).start()

    try:
        while True:
            data = ws.receive()
            if data is None:
                break
            if isinstance(data, str) and data.startswith('{"resize"'):
                try:
                    msg = _json.loads(data)
                    r = msg.get('resize', {})
                    _set_winsize(master, r.get('rows', 24), r.get('cols', 80))
                except Exception:
                    pass
                continue
            if isinstance(data, str):
                data = data.encode()
            os.write(master, data)
    except Exception:
        pass
    finally:
        done.set()
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            pass
        try:
            os.close(master)
        except Exception:
            pass


# ─── Routes ───────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    up, down = link_states()
    return render_template("index.html",
                           node_groups=NODES, hosts=HOSTS,
                           up_links=",".join(map(str, up)) if up else "",
                           down_links=",".join(map(str, down)) if down else "")


@app.route("/term/<node>")
def term(node):
    if not (is_router(node) or is_host(node) or node == "lab-host"):
        return "unknown node", 404
    return render_template("term.html", node=node)


@app.route("/lg")
def lg():
    return render_template("lg.html", routers=ROUTERS, commands=LG_COMMANDS)


@app.route("/lg/run", methods=["POST"])
def lg_run():
    node = request.form.get("node", "").strip()
    cmd  = request.form.get("cmd", "").strip()
    if node not in ROUTERS:
        return jsonify(ok=False, error=f"unknown router '{node}'")
    if not cmd or len(cmd) > 200:
        return jsonify(ok=False, error="bad command")
    container = PREFIX + node
    out = local_run(f"docker exec {container} Cli -p15 -c {shlex.quote(cmd)}", timeout=20)
    return jsonify(ok=True, output=out)


@app.route("/cap")
def cap():
    iface_choices = ["eth1", "eth2", "eth3", "eth4", "eth5", "eth6", "eth7", "eth8"]
    return render_template("cap.html",
                           routers=ROUTERS, hosts=[h[0] for h in HOSTS],
                           ifaces=iface_choices)


@app.route("/cap/start", methods=["POST"])
def cap_start():
    node    = request.form.get("node", "").strip()
    iface   = request.form.get("iface", "eth1").strip()
    seconds = max(1, min(120, int(request.form.get("seconds", "30"))))
    if not (is_router(node) or is_host(node)):
        return jsonify(ok=False, error=f"unknown node '{node}'")
    if not iface.startswith(("eth", "Ethernet")):
        return jsonify(ok=False, error="bad interface")

    container = PREFIX + node
    fname = f"{node}_{iface}_{int(time.time())}.pcap"
    pcap_path = os.path.join(CAP_DIR, fname)

    cap_cmd = (f"nsenter -t $(docker inspect -f '{{{{.State.Pid}}}}' {container}) "
               f"-n tcpdump -i {iface} -U -w {pcap_path} -G {seconds} -W 1 2>&1 | tail -5")
    out = local_run(cap_cmd, timeout=seconds + 20)

    if not os.path.isfile(pcap_path):
        return jsonify(ok=False, error="pcap file not created", capture_log=out)

    size = os.path.getsize(pcap_path)
    return jsonify(ok=True, filename=fname, size=size, capture_log=out)


@app.route("/cap/download/<name>")
def cap_download(name):
    if "/" in name or ".." in name:
        return "no", 400
    path = os.path.join(CAP_DIR, name)
    if not os.path.isfile(path):
        return "not found", 404
    return send_file(path, as_attachment=True, mimetype="application/vnd.tcpdump.pcap")


@app.route("/cap/live/<node>/<iface>")
def cap_live(node, iface):
    container = PREFIX + node
    cmd = (f"ssh root@192.168.236.139 \"nsenter -t \\$(docker inspect -f "
           f"'{{{{.State.Pid}}}}' {container}) -n tcpdump -i {iface} -U -w - "
           f"not port 22\" | wireshark -k -i -")
    return jsonify(cmd=cmd)


@app.route("/ctrl")
def ctrl():
    states = container_states(max_age=0)
    grouped = []
    for group, nodes in NODES.items():
        items = [(n, states.get(n, "missing"), ip, asn) for (n, ip, asn) in nodes]
        grouped.append((group, items))
    host_items = [(h[0], states.get(h[0], "missing"), h[2], h[1]) for h in HOSTS]
    return render_template("ctrl.html", grouped=grouped, host_items=host_items)


@app.route("/ctrl/action/<action>/<node>", methods=["POST"])
def ctrl_action(action, node):
    if action not in ("stop", "start", "restart"):
        return jsonify(ok=False, error=f"unknown action '{action}'")
    if not (is_router(node) or is_host(node)):
        return jsonify(ok=False, error=f"unknown node '{node}'")
    container = PREFIX + node
    out = local_run(f"docker {action} {container}", timeout=30)
    _state_cache["ts"] = 0
    return jsonify(ok=True, output=out.strip())


@app.route("/ctrl/states")
def ctrl_states():
    return jsonify(container_states(max_age=2))


@app.route("/peer")
def peer_view():
    return render_template("peering.html", routers=ROUTERS, node_groups=NODES)


@app.route("/peer/refresh", methods=["POST"])
def peer_refresh():
    return jsonify(ok=True, data=refresh_peer_cache())


@app.route("/peer/data")
def peer_data():
    if not _peer_cache["data"]:
        return jsonify(ok=True, data=None, empty=True)
    return jsonify(ok=True, data=_peer_cache["data"],
                   age_sec=int(time.time() - _peer_cache["ts"]))


@app.route("/tenants")
def tenants_view():
    hosts_by = {t["name"]: tenant_hosts(t["name"]) for t in TENANTS}
    return render_template("tenants.html", tenants=TENANTS,
                           hosts_by=hosts_by, leaves=TENANT_LEAVES)


@app.route("/tenants/refresh", methods=["POST"])
def tenants_refresh():
    data = collect_tenant_state()
    return jsonify(ok=True, data=data, ts_human=time.strftime("%Y-%m-%d %H:%M:%S"))


@app.route("/tenants/data")
def tenants_data():
    if not _tenant_cache["data"]:
        return jsonify(ok=True, data=None, empty=True)
    return jsonify(ok=True, data=_tenant_cache["data"],
                   ts_human=time.strftime("%Y-%m-%d %H:%M:%S",
                                          time.localtime(_tenant_cache["ts"])),
                   age_sec=int(time.time() - _tenant_cache["ts"]))


if __name__ == "__main__":
    os.makedirs(CAP_DIR, exist_ok=True)
    app.run(host="0.0.0.0", port=5000, debug=False)
