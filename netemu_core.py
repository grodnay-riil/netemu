"""
netemu_core.py — Linux tc/netem control plane for NetEmu
Requires: iproute2, privileged container (NET_ADMIN + NET_RAW)

Copyright (c) 2025 Skana Robotics Ltd. All rights reserved.
Proprietary and confidential. Unauthorized use prohibited.
"""

import subprocess
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import os

PROFILES_DIR = Path("/app/profiles")
PROFILES_DIR.mkdir(parents=True, exist_ok=True)

BRIDGE = os.environ.get("NETEMU_BRIDGE", "br0")

# ─── Data Models ─────────────────────────────────────────────────────────────

@dataclass
class QoSClass:
    dscp:       int
    name:       str
    priority:   int
    min_kbps:   int   = 1        # HTB rate (guaranteed minimum), default 1 kbps
    max_kbps:   int   = 1000000  # HTB ceil (maximum), default 1 Gbps
    queue_limit: int  = 1000     # netem queue depth in packets before drop

@dataclass
class DirectionConfig:
    bw_kbps:     float = 0.0
    latency_ms:  int   = 0
    jitter_ms:   int   = 0
    loss_pct:    float = 0.0
    corrupt_pct: float = 0.0
    reorder_pct: float = 0.0

@dataclass
class LinkConfig:
    if_a:        str  = "eth0"
    if_b:        str  = "eth1"
    mtu:         int  = 1500
    forward:     DirectionConfig = field(default_factory=DirectionConfig)
    reverse:     DirectionConfig = field(default_factory=DirectionConfig)
    qos_classes: list[QoSClass]  = field(default_factory=list)

    def to_dict(self) -> dict:
        import dataclasses
        return dataclasses.asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "LinkConfig":
        fwd = DirectionConfig(**d.get("forward", {}))
        rev = DirectionConfig(**d.get("reverse", {}))
        qos = [QoSClass(**q) for q in d.get("qos_classes", [])]
        return LinkConfig(
            if_a=d["if_a"], if_b=d["if_b"],
            mtu=d.get("mtu", 1500),
            forward=fwd, reverse=rev,
            qos_classes=qos,
        )

# ─── Shell helpers ────────────────────────────────────────────────────────────

def _run(cmd: str, check=True) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and result.returncode != 0:
        benign = [
            "already exists",
            "RTNETLINK answers: File exists",
            "Cannot find device",
        ]
        if not any(b in result.stderr for b in benign):
            raise RuntimeError(f"Command failed: {cmd}\n{result.stderr.strip()}")
    return result

def _run_many(cmds: list[str], log: list[str]) -> None:
    for cmd in cmds:
        log.append(f"$ {cmd}")
        try:
            r = _run(cmd)
            if r.stdout.strip():
                log.append(r.stdout.strip())
        except RuntimeError as e:
            log.append(f"  ERROR: {e}")

# ─── Interface discovery ──────────────────────────────────────────────────────

def list_interfaces() -> list[str]:
    ifaces = []
    base = Path("/sys/class/net")
    for p in sorted(base.iterdir()):
        name = p.name
        if name == "lo" or name.startswith("br"):
            continue
        ifaces.append(name)
    return ifaces

# ─── Bridge setup ─────────────────────────────────────────────────────────────

def _setup_bridge(if_a: str, if_b: str, log: list[str]) -> None:
    cmds = [
        f"ip link add name {BRIDGE} type bridge",
        f"ip link set {BRIDGE} up",
        f"ip link set {if_a} master {BRIDGE}",
        f"ip link set {if_b} master {BRIDGE}",
        f"ip link set {if_a} up",
        f"ip link set {if_b} up",
    ]
    _run_many(cmds, log)

def _teardown_bridge(log: list[str]) -> None:
    cmds = [
        f"ip link set {BRIDGE} down",
        f"ip link del {BRIDGE}",
    ]
    _run_many(cmds, log)

# ─── tc / netem builders ──────────────────────────────────────────────────────

def _netem_params(d: DirectionConfig) -> str:
    parts = []
    if d.latency_ms > 0:
        if d.jitter_ms > 0:
            parts.append(f"delay {d.latency_ms}ms {d.jitter_ms}ms distribution normal")
        else:
            parts.append(f"delay {d.latency_ms}ms")
    if d.loss_pct > 0:
        parts.append(f"loss {d.loss_pct:.3f}%")
    if d.corrupt_pct > 0:
        parts.append(f"corrupt {d.corrupt_pct:.3f}%")
    if d.reorder_pct > 0 and d.latency_ms > 0:
        parts.append(f"reorder {d.reorder_pct:.1f}% 25%")
    return " ".join(parts) if parts else "delay 0ms"

def _apply_qos(iface: str, d: DirectionConfig,
               classes: list[QoSClass], log: list[str]) -> None:
    cmds = []
    total_bw = f"{int(d.bw_kbps)}kbit" if d.bw_kbps > 0 else "1gbit"
    cmds.append(f"tc qdisc add dev {iface} root handle 1: htb default 99")
    cmds.append(
        f"tc class add dev {iface} parent 1: classid 1:1 htb rate {total_bw} ceil {total_bw}"
    )
    # Separate DSCP-matched classes from the catch-all (dscp < 0)
    matched = [cls for cls in classes if cls.dscp >= 0]
    be_cls  = next((cls for cls in classes if cls.dscp < 0), None)

    for i, cls in enumerate(matched, start=10):
        handle = i
        cmds.append(
            f"tc class add dev {iface} parent 1:1 classid 1:{handle} "
            f"htb rate {cls.min_kbps}kbit ceil {cls.max_kbps}kbit prio {cls.priority}"
        )
        netem = _netem_params(d)
        cmds.append(
            f"tc qdisc add dev {iface} parent 1:{handle} handle {handle}: netem {netem} limit {cls.queue_limit}"
        )
        tos_val = cls.dscp << 2
        cmds.append(
            f"tc filter add dev {iface} parent 1: protocol ip prio {cls.priority} "
            f"u32 match ip tos {tos_val} 0xfc flowid 1:{handle}"
        )
    be_rate  = f"{be_cls.min_kbps}kbit" if be_cls else "1mbit"
    be_ceil  = f"{be_cls.max_kbps}kbit" if be_cls else total_bw
    be_prio  = be_cls.priority           if be_cls else 7
    be_limit = be_cls.queue_limit        if be_cls else 1000
    cmds.append(
        f"tc class add dev {iface} parent 1:1 classid 1:99 htb rate {be_rate} ceil {be_ceil} prio {be_prio}"
    )
    be_netem = _netem_params(d)
    cmds.append(f"tc qdisc add dev {iface} parent 1:99 handle 99: netem {be_netem} limit {be_limit}")
    _run_many(cmds, log)

# ─── Public API ───────────────────────────────────────────────────────────────

def apply(cfg: LinkConfig) -> list[str]:
    log: list[str] = []

    # Tear down any previous state
    for iface in [cfg.if_a, cfg.if_b]:
        _run(f"tc qdisc del dev {iface} root", check=False)
    _run(f"ip link set {BRIDGE} down", check=False)
    _run(f"ip link del {BRIDGE}", check=False)

    _setup_bridge(cfg.if_a, cfg.if_b, log)

    for iface in [cfg.if_a, cfg.if_b]:
        _run_many([f"ip link set dev {iface} mtu {cfg.mtu}"], log)

    # if_b egress = A→B (forward), if_a egress = B→A (reverse)
    _apply_qos(cfg.if_b, cfg.forward,  cfg.qos_classes, log)
    _apply_qos(cfg.if_a, cfg.reverse, cfg.qos_classes, log)

    return log

def bridge_only(if_a: str, if_b: str, mtu: int = 1500) -> list[str]:
    """Set up the bridge with no impairments — passthrough mode."""
    log: list[str] = []
    for iface in [if_a, if_b]:
        _run(f"tc qdisc del dev {iface} root", check=False)
    _run(f"ip link set {BRIDGE} down", check=False)
    _run(f"ip link del {BRIDGE}", check=False)
    _setup_bridge(if_a, if_b, log)
    for iface in [if_a, if_b]:
        _run_many([f"ip link set dev {iface} mtu {mtu}"], log)
    return log

def reset(if_a: str, if_b: str) -> list[str]:
    log: list[str] = []
    for iface in [if_a, if_b]:
        _run_many([
            f"tc qdisc del dev {iface} root",
            f"ip link set dev {iface} mtu 1500",
        ], log)
    _teardown_bridge(log)
    return log

# ─── Statistics ───────────────────────────────────────────────────────────────

def _read_sys_counter(iface: str, counter: str) -> int:
    try:
        return int(Path(f"/sys/class/net/{iface}/statistics/{counter}").read_text())
    except Exception:
        return 0

def _parse_tc_stats(iface: str) -> list[dict]:
    result = _run(f"tc -s qdisc show dev {iface}", check=False)
    qdiscs, current = [], {}
    for line in result.stdout.splitlines():
        line = line.strip()
        m = re.match(r"qdisc (\S+) (\S+).*", line)
        if m:
            if current:
                qdiscs.append(current)
            current = {"type": m.group(1), "handle": m.group(2)}
            continue
        m = re.match(r"Sent (\d+) bytes (\d+) pkt.*dropped (\d+).*overlimits (\d+)", line)
        if m and current:
            current.update({
                "bytes": int(m.group(1)), "packets": int(m.group(2)),
                "dropped": int(m.group(3)), "overlimits": int(m.group(4)),
            })
    if current:
        qdiscs.append(current)
    return qdiscs

def get_stats(if_a: str, if_b: str) -> dict:
    stats = {}
    for iface in [if_a, if_b]:
        stats[iface] = {
            "tx_bytes":   _read_sys_counter(iface, "tx_bytes"),
            "rx_bytes":   _read_sys_counter(iface, "rx_bytes"),
            "tx_packets": _read_sys_counter(iface, "tx_packets"),
            "rx_packets": _read_sys_counter(iface, "rx_packets"),
            "tx_errors":  _read_sys_counter(iface, "tx_errors"),
            "rx_errors":  _read_sys_counter(iface, "rx_errors"),
            "tx_dropped": _read_sys_counter(iface, "tx_dropped"),
            "rx_dropped": _read_sys_counter(iface, "rx_dropped"),
            "qdiscs":     _parse_tc_stats(iface),
        }
    return stats

# ─── Profiles ─────────────────────────────────────────────────────────────────

def save_profile(name: str, cfg: LinkConfig) -> None:
    (PROFILES_DIR / f"{name}.json").write_text(json.dumps(cfg.to_dict(), indent=2))

def load_profile(name: str) -> LinkConfig:
    return LinkConfig.from_dict(json.loads((PROFILES_DIR / f"{name}.json").read_text()))

def list_profiles() -> list[str]:
    return [p.stem for p in sorted(PROFILES_DIR.glob("*.json"))]

_DEFAULT_QOS = [
    QoSClass(dscp=46, name="Telemetry",   priority=1, queue_limit=100),
    QoSClass(dscp=0,  name="Default",     priority=2, queue_limit=100),
    QoSClass(dscp=-1, name="Best Effort", priority=7, queue_limit=100),
]

_PRESETS: dict[str, LinkConfig] = {
    "Good Link": LinkConfig(
        forward=DirectionConfig(bw_kbps=10000, latency_ms=5,   jitter_ms=1,  loss_pct=0.0),
        reverse=DirectionConfig(bw_kbps=10000, latency_ms=5,   jitter_ms=1,  loss_pct=0.0),
        qos_classes=_DEFAULT_QOS,
    ),
    "Bad Link": LinkConfig(
        forward=DirectionConfig(bw_kbps=2000,  latency_ms=80,  jitter_ms=20, loss_pct=1.0),
        reverse=DirectionConfig(bw_kbps=2000,  latency_ms=80,  jitter_ms=20, loss_pct=1.0),
        qos_classes=_DEFAULT_QOS,
    ),
    "Satellite (Iridium Certus 200)": LinkConfig(
        mtu=576,
        forward=DirectionConfig(bw_kbps=200,   latency_ms=600, jitter_ms=50, loss_pct=0.5),
        reverse=DirectionConfig(bw_kbps=200,   latency_ms=600, jitter_ms=50, loss_pct=0.5),
        qos_classes=_DEFAULT_QOS,
    ),
    "Mobile (4G)": LinkConfig(
        forward=DirectionConfig(bw_kbps=5000,  latency_ms=40,  jitter_ms=15, loss_pct=0.2),
        reverse=DirectionConfig(bw_kbps=1000,  latency_ms=40,  jitter_ms=15, loss_pct=0.2),
        qos_classes=_DEFAULT_QOS,
    ),
}

def seed_builtin_profiles() -> None:
    """Write built-in profiles as JSON files if they don't already exist."""
    for name, cfg in _PRESETS.items():
        path = PROFILES_DIR / f"{name}.json"
        if not path.exists():
            path.write_text(json.dumps(cfg.to_dict(), indent=2))

seed_builtin_profiles()
