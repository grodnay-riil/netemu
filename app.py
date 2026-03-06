"""
app.py — NetEmu Streamlit Web UI
"""

import streamlit as st
import pandas as pd
from netemu_core import (
    LinkConfig, DirectionConfig, QoSClass,
    apply, reset, get_stats,
    list_interfaces, list_profiles, load_profile, save_profile,
    PRESETS,
)

st.set_page_config(
    page_title="NetEmu",
    page_icon="🌐",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("🌐 NetEmu — Network Emulator")

def _init_state():
    defaults = {
        "applied": False,
        "log": [],
        "qos_classes": [
            {"dscp": 46, "name": "EF (VoIP)",   "bw_pct": 30, "priority": 1, "latency_ms": 0, "loss_pct": 0.0},
            {"dscp": 34, "name": "AF41 (Video)", "bw_pct": 30, "priority": 2, "latency_ms": 0, "loss_pct": 0.0},
            {"dscp": 0,  "name": "BE (Default)", "bw_pct": 40, "priority": 3, "latency_ms": 0, "loss_pct": 0.0},
        ],
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_state()

# ─── Sidebar ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("🔌 Interfaces")
    ifaces = list_interfaces()
    if len(ifaces) < 2:
        st.error("Need at least 2 network interfaces. Found: " + str(ifaces))
        st.stop()

    if_a = st.selectbox("Interface A (LAN side)", ifaces, index=0)
    if_b = st.selectbox("Interface B (WAN side)", ifaces, index=min(1, len(ifaces) - 1))

    if if_a == if_b:
        st.warning("Interface A and B must differ.")

    st.divider()
    st.header("📁 Presets & Profiles")

    preset_names = ["— select —"] + list(PRESETS.keys())
    preset_sel = st.selectbox("Built-in preset", preset_names)
    if st.button("Load Preset") and preset_sel != "— select —":
        cfg = PRESETS[preset_sel]
        cfg.if_a = if_a
        cfg.if_b = if_b
        st.session_state["_preset"] = cfg
        st.rerun()

    saved = list_profiles()
    profile_name = st.text_input("Profile name", value="my-profile")
    col1, col2 = st.columns(2)
    with col1:
        save_clicked = st.button("💾 Save")
    with col2:
        load_clicked = st.button("📂 Load")

    if saved:
        load_sel = st.selectbox("Saved profiles", ["—"] + saved)
    else:
        load_sel = "—"

    st.divider()
    st.caption("NetEmu v1.0 · privileged container required")

# ─── Direction widget helper ──────────────────────────────────────────────────

def _direction_widgets(label: str, key_prefix: str,
                       preset: DirectionConfig | None = None) -> DirectionConfig:
    p = preset or DirectionConfig()
    with st.expander(label, expanded=True):
        bw      = st.slider("Bandwidth (Mbps, 0 = unlimited)", 0, 1000,
                             int(p.bw_kbps / 1000), key=f"{key_prefix}_bw")
        lat     = st.slider("Latency (ms)",     0, 2000,  p.latency_ms,       key=f"{key_prefix}_lat")
        jit     = st.slider("Jitter (ms)",       0, 500,   p.jitter_ms,        key=f"{key_prefix}_jit")
        loss    = st.slider("Packet Loss (%)",   0.0, 100.0, float(p.loss_pct),
                             step=0.1, key=f"{key_prefix}_loss")
        corrupt = st.slider("Corruption (%)",    0.0, 10.0,  float(p.corrupt_pct),
                             step=0.01, key=f"{key_prefix}_corrupt")
        reorder = st.slider("Reorder (%)",       0.0, 50.0,  float(p.reorder_pct),
                             step=0.1, key=f"{key_prefix}_reorder")
    return DirectionConfig(
        bw_kbps=bw * 1000,
        latency_ms=lat, jitter_ms=jit,
        loss_pct=loss, corrupt_pct=corrupt, reorder_pct=reorder,
    )

# ─── Tabs ─────────────────────────────────────────────────────────────────────

tab_impair, tab_qos, tab_stats, tab_log = st.tabs(
    ["⚡ Impairments", "🏷️ QoS / DSCP", "📊 Stats", "🖥️ Command Log"]
)

with tab_impair:
    asym = st.toggle("Asymmetric (different forward / reverse settings)", key="asymmetric")

    preset = st.session_state.pop("_preset", None)
    fwd_preset = preset.forward if preset else None
    rev_preset = preset.reverse if preset else None

    fwd = _direction_widgets("➡️ Forward  (A → B)", "fwd", fwd_preset)
    rev = _direction_widgets("⬅️ Reverse  (B → A)", "rev", rev_preset) if asym else fwd

    # Build config here, inside the tab, after widgets are rendered
    qos_enabled = st.session_state.get("qos_enabled", False)
    qos_classes = [QoSClass(**c) for c in st.session_state["qos_classes"]] if qos_enabled else []
    cfg = LinkConfig(
        if_a=if_a, if_b=if_b, asymmetric=asym,
        forward=fwd, reverse=rev,
        qos_enabled=qos_enabled, qos_classes=qos_classes,
    )

    # Handle profile save/load (buttons were rendered in sidebar)
    if save_clicked and profile_name:
        save_profile(profile_name, cfg)
        st.sidebar.success(f"Saved '{profile_name}'")
    if load_clicked and load_sel != "—":
        loaded = load_profile(load_sel)
        loaded.if_a = if_a
        loaded.if_b = if_b
        st.session_state["_preset"] = loaded
        st.rerun()

    st.divider()
    col_apply, col_reset, col_status = st.columns([1, 1, 4])
    with col_apply:
        if st.button("✅ Apply", type="primary", use_container_width=True):
            if if_a == if_b:
                st.error("Interfaces must differ!")
            else:
                with st.spinner("Applying tc rules…"):
                    try:
                        log = apply(cfg)
                        st.session_state["log"] = log
                        st.session_state["applied"] = True
                        st.success("Applied!")
                    except Exception as e:
                        st.error(f"Error: {e}")

    with col_reset:
        if st.button("🔴 Reset", use_container_width=True):
            with st.spinner("Resetting…"):
                log = reset(if_a, if_b)
                st.session_state["log"] = log
                st.session_state["applied"] = False
                st.success("Reset!")

    with col_status:
        if st.session_state["applied"]:
            st.info(
                f"✅ Active on **{if_a}** ↔ **{if_b}**  |  "
                f"{'Asymmetric' if cfg.asymmetric else 'Symmetric'}  |  "
                f"Latency: {cfg.forward.latency_ms}ms  |  "
                f"Loss: {cfg.forward.loss_pct}%  |  "
                f"BW: {cfg.forward.bw_kbps // 1000 or '∞'} Mbps"
            )
        else:
            st.warning("⬜ No impairments active")

with tab_qos:
    st.subheader("QoS / DSCP Classes")
    qos_on = st.toggle("Enable QoS (HTB + per-DSCP netem)", key="qos_enabled")

    if qos_on:
        st.caption("Traffic is classified by DSCP field into separate HTB classes, "
                   "each with independent bandwidth share and netem parameters.")
        df = pd.DataFrame(st.session_state["qos_classes"])
        edited = st.data_editor(
            df,
            column_config={
                "dscp":       st.column_config.NumberColumn("DSCP (0–63)", min_value=0, max_value=63),
                "name":       st.column_config.TextColumn("Name"),
                "bw_pct":     st.column_config.NumberColumn("BW %", min_value=1, max_value=100),
                "priority":   st.column_config.NumberColumn("Priority (1=high)", min_value=1, max_value=7),
                "latency_ms": st.column_config.NumberColumn("Extra Delay (ms)", min_value=0),
                "loss_pct":   st.column_config.NumberColumn("Extra Loss %", min_value=0.0, max_value=100.0),
            },
            num_rows="dynamic",
            use_container_width=True,
            key="qos_editor",
        )
        st.session_state["qos_classes"] = edited.to_dict("records")

        total_bw = sum(r["bw_pct"] for r in st.session_state["qos_classes"])
        if total_bw != 100:
            st.warning(f"BW% sums to {total_bw}% — should equal 100%")

        with st.expander("📖 Common DSCP values"):
            st.table(pd.DataFrame([
                {"DSCP": 46, "Name": "EF",   "Use case": "VoIP, real-time"},
                {"DSCP": 34, "Name": "AF41", "Use case": "Interactive video"},
                {"DSCP": 26, "Name": "AF31", "Use case": "Mission-critical data"},
                {"DSCP": 18, "Name": "AF21", "Use case": "Transactional data"},
                {"DSCP": 10, "Name": "AF11", "Use case": "High-throughput data"},
                {"DSCP": 8,  "Name": "CS1",  "Use case": "Scavenger / bulk"},
                {"DSCP": 0,  "Name": "CS0",  "Use case": "Best effort (default)"},
            ]))
    else:
        st.info("Enable QoS above to configure per-DSCP traffic classes.")

with tab_stats:
    st.subheader("Interface & Queue Statistics")
    if st.button("🔄 Refresh Stats"):
        with st.spinner("Reading stats…"):
            try:
                stats = get_stats(if_a, if_b)
                st.session_state["last_stats"] = stats
            except Exception as e:
                st.error(f"Stats error: {e}")

    if "last_stats" not in st.session_state:
        st.info("Press **Refresh Stats** to load current counters.")
    else:
        stats = st.session_state["last_stats"]
        for iface, s in stats.items():
            st.markdown(f"#### Interface `{iface}`")
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
                st.markdown("**Queue disciplines (tc -s):**")
                st.dataframe(pd.DataFrame([
                    {"Type": q.get("type"), "Handle": q.get("handle"),
                     "Bytes": q.get("bytes", 0), "Packets": q.get("packets", 0),
                     "Dropped": q.get("dropped", 0), "Overlimits": q.get("overlimits", 0)}
                    for q in s["qdiscs"]
                ]), use_container_width=True)
            st.divider()

with tab_log:
    st.subheader("Command Log")
    if st.session_state["log"]:
        st.code("\n".join(st.session_state["log"]), language="bash")
    else:
        st.info("No commands run yet. Press **Apply** or **Reset**.")
