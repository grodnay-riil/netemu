# NetEmu — Network Emulator

Copyright (c) 2025 Skana Robotics Ltd. All rights reserved.

A Docker-containerized Linux network emulator that bridges two host NICs and
imposes configurable impairments using `tc netem` with HTB (Hierarchical Token
Bucket) for per-DSCP QoS. Designed for testing robot applications under
realistic satellite and cellular link conditions.

```
Robot ── if_a ──[ br0 ]── if_b ── Operator
            │                  │
       B→A shaping         A→B shaping
       HTB + netem         HTB + netem
```

Controlled via a Streamlit web UI on port 8501.

---

## Requirements

- Docker with Compose
- Host kernel modules: `sch_netem`, `sch_htb` (usually loaded by default)
- At least 2 physical or virtual network interfaces

---

## Running

```bash
docker compose up --build
```

Open [http://localhost:8501](http://localhost:8501).

Remark: The container is automatically run with `privileged: true` and `network_mode: host` to manipulate


---

## UI overview

| Section            | Description                                                       |
| ------------------ | ----------------------------------------------------------------- |
| Interface A / B    | Pick the two NICs to bridge                                       |
| MTU                | Applied to both interfaces                                        |
| Forward (A→B)      | Bandwidth, latency, jitter, loss, corrupt, reorder                |
| Reverse (B→A)      | Same, independently configurable                                  |
| QoS / DSCP Classes | Per-DSCP HTB class with min/max BW, priority, queue depth         |
| Apply              | Create bridge + install tc qdiscs                                 |
| Bridge Only        | Create bridge with no impairments (passthrough)                   |
| Reset              | Remove all qdiscs and tear down bridge                            |
| Profiles           | Save / load named configurations; auto-loads `default` on startup |

---

## Built-in profiles

| Profile                        | BW       | Latency | Jitter | Loss | MTU  |
| ------------------------------ | -------- | ------- | ------ | ---- | ---- |
| Good Link                      | 10 Mbps  | 5 ms    | 1 ms   | 0%   | 1500 |
| Bad Link                       | 2 Mbps   | 80 ms   | 20 ms  | 1%   | 1500 |
| Satellite (Iridium Certus 200) | 200 kbps | 600 ms  | 50 ms  | 0.5% | 576  |
| Mobile (4G)                    | 5/1 Mbps | 40 ms   | 15 ms  | 0.2% | 1500 |

Profiles are seeded as JSON to `./profiles/` on first run. Edit or delete them
freely — built-in profiles are only written if the file doesn't exist.

To set a startup default: configure the link and press **Save as Default**.

---

## QoS / DSCP

Traffic is always shaped through an HTB hierarchy with per-DSCP leaf qdiscs.
Untagged traffic falls to the catch-all class (DSCP = -1).

Default classes:

| Class       | DSCP     | Priority    | Queue   |
| ----------- | -------- | ----------- | ------- |
| Telemetry   | 46 (EF)  | 1 (highest) | 10 pkts |
| Default     | 0        | 2           | 10 pkts |
| Best Effort | -1 (any) | 7 (lowest)  | 10 pkts |

The QoS table is fully editable in the UI. Rows can be added or removed.

---

## Profiles directory

`./profiles/` is volume-mounted into the container at `/app/profiles/`.
Profiles are plain JSON files — safe to version-control or copy between hosts.

---

## Development mode (source file hot-reload)

```bash
docker compose --profile dev up --build netemu-dev
```
Mounts `app.py` and `netemu_core.py` from the host so Streamlit reloads on save.

---

## Kernel module check

If `tc` commands fail with `RTNETLINK: No such file`:

```bash
modprobe sch_netem
modprobe sch_htb
```

## Smoke test without physical NICs

```bash
sudo ip link add dummy0 type dummy && sudo ip link set dummy0 up
sudo ip link add dummy1 type dummy && sudo ip link set dummy1 up
```

---

## License

Proprietary — see [LICENSE](LICENSE).
Third-party attributions — see [NOTICE](NOTICE).
