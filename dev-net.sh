#!/usr/bin/env bash
# dev-net.sh — create or tear down a veth+namespace test environment for NetEmu
#
# Topology:
#   [ns-a] -- veth-a -- veth-ap -- [br0] -- veth-bp -- veth-b -- [ns-b]
#   10.0.0.1/24    (if_a in UI)        (if_b in UI)    10.0.0.2/24
#
# Usage:
#   sudo ./dev-net.sh up        # create namespaces + veth pairs, start container
#   sudo ./dev-net.sh down      # stop container, tear down everything
#   sudo ./dev-net.sh status    # show interface and tc state
#   sudo ./dev-net.sh test      # run ping + iperf3 tests (requires iperf3)

set -euo pipefail

NS_A="ns-a"
NS_B="ns-b"
IFACE_A="veth-ap"   # bridge-side peer — use this as Interface A in the UI
IFACE_B="veth-bp"   # bridge-side peer — use this as Interface B in the UI
IP_A="10.0.0.1"
IP_B="10.0.0.2"
PREFIX="24"

cmd_up() {
    echo "=== Creating network namespaces ==="
    for ns in $NS_A $NS_B; do
        if ip netns list | grep -q "^$ns"; then
            echo "  $ns already exists, skipping"
        else
            ip netns add "$ns"
            echo "  $ns created"
        fi
    done

    echo ""
    echo "=== Creating veth pairs ==="
    if ! ip link show veth-a &>/dev/null; then
        ip link add veth-a type veth peer name veth-ap
        echo "  veth-a <-> veth-ap created"
    else
        echo "  veth-a already exists, skipping"
    fi
    if ! ip link show veth-b &>/dev/null; then
        ip link add veth-b type veth peer name veth-bp
        echo "  veth-b <-> veth-bp created"
    else
        echo "  veth-b already exists, skipping"
    fi

    echo ""
    echo "=== Configuring namespace endpoints ==="
    ip link set veth-a netns $NS_A 2>/dev/null || true
    ip link set veth-b netns $NS_B 2>/dev/null || true
    ip netns exec $NS_A ip addr add ${IP_A}/${PREFIX} dev veth-a 2>/dev/null || true
    ip netns exec $NS_B ip addr add ${IP_B}/${PREFIX} dev veth-b 2>/dev/null || true
    ip netns exec $NS_A ip link set veth-a up
    ip netns exec $NS_B ip link set veth-b up
    ip netns exec $NS_A ip link set lo up
    ip netns exec $NS_B ip link set lo up

    echo ""
    echo "=== Setting up bridge ==="
    if ! ip link show br0 &>/dev/null; then
        ip link add name br0 type bridge
        echo "  br0 created"
    else
        echo "  br0 already exists, skipping"
    fi
    ip link set br0 up
    ip link set $IFACE_A master br0 && ip link set $IFACE_A up
    ip link set $IFACE_B master br0 && ip link set $IFACE_B up

    echo ""
    echo "=== Starting container ==="
    docker compose up --build -d
    echo ""
    echo "UI:          http://localhost:8501"
    echo "Interface A: $IFACE_A   (traffic from ns-a)"
    echo "Interface B: $IFACE_B   (traffic from ns-b)"
    echo "ns-a IP:     $IP_A"
    echo "ns-b IP:     $IP_B"
}

cmd_down() {
    echo "=== Stopping container ==="
    docker compose down || true

    echo ""
    echo "=== Removing bridge ==="
    if ip link show br0 &>/dev/null; then
        ip link set br0 down
        ip link del br0
        echo "  br0 removed (also removed veth-ap, veth-bp)"
    else
        echo "  br0 not found, skipping"
    fi

    echo ""
    echo "=== Removing namespaces ==="
    for ns in $NS_A $NS_B; do
        if ip netns list | grep -q "^$ns"; then
            ip netns del "$ns"
            echo "  $ns removed (also removed its veth)"
        else
            echo "  $ns not found, skipping"
        fi
    done
}

cmd_status() {
    for iface in $IFACE_A $IFACE_B; do
        echo "=== $iface ==="
        ip link show "$iface" 2>/dev/null || echo "  not found"
        echo "--- tc qdiscs ---"
        tc qdisc show dev "$iface" 2>/dev/null || echo "  (none)"
        echo ""
    done
    echo "=== Namespace reachability ==="
    ip netns exec $NS_A ip addr show veth-a 2>/dev/null || echo "  ns-a not found"
    ip netns exec $NS_B ip addr show veth-b 2>/dev/null || echo "  ns-b not found"
}

cmd_test() {
    if ! command -v iperf3 &>/dev/null; then
        echo "iperf3 not found. Install with: apt install iperf3"
        exit 1
    fi

    # BW argument: pass configured link BW so UDP sender matches the cap.
    # Default 200kbps (Iridium Certus 200). Override: sudo ./dev-net.sh test 500k
    BW=${1:-200k}

    echo "=== Ping RTT — 20 packets (A → B) ==="
    ip netns exec $NS_A ping -c 20 -i 0.2 $IP_B
    echo ""

    echo "=== UDP throughput — 5s @ ${BW} (A → B, video/bulk) ==="
    echo "    Reports jitter and packet loss — use these to verify BW cap and loss settings"
    ip netns exec $NS_B iperf3 -s --one-off -D -p 5201
    sleep 0.2
    ip netns exec $NS_A iperf3 -c $IP_B -p 5201 -t 5 -u -b $BW
    echo ""

    echo "=== UDP throughput — 5s @ ${BW} (B → A, reverse) ==="
    ip netns exec $NS_A iperf3 -s --one-off -D -p 5201
    sleep 0.2
    ip netns exec $NS_B iperf3 -c $IP_A -p 5201 -t 5 -u -b $BW
    echo ""

    echo "=== UDP DSCP 46 (Telemetry class) — 5s @ 10k (A → B) ==="
    echo "    Low-rate control traffic — expect near-zero loss even under BW pressure"
    ip netns exec $NS_B iperf3 -s --one-off -D -p 5202
    sleep 0.2
    ip netns exec $NS_A iperf3 -c $IP_B -p 5202 -t 5 -u -b 10k --tos 184
}

cmd_pressure() {
    if ! command -v iperf3 &>/dev/null; then
        echo "iperf3 not found. Install with: apt install iperf3"
        exit 1
    fi

    BW=${1:-200k}

    echo "=== Telemetry-under-pressure test ==="
    echo "  Flood:     UDP 2Mbps (no DSCP) → saturates ${BW} cap, fills Best Effort queue"
    echo "  Telemetry: UDP 10kbps DSCP 46  → priority class, should survive with low loss"
    echo ""

    # Start two servers (no --one-off so they accept the sequential flood + telemetry clients)
    ip netns exec $NS_B iperf3 -s -p 5201 &
    SRV_FLOOD=$!
    ip netns exec $NS_B iperf3 -s -p 5202 &
    SRV_TELEM=$!
    sleep 0.3

    # Saturating flood — best-effort (no TOS), runs 12s in background
    echo "--- Starting bulk flood (2Mbps, best-effort) ---"
    ip netns exec $NS_A iperf3 -c $IP_B -p 5201 -t 12 -u -b 2m &
    FLOOD_PID=$!

    sleep 2  # let flood fill the pipe and queues

    # Measure telemetry WHILE flood is running
    echo ""
    echo "--- Telemetry (DSCP 46, 10kbps) under flood ---"
    ip netns exec $NS_A iperf3 -c $IP_B -p 5202 -t 5 -u -b 10k --tos 184

    wait $FLOOD_PID 2>/dev/null || true
    kill $SRV_FLOOD $SRV_TELEM 2>/dev/null || true

    echo ""
    echo "Expected: telemetry loss ≈ link loss% (0.5%), jitter ≈ link jitter"
    echo "If telemetry loss >> link loss, the priority queue is not working"
}

case "${1:-}" in
    up)       cmd_up ;;
    down)     cmd_down ;;
    status)   cmd_status ;;
    test)     cmd_test "${2:-}" ;;
    pressure) cmd_pressure "${2:-}" ;;
    *)
        echo "Usage: sudo $0 {up|down|status|test|pressure} [BW]"
        echo "  test [BW]:     ping + UDP throughput tests (default BW: 200k)"
        echo "  pressure [BW]: flood link with bulk UDP, measure telemetry survival"
        exit 1
        ;;
esac
