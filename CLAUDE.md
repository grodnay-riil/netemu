# NetEmu — Project Context for Claude

## What this project is
A Docker-containerized Linux network emulator that bridges two host NICs and
imposes configurable impairments (bandwidth, latency, jitter, packet loss,
corruption, reorder) using the Linux `tc netem` / `HTB` / `IFB` stack.
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
└── app.py              ← Streamlit UI (4 tabs: Impairments, QoS, Stats, Log)
└── profiles/           ← saved JSON link profiles (volume-mounted)
```

## Key design decisions
- **IFB trick** — `tc netem` only works on egress. Ingress traffic on each
  interface is mirrored to a virtual `ifb0`/`ifb1` device so netem can be
  applied to both directions.
- **Asymmetric mode** — forward (A→B) and reverse (B→A) can have independent
  impairment settings.
- **QoS mode** — when enabled, uses HTB as root qdisc with per-DSCP child
  classes, each with their own netem leaf. DSCP matched via `tc filter u32`.
- **Simple mode** — when QoS disabled, flat `netem` qdisc with optional `rate`
  clause (simpler, fewer tc commands).
- Profiles saved as JSON to `/app/profiles/` (volume-mounted to `./profiles/`
  on host).

## netemu_core.py — public API
```python
apply(cfg: LinkConfig) -> list[str]      # runs tc commands, returns log
reset(if_a, if_b) -> list[str]           # removes all qdiscs + IFB devices
get_stats(if_a, if_b) -> dict            # /sys counters + tc -s qdisc output
list_interfaces() -> list[str]           # non-loopback, non-IFB interfaces
save_profile(name, cfg) / load_profile(name) / list_profiles()
PRESETS: dict[str, LinkConfig]           # 3G, DSL, Satellite, Clean LAN
```

## app.py — UI structure
- **Sidebar**: interface picker (A/B), presets dropdown, profile save/load
- **Tab 1 — Impairments**: sliders for BW/latency/jitter/loss/corrupt/reorder,
  asymmetric toggle, Apply / Reset buttons, status bar
- **Tab 2 — QoS/DSCP**: editable dataframe of DSCP classes (bw%, priority,
  extra delay/loss), DSCP reference table
- **Tab 3 — Stats**: per-interface TX/RX counters + per-qdisc drop table,
  manual refresh button
- **Tab 4 — Command Log**: all tc/ip commands from last Apply/Reset

## How to run
```bash
docker compose up --build
# open http://localhost:8501
```

## Known potential issues / things to debug
- `_build_config_from_state()` calls `_direction_widgets()` which renders
  Streamlit widgets — if "widget rendered outside context" errors appear,
  move `cfg = _build_config_from_state(...)` inside the `tab_impair` block.
- Requires at least 2 non-loopback interfaces. For smoke testing without real
  NICs use: `ip link add dummy0 type dummy && ip link set dummy0 up`
- `tc` commands require the `sch_netem` and `sch_htb` kernel modules.
  If netem commands fail with "RTNETLINK: No such file", run:
  `modprobe sch_netem && modprobe sch_htb` on the host.
- IFB module: `modprobe ifb numifbs=2` may be needed on some hosts.

## Current status
Code written, not yet tested. First debug session starting now.
