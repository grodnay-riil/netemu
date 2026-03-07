# NetEmu — Project Context for Claude

## What this project is
A Docker-containerized Linux network emulator that bridges two host NICs and
imposes configurable impairments (bandwidth, latency, jitter, packet loss,
corruption, reorder) using `tc netem` with HTB (Hierarchical Token Bucket) for
per-DSCP QoS and MTU settings. Designed as a simple, self-contained tool for testing applications
under realistic network conditions.

  Robot ── if_a ──[br0]── if_b ── Operator
              │                │
         B→A shaping      A→B shaping
         HTB + netem      HTB + netem

Controlled via a **Streamlit** web UI on port 8501.

## Stack
- **Python 3.11**
- **Streamlit** — web UI, zero JS, single-page app
- **iproute2** (`tc`, `ip`) — all network manipulation via subprocess calls
- **Docker** — `privileged: true`, `network_mode: host` (required for tc/ip)
- No REST API, no CLI, no database, no frontend framework

## File structure
```
netemu/
├── CLAUDE.md           ← you are here
├── Dockerfile
├── docker-compose.yml
├── requirements.txt    ← streamlit, pandas
├── netemu_core.py      ← all tc/ip logic (apply, reset, get_stats, profiles)
└── app.py              ← Streamlit UI (single page: impairments, QoS, stats, log)
└── profiles/           ← saved JSON link profiles (volume-mounted)
```

## Key design decisions
- **Bridge-based** — `if_a` and `if_b` are added as ports to a Linux bridge
  (`br0`). The host acts as a transparent L2 bump-in-the-wire.
- **Egress-only shaping** — because the bridge forwards traffic out the other
  port, shaping egress of each physical interface is sufficient:
  - netem on egress of `if_b` = A→B impairments
  - netem on egress of `if_a` = B→A impairments
- **Always asymmetric** — forward (A→B) and reverse (B→A) always have
  independent impairment settings.
- **Always QoS** — HTB root qdisc with per-DSCP child classes, each with their
  own netem leaf. DSCP matched via `tc filter u32`. Untagged packets fall to
  default class `1:99`.
- **Use case** — remote control robot: telemetry/control commands (DSCP 46,
  high priority) share the link with video (untagged, best-effort). Default
  QoS table has three classes: Telemetry (DSCP 46, prio 1, queue=10),
  Default (DSCP 0, prio 2, queue=50), Best Effort (DSCP -1 = catch-all/1:99,
  prio 7, queue=100). Per-class min/max BW configurable (default 1 kbps /
  1 Gbps = pure priority). Table is editable. DSCP -1 means no filter rule —
  maps to HTB default class 1:99.
- Profiles saved as JSON to `/app/profiles/` (volume-mounted to `./profiles/`
  on host).

## Parameter scope

| Parameter | Scope | Mechanism |
|---|---|---|
| Bandwidth cap | Per NIC (shared by all classes) | HTB class `1:1` rate/ceil |
| Latency, jitter | Per NIC (applied to all classes) | netem on each class leaf |
| Loss, corrupt, reorder | Per NIC (applied to all classes) | netem on each class leaf |
| MTU | Per NIC (both interfaces same value) | `ip link set mtu` |
| Priority | Per class | HTB class `prio` |
| Min / Max BW | Per class | HTB class `rate` / `ceil` |
| Queue depth | Per class | netem `limit` |

## netemu_core.py — data models
```python
@dataclass
class QoSClass:
    dscp: int          # DSCP value (0–63), or -1 = catch-all (HTB default class 1:99)
    name: str          # display name
    priority: int      # HTB priority (1=highest)
    min_kbps: int      # HTB rate (guaranteed minimum), default 1 kbps
    max_kbps: int      # HTB ceil (maximum), default 1 Gbps
    queue_limit: int   # netem queue depth in packets before drop, default 1000

@dataclass
class DirectionConfig:
    bw_kbps: float     # 0 = unlimited
    latency_ms: int
    jitter_ms: int
    loss_pct: float
    corrupt_pct: float
    reorder_pct: float

@dataclass
class LinkConfig:
    if_a: str              # Robot-side NIC
    if_b: str              # Operator-side NIC
    mtu: int               # MTU applied to both interfaces (default 1500)
    forward: DirectionConfig   # A→B (operator receives)
    reverse: DirectionConfig   # B→A (robot receives)
    qos_classes: list[QoSClass]
```

## netemu_core.py — public API
```python
apply(cfg: LinkConfig) -> list[str]      # creates bridge, runs tc commands, returns log
reset(if_a, if_b) -> list[str]           # removes qdiscs + tears down bridge
get_stats(if_a, if_b) -> dict            # /sys counters + tc -s qdisc output
list_interfaces() -> list[str]           # non-loopback, non-bridge interfaces
save_profile(name, cfg) / load_profile(name) / list_profiles()
PRESETS: dict[str, LinkConfig]           # Good Link, Bad Link, Satellite, Mobile
```

## app.py — UI structure
Single page, no tabs, no sidebar, no collapsible sections.
- **Row 1**: Interface picker (A/B) | MTU input | Preset selector | Profile save/load
- **Row 2**: Forward (A→B) impairment inputs | Reverse (B→A) impairment inputs (side by side)
- **Row 3**: QoS DSCP class table (always visible)
- **Row 4**: Apply / Reset buttons + status bar
- **Row 5**: Per-interface TX/RX stats (always visible, manual refresh button)
- **Row 6**: Command log (always visible)

## How to run
```bash
docker compose up --build
# open http://localhost:8501
```

## Known potential issues / things to debug
- Requires at least 2 non-loopback interfaces. For smoke testing without real
  NICs use: `ip link add dummy0 type dummy && ip link set dummy0 up`
- `tc` commands require the `sch_netem` and `sch_htb` kernel modules.
  If netem commands fail with "RTNETLINK: No such file", run:
  `modprobe sch_netem && modprobe sch_htb` on the host.
- Bridge interfaces (`br0`) should be excluded from the interface picker in the
  UI (`list_interfaces()` filters them out).

## Current status
- Core (`netemu_core.py`): bridge-based, always asymmetric, always QoS — implemented
- UI (`app.py`): single-page, no tabs, no sidebar — implemented
- PRESETS: hardcoded (Good Link, Bad Link, Satellite, Mobile 4G) — not yet tuned for robot use case
- Streamlit uses poll-based file watcher (`--server.fileWatcherType=poll`) for auto-reload on volume mounts
- Not yet tested on real hardware
