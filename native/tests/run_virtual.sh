#!/usr/bin/env bash
set -euo pipefail
cd /workspace
mkdir -p build/virtual-evidence
printf '%s\n' '正在创建隔离的虚拟以太网：server <-> bridge <-> client'
ip netns add soa-server
ip netns add soa-client
ip link add soa-bridge type bridge
ip addr add 10.77.0.254/24 dev soa-bridge
ip link set soa-bridge up
for node in server client; do
  ip link add "${node}-host" type veth peer name "${node}-eth"
  ip link set "${node}-host" master soa-bridge
  ip link set "${node}-host" up
  ip link set "${node}-eth" netns "soa-${node}"
  ip -n "soa-${node}" link set lo up
  ip -n "soa-${node}" link set "${node}-eth" name eth0
  ip -n "soa-${node}" addr add "10.77.0.$([ "$node" = server ] && echo 1 || echo 2)/24" dev eth0
  ip -n "soa-${node}" -6 addr add "fd77::$([ "$node" = server ] && echo 1 || echo 2)/64" dev eth0 nodad
  ip -n "soa-${node}" link set eth0 up
  ip -n "soa-${node}" route add 224.0.0.0/4 dev eth0
done
ip -j link show > build/virtual-evidence/interfaces.json
tcpdump --immediate-mode -i soa-bridge -U -w build/virtual-evidence/soa.pcap 'udp or tcp' > build/virtual-evidence/tcpdump.log 2>&1 &
capture_pid=$!
trap 'kill -INT "$capture_pid" 2>/dev/null || true; wait "$capture_pid" 2>/dev/null || true' EXIT
export SOMEIP_AGENT_NATIVE_BINARY=/workspace/build/native/soa_partner
python -m pytest native/tests/test_virtual.py native/tests/test_arxml_virtual.py native/tests/test_sat_wti_virtual.py -v --junitxml=build/virtual-evidence/junit.xml
ip netns exec soa-server python -m pytest native/tests/test_backend_virtual.py native/tests/test_network_virtual.py native/tests/test_capture_virtual.py native/tests/test_recovery_virtual.py native/tests/test_services_virtual.py -v \
  --basetemp=build/virtual-evidence/backend-pytest \
  --junitxml=build/virtual-evidence/backend-junit.xml
kill -INT "$capture_pid"
wait "$capture_pid" || true
trap - EXIT
python native/tests/audit_pcap.py build/virtual-evidence/soa.pcap \
  --output build/virtual-evidence/pcap-audit.json
python native/tests/audit_offline.py build/virtual-evidence/soa.pcap \
  --reference build/virtual-evidence/pcap-audit.json \
  --output build/virtual-evidence/offline-audit.json
