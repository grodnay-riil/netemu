"""
app.py — NetEmu Streamlit Web UI
Single page, no tabs, no sidebar.
"""

import streamlit as st
import pandas as pd
from netemu_core import (
    LinkConfig, DirectionConfig, QoSClass,
    apply, reset, get_stats,
    list_interfaces, list_profiles, load_profile, save_profile,
    PRESETS,
)

st.set_page_config(page_title="NetEmu", page_icon="🌐", layout="wide")
st.title("🌐 NetEmu — Network Emulator")

def _init_state():
    defaults = {
        "applied": False,
        "log": [],
        "qos_classes": [
            {"dscp": 46, "name": "Telemetry",   "priority": 1, "min_kbps": 1, "max_kbps": 1000000, "queue_limit":  10},
            {"dscp":  0, "name": "Default",     "priority": 2, "min_kbps": 1, "max_kbps": 1000000, "queue_limit":  50},
            {"dscp": -1, "name": "Best Effort", "priority": 7, "min_kbps": 1, "max_kbps": 1000000, "queue_limit": 100},
        ],
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_state()

# ─── Row 1: Interface picker | Preset selector | Profile save/load ────────────

st.subheader("Configuration")
r1a, r1b, r1c, r1d, r1e, r1f = st.columns([2, 2, 2, 2, 2, 2])

ifaces = list_interfaces()
if len(ifaces) < 2:
    st.error("Need at least 2 network interfaces. Found: " + str(ifaces))
    st.stop()

if_a = r1a.selectbox("Interface A (Robot)", ifaces, index=0)
if_b = r1b.selectbox("Interface B (Operator)", ifaces, index=min(1, len(ifaces) - 1))
if if_a == if_b:
    st.warning("Interface A and B must differ.")

mtu = r1c.number_input("MTU (bytes)", min_value=576, max_value=9000, value=1500, step=1)

preset_names = ["— select —"] + list(PRESETS.keys())
preset_sel = r1d.selectbox("Preset", preset_names)
if r1d.button("Load Preset", use_container_width=True) and preset_sel != "— select —":
    cfg = PRESETS[preset_sel]
    cfg.if_a = if_a
    cfg.if_b = if_b
    st.session_state["_preset"] = cfg
    st.rerun()

profile_name = r1e.text_input("Profile name", value="my-profile")
saved = list_profiles()
load_sel = r1e.selectbox("Saved profiles", ["—"] + saved) if saved else "—"
save_clicked = r1f.button("💾 Save Profile", use_container_width=True)
load_clicked = r1f.button("📂 Load Profile", use_container_width=True)

st.divider()

# ─── Row 2: Forward | Reverse impairment inputs ───────────────────────────────

preset = st.session_state.pop("_preset", None)
fp = preset.forward if preset else None
rp = preset.reverse if preset else None

def _dir_inputs(label: str, key: str, p: DirectionConfig | None) -> DirectionConfig:
    d = p or DirectionConfig()
    st.markdown(f"**{label}**")
    c1, c2, c3 = st.columns(3)
    bw  = c1.number_input("BW (Mbps, 0=∞)", min_value=0.0, max_value=10000.0,
                           value=round(d.bw_kbps / 1000, 3), step=0.001, format="%.3f", key=f"{key}_bw")
    lat = c2.number_input("Latency (ms)",   min_value=0, max_value=60000,
                           value=d.latency_ms, step=1, key=f"{key}_lat")
    jit = c3.number_input("Jitter (ms)",    min_value=0, max_value=10000,
                           value=d.jitter_ms, step=1, key=f"{key}_jit")
    c4, c5, c6 = st.columns(3)
    loss    = c4.number_input("Loss (%)",    min_value=0.0, max_value=100.0,
                               value=float(d.loss_pct), step=0.1, format="%.1f", key=f"{key}_loss")
    corrupt = c5.number_input("Corrupt (%)", min_value=0.0, max_value=100.0,
                               value=float(d.corrupt_pct), step=0.01, format="%.2f", key=f"{key}_corrupt")
    reorder = c6.number_input("Reorder (%)", min_value=0.0, max_value=100.0,
                               value=float(d.reorder_pct), step=0.1, format="%.1f", key=f"{key}_reorder")
    return DirectionConfig(bw_kbps=bw * 1000, latency_ms=lat, jitter_ms=jit,
                           loss_pct=loss, corrupt_pct=corrupt, reorder_pct=reorder)

col_fwd, col_rev = st.columns(2)
with col_fwd:
    fwd = _dir_inputs("➡️ Forward  (A → B)", "fwd", fp)
with col_rev:
    rev = _dir_inputs("⬅️ Reverse  (B → A)", "rev", rp)

st.divider()

# ─── Row 3: QoS DSCP class table ─────────────────────────────────────────────

st.subheader("QoS / DSCP Classes")
df = pd.DataFrame(st.session_state["qos_classes"])
edited = st.data_editor(
    df,
    column_config={
        "dscp":       st.column_config.NumberColumn("DSCP (-1=any)", min_value=-1, max_value=63),
        "name":       st.column_config.TextColumn("Name"),
        "priority":   st.column_config.NumberColumn("Priority (1=high)", min_value=1, max_value=7),
        "min_kbps":   st.column_config.NumberColumn("Min BW (kbps)", min_value=1),
        "max_kbps":   st.column_config.NumberColumn("Max BW (kbps)", min_value=1),
        "queue_limit": st.column_config.NumberColumn("Queue (pkts)", min_value=1),
    },
    num_rows="dynamic",
    use_container_width=True,
    key="qos_editor",
)
st.session_state["qos_classes"] = edited.to_dict("records")


st.divider()

# ─── Row 4: Apply / Reset + status ───────────────────────────────────────────

qos_classes = [QoSClass(**c) for c in st.session_state["qos_classes"]]
cfg = LinkConfig(if_a=if_a, if_b=if_b, mtu=mtu, forward=fwd, reverse=rev, qos_classes=qos_classes)

if save_clicked and profile_name:
    save_profile(profile_name, cfg)
    st.success(f"Saved '{profile_name}'")
if load_clicked and load_sel != "—":
    loaded = load_profile(load_sel)
    loaded.if_a = if_a
    loaded.if_b = if_b
    st.session_state["_preset"] = loaded
    st.rerun()

col_apply, col_reset, col_status = st.columns([1, 1, 4])
with col_apply:
    if st.button("✅ Apply", type="primary", use_container_width=True):
        if if_a == if_b:
            st.error("Interfaces must differ!")
        else:
            with st.spinner("Applying…"):
                try:
                    st.session_state["log"] = apply(cfg)
                    st.session_state["applied"] = True
                    st.success("Applied!")
                except Exception as e:
                    st.error(f"Error: {e}")

with col_reset:
    if st.button("🔴 Reset", use_container_width=True):
        with st.spinner("Resetting…"):
            st.session_state["log"] = reset(if_a, if_b)
            st.session_state["applied"] = False
            st.success("Reset!")

with col_status:
    if st.session_state["applied"]:
        st.info(
            f"✅ Active on **{if_a}** ↔ **{if_b}**  |  "
            f"Fwd: {cfg.forward.latency_ms}ms / {cfg.forward.loss_pct}% loss / "
            f"{cfg.forward.bw_kbps / 1000 or '∞'} Mbps  |  "
            f"Rev: {cfg.reverse.latency_ms}ms / {cfg.reverse.loss_pct}% loss / "
            f"{cfg.reverse.bw_kbps / 1000 or '∞'} Mbps"
        )
    else:
        st.warning("⬜ No impairments active")

st.divider()

# ─── Row 5: Stats ─────────────────────────────────────────────────────────────

st.subheader("Interface Statistics")
if st.button("🔄 Refresh Stats"):
    with st.spinner("Reading stats…"):
        try:
            st.session_state["last_stats"] = get_stats(if_a, if_b)
        except Exception as e:
            st.error(f"Stats error: {e}")

if "last_stats" not in st.session_state:
    st.info("Press **Refresh Stats** to load current counters.")
else:
    for iface, s in st.session_state["last_stats"].items():
        st.markdown(f"#### `{iface}`")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("TX Bytes",   f"{s['tx_bytes']:,}")
        c2.metric("RX Bytes",   f"{s['rx_bytes']:,}")
        c3.metric("TX Packets", f"{s['tx_packets']:,}")
        c4.metric("RX Packets", f"{s['rx_packets']:,}")
        c1.metric("TX Errors",  s["tx_errors"])
        c2.metric("RX Errors",  s["rx_errors"])
        c3.metric("TX Dropped", s["tx_dropped"])
        c4.metric("RX Dropped", s["rx_dropped"])
        if s["qdiscs"]:
            st.dataframe(pd.DataFrame([
                {"Type": q.get("type"), "Handle": q.get("handle"),
                 "Bytes": q.get("bytes", 0), "Packets": q.get("packets", 0),
                 "Dropped": q.get("dropped", 0), "Overlimits": q.get("overlimits", 0)}
                for q in s["qdiscs"]
            ]), use_container_width=True)
        st.divider()

# ─── Row 6: Command log ───────────────────────────────────────────────────────

st.subheader("Command Log")
if st.session_state["log"]:
    st.code("\n".join(st.session_state["log"]), language="bash")
else:
    st.info("No commands run yet. Press **Apply** or **Reset**.")
