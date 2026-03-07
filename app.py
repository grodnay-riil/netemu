"""
app.py — NetEmu Streamlit Web UI
Single page, no tabs, no sidebar.

Copyright (c) 2025 Skana Robotics Ltd. All rights reserved.
Proprietary and confidential. Unauthorized use prohibited.
"""

import streamlit as st
import pandas as pd
from netemu_core import (
    LinkConfig, DirectionConfig, QoSClass,
    apply, reset, bridge_only, get_stats,
    list_interfaces, list_profiles, load_profile, save_profile,
)

st.set_page_config(page_title="NetEmu", page_icon="🌐", layout="wide")
st.title("🌐 NetEmu — Network Emulator  |  Skana Robotics Ltd.")

# ─── Session state ────────────────────────────────────────────────────────────

def _init_state():
    defaults = {
        "applied": False,
        "log": [],
        "qos_editor_v": 0,
        "qos_classes": [
            {"dscp": 46, "name": "Telemetry",   "priority": 1, "min_kbps": 1, "max_kbps": 1000000, "queue_limit":  10},
            {"dscp":  0, "name": "Default",     "priority": 2, "min_kbps": 1, "max_kbps": 1000000, "queue_limit":  10},
            {"dscp": -1, "name": "Best Effort", "priority": 7, "min_kbps": 1, "max_kbps": 1000000, "queue_limit":  10},
        ],
        # widget-bound keys — set here for first run only
        "mtu": 1500,
        "fwd_bw": 0.0, "fwd_lat": 0, "fwd_jit": 0,
        "fwd_loss": 0.0, "fwd_corrupt": 0.0, "fwd_reorder": 0.0,
        "rev_bw": 0.0, "rev_lat": 0, "rev_jit": 0,
        "rev_loss": 0.0, "rev_corrupt": 0.0, "rev_reorder": 0.0,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

def _load_config_to_state(cfg: LinkConfig) -> None:
    """Stage a config for loading. Applied at the top of the next rerun before any widget renders."""
    import dataclasses
    st.session_state["_staged"] = {
        "mtu":         cfg.mtu,
        "fwd_bw":      round(cfg.forward.bw_kbps / 1000, 3),
        "fwd_lat":     cfg.forward.latency_ms,
        "fwd_jit":     cfg.forward.jitter_ms,
        "fwd_loss":    float(cfg.forward.loss_pct),
        "fwd_corrupt": float(cfg.forward.corrupt_pct),
        "fwd_reorder": float(cfg.forward.reorder_pct),
        "rev_bw":      round(cfg.reverse.bw_kbps / 1000, 3),
        "rev_lat":     cfg.reverse.latency_ms,
        "rev_jit":     cfg.reverse.jitter_ms,
        "rev_loss":    float(cfg.reverse.loss_pct),
        "rev_corrupt": float(cfg.reverse.corrupt_pct),
        "rev_reorder": float(cfg.reverse.reorder_pct),
        "qos_classes": [dataclasses.asdict(c) for c in cfg.qos_classes],
        "qos_editor_v": st.session_state.get("qos_editor_v", 0) + 1,
        "if_a": cfg.if_a,
        "if_b": cfg.if_b,
    }

_init_state()

# Auto-load "default" profile on first run of a new session
if "_startup_done" not in st.session_state:
    st.session_state["_startup_done"] = True
    if "default" in list_profiles():
        _load_config_to_state(load_profile("default"))

# Apply any staged config BEFORE any widget is rendered (Streamlit allows
# writing to widget-owned keys only before the widget runs in the current script execution)
if "_staged" in st.session_state:
    for k, v in st.session_state.pop("_staged").items():
        st.session_state[k] = v

# ─── Row 1: Interface picker | MTU | Profile save/load ───────────────────────

st.subheader("Configuration")
r1a, r1b, r1c, r1d = st.columns([2, 2, 2, 3])

ifaces = list_interfaces()
if len(ifaces) < 2:
    st.error("Need at least 2 network interfaces. Found: " + str(ifaces))
    st.stop()

if st.session_state.get("if_a") not in ifaces:
    st.session_state["if_a"] = ifaces[0]
if st.session_state.get("if_b") not in ifaces:
    st.session_state["if_b"] = ifaces[min(1, len(ifaces) - 1)]
if_a = r1a.selectbox("Interface A (Robot)", ifaces, key="if_a")
if_b = r1b.selectbox("Interface B (Operator)", ifaces, key="if_b")
if if_a == if_b:
    st.warning("Interface A and B must differ.")

mtu = r1c.number_input("MTU (bytes)", min_value=576, max_value=9000, step=1, key="mtu")

saved = list_profiles()
profile_name = r1d.text_input("Profile name", value="my-profile")
load_sel = r1d.selectbox("Profiles", ["—"] + saved) if saved else "—"
_bc1, _bc2, _bc3 = r1d.columns(3)
save_clicked = _bc1.button("💾 Save", use_container_width=True)
load_clicked = _bc2.button("📂 Load", use_container_width=True)
set_default_clicked = _bc3.button("⭐ Save as Default", use_container_width=True)

st.divider()

# ─── Row 2: Forward | Reverse impairment inputs ───────────────────────────────

def _dir_inputs(label: str, key: str) -> DirectionConfig:
    st.markdown(f"**{label}**")
    c1, c2, c3 = st.columns(3)
    bw      = c1.number_input("BW (Mbps, 0=∞)", min_value=0.0, max_value=10000.0,
                               step=0.001, format="%.3f", key=f"{key}_bw")
    lat     = c2.number_input("Latency (ms)",    min_value=0, max_value=60000,
                               step=1, key=f"{key}_lat")
    jit     = c3.number_input("Jitter (ms)",     min_value=0, max_value=10000,
                               step=1, key=f"{key}_jit")
    c4, c5, c6 = st.columns(3)
    loss    = c4.number_input("Loss (%)",        min_value=0.0, max_value=100.0,
                               step=0.1, format="%.1f", key=f"{key}_loss")
    corrupt = c5.number_input("Corrupt (%)",     min_value=0.0, max_value=100.0,
                               step=0.01, format="%.2f", key=f"{key}_corrupt")
    reorder = c6.number_input("Reorder (%)",     min_value=0.0, max_value=100.0,
                               step=0.1, format="%.1f", key=f"{key}_reorder")
    return DirectionConfig(bw_kbps=bw * 1000, latency_ms=lat, jitter_ms=jit,
                           loss_pct=loss, corrupt_pct=corrupt, reorder_pct=reorder)

col_fwd, col_rev = st.columns(2)
with col_fwd:
    fwd = _dir_inputs("➡️ Forward  (A → B)", "fwd")
with col_rev:
    rev = _dir_inputs("⬅️ Reverse  (B → A)", "rev")

with st.expander("QoS / DSCP Classes", expanded=False):
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
        key=f"qos_editor_{st.session_state['qos_editor_v']}",
    )
    st.session_state["qos_classes"] = edited.to_dict("records")

# ─── Row 4: Apply / Reset + status ───────────────────────────────────────────

qos_classes = [QoSClass(**c) for c in st.session_state["qos_classes"]]
cfg = LinkConfig(if_a=if_a, if_b=if_b, mtu=mtu, forward=fwd, reverse=rev, qos_classes=qos_classes)

if save_clicked and profile_name:
    save_profile(profile_name, cfg)
    st.success(f"Saved '{profile_name}'")
if set_default_clicked:
    save_profile("default", cfg)
    st.success("Saved as default — will auto-load on next session start")
if load_clicked and load_sel != "—":
    _load_config_to_state(load_profile(load_sel))
    st.rerun()

col_apply, col_bridge, col_reset, col_status = st.columns([1, 1, 1, 3])
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

with col_bridge:
    if st.button("🌉 Bridge Only", use_container_width=True):
        if if_a == if_b:
            st.error("Interfaces must differ!")
        else:
            with st.spinner("Bridging…"):
                try:
                    st.session_state["log"] = bridge_only(if_a, if_b, mtu)
                    st.session_state["applied"] = False
                    st.success("Bridge up — no impairments!")
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

with st.expander("Interface Statistics", expanded=False):
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

with st.expander("Command Log", expanded=False):
    if st.session_state["log"]:
        st.code("\n".join(st.session_state["log"]), language="bash")
    else:
        st.info("No commands run yet. Press **Apply** or **Reset**.")
