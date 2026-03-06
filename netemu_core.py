"""
netemu_core.py — Linux tc/netem control plane for NetEmu
Requires: iproute2, privileged container (NET_ADMIN + NET_RAW)
"""

import subprocess
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

PROFILES_DIR = Path("/app/profiles")
PROFILES_DIR.mkdir(parents=True, exist_ok=True)

# ─── Data Models ─────────────────────────────────────────────────────────────

@dataclass
class QoSClass:
    dscp:       int
    name:       str
    bw_pct:     int
    priority:   int
    latency_ms: int = 0
    loss_pct:   float = 0.0

@dataclass
class DirectionConfig:
    bw_kbps:     int   = 0
    latency_ms:  int   = 0
    jitter_ms:   int   = 0
    loss_pct:    float = 0.0
    corrupt_pct: float = 0.0
    reorder_pct: float = 0.0

@dataclass
class LinkConfig:
    if_a:        str  = "eth0"
    if_b:        str  = "eth1"
    asymmetric:  bool = False
    forward:     DirectionConfig = field(default_factory=DirectionConfig)
    reverse:     DirectionConfig = field(default_factory=DirectionConfig)
    qos_enabled: bool = False
    qos_classes: list[QoSClass] = field(default_factory=list)

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
            asymmetric=d.get("asymmetric", False),
            forward=fwd, reverse=rev,
            qos_enabled=d.get("qos_enabled", False),
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
        if name == "lo" or name.startswith("ifb"):
            continue
        ifaces.append(name)
    return ifaces

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

def _setup_ifb(ifb: str, src_if: str, log: list[str]) -> None:
    cmds = [
        f"ip link add {ifb} type ifb",
        f"ip link set {ifb} up",
        f"tc qdisc add dev {src_if} ingress",
        (f"tc filter add dev {src_if} parent ffff: protocol all u32 "
         f"match u32 0 0 action mirred egress redirect dev {ifb}"),
    ]
    _run_many(cmds, log)

def _apply_simple(iface: str, ifb: str, d: DirectionConfig, log: list[str]) -> None:
    netem = _netem_params(d)
    rate_clause = f"rate {d.bw_kbps}kbit" if d.bw_kbps > 0 else ""
    cmds = [
        f"tc qdisc del dev {iface} root",
        f"tc qdisc add dev {iface} root handle 1: netem {netem} {rate_clause}".strip(),
        f"tc qdisc del dev {ifb} root",
        f"tc qdisc add dev {ifb} root handle 1: netem {netem} {rate_clause}".strip(),
    ]
    _run_many(cmds, log)

def _apply_qos(iface: str, ifb: str, d: DirectionConfig,
               classes: list[QoSClass], log: list[str]) -> None:
    def _build(dev: str) -> list[str]:
        cmds = [f"tc qdisc del dev {dev} root"]
        total_bw = f"{d.bw_kbps}kbit" if d.bw_kbps > 0 else "1gbit"
        cmds.append(f"tc qdisc add dev {dev} root handle 1: htb default 99")
        cmds.append(
            f"tc class add dev {dev} parent 1: classid 1:1 htb rate {total_bw} ceil {total_bw}"
        )
        for i, cls in enumerate(classes, start=10):
            cls_bw = f"{int(d.bw_kbps * cls.bw_pct / 100)}kbit" if d.bw_kbps > 0 \
                     else f"{cls.bw_pct}mbit"
            handle = i
            cmds.append(
                f"tc class add dev {dev} parent 1:1 classid 1:{handle} "
                f"htb rate {cls_bw} ceil {total_bw} prio {cls.priority}"
            )
            extra_d = DirectionConfig(
                latency_ms=d.latency_ms + cls.latency_ms,
                jitter_ms=d.jitter_ms,
                loss_pct=max(d.loss_pct, cls.loss_pct),
                corrupt_pct=d.corrupt_pct,
            )
            netem = _netem_params(extra_d)
            cmds.append(
                f"tc qdisc add dev {dev} parent 1:{handle} handle {handle}: netem {netem}"
            )
            tos_val = cls.dscp << 2
            cmds.append(
                f"tc filter add dev {dev} parent 1: protocol ip prio {cls.priority} "
                f"u32 match ip tos {tos_val} 0xfc flowid 1:{handle}"
            )
        cmds.append(
            f"tc class add dev {dev} parent 1:1 classid 1:99 htb rate 1mbit ceil {total_bw} prio 7"
        )
        be_netem = _netem_params(d)
        cmds.append(f"tc qdisc add dev {dev} parent 1:99 handle 99: netem {be_netem}")
        return cmds

    _run_many(_build(iface), log)
    _run_many(_build(ifb), log)

# ─── Public API ───────────────────────────────────────────────────────────────

def apply(cfg: LinkConfig) -> list[str]:
    log: list[str] = []
    fwd = cfg.forward
    rev = cfg.reverse if cfg.asymmetric else cfg.forward
    ifb_a, ifb_b = "ifb0", "ifb1"

    for iface in [cfg.if_a, cfg.if_b, ifb_a, ifb_b]:
        _run(f"tc qdisc del dev {iface} root", check=False)
        _run(f"tc qdisc del dev {iface} ingress", check=False)
    for ifb in [ifb_a, ifb_b]:
        _run(f"ip link set {ifb} down", check=False)
        _run(f"ip link del {ifb}", check=False)

    _setup_ifb(ifb_a, cfg.if_a, log)
    _setup_ifb(ifb_b, cfg.if_b, log)

    if cfg.qos_enabled and cfg.qos_classes:
        _apply_qos(cfg.if_a, ifb_b, fwd, cfg.qos_classes, log)
        _apply_qos(cfg.if_b, ifb_a, rev, cfg.qos_classes, log)
    else:
        _apply_simple(cfg.if_a, ifb_b, fwd, log)
        _apply_simple(cfg.if_b, ifb_a, rev, log)

    return log

def reset(if_a: str, if_b: str) -> list[str]:
    log: list[str] = []
    for iface in [if_a, if_b]:
        _run_many([
            f"tc qdisc del dev {iface} root",
            f"tc qdisc del dev {iface} ingress",
        ], log)
    for ifb in ["ifb0", "ifb1"]:
        _run_many([
            f"tc qdisc del dev {ifb} root",
            f"ip link set {ifb} down",
            f"ip link del {ifb}",
        ], log)
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
        m = re.match(r"(\d+) bytes? requeued (\d+)", line)
        if m and current:
            current["requeued"] = int(m.group(2))
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

PRESETS: dict[str, LinkConfig] = {
    "3G Mobile": LinkConfig(
        forward=DirectionConfig(bw_kbps=3000, latency_ms=100, jitter_ms=20, loss_pct=0.5),
        reverse=DirectionConfig(bw_kbps=1000, latency_ms=100, jitter_ms=20, loss_pct=0.5),
        asymmetric=True,
    ),
    "DSL": LinkConfig(
        forward=DirectionConfig(bw_kbps=8000, latency_ms=20, jitter_ms=3, loss_pct=0.1),
        reverse=DirectionConfig(bw_kbps=1000, latency_ms=20, jitter_ms=3, loss_pct=0.1),
        asymmetric=True,
    ),
    "Lossy Satellite": LinkConfig(
        forward=DirectionConfig(bw_kbps=5000, latency_ms=600, jitter_ms=50, loss_pct=2.0),
        reverse=DirectionConfig(bw_kbps=2000, latency_ms=600, jitter_ms=50, loss_pct=2.0),
        asymmetric=True,
    ),
    "Clean LAN": LinkConfig(
        forward=DirectionConfig(bw_kbps=0, latency_ms=1, jitter_ms=0, loss_pct=0.0),
    ),
}
