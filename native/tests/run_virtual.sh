#!/usr/bin/env bash
set -euo pipefail
cd /workspace
if [[ -n "${SOMEIP_AGENT_EVIDENCE_OWNER:-}" && ! "$SOMEIP_AGENT_EVIDENCE_OWNER" =~ ^[0-9]+:[0-9]+$ ]]; then
  printf '%s\n' '验收证据所有者必须是显式 UID:GID' >&2
  exit 1
fi
if [[ -L build/virtual-evidence ]]; then
  printf '%s\n' '验收证据根目录不得是符号链接' >&2
  exit 1
fi
mkdir -p build/virtual-evidence
capture_pid=
cleanup() {
  local result=$?
  trap - EXIT
  if [[ -n "$capture_pid" ]]; then
    kill -INT "$capture_pid" 2>/dev/null || true
    wait "$capture_pid" 2>/dev/null || true
  fi
  # root 只把生成的证据归还调用方；不放宽权限、不追随目录中的链接。
  if [[ -n "${SOMEIP_AGENT_EVIDENCE_OWNER:-}" ]]; then
    if ! chown -hR "$SOMEIP_AGENT_EVIDENCE_OWNER" build/virtual-evidence; then
      printf '%s\n' '验收证据归还所有权失败' >&2
      if [[ "$result" == 0 ]]; then result=1; fi
    fi
  fi
  exit "$result"
}
trap cleanup EXIT
python -m pytest native/tests/test_evidence_permissions.py -v \
  --junitxml=build/virtual-evidence/permissions-junit.xml
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
export SOMEIP_AGENT_NATIVE_BINARY=/workspace/build/native/soa_partner
python -m pytest native/tests/test_virtual.py native/tests/test_arxml_virtual.py native/tests/test_arxml_composite_virtual.py native/tests/test_sat_wti_virtual.py -v --junitxml=build/virtual-evidence/junit.xml
ip netns exec soa-server python -m pytest native/tests/test_backend_virtual.py native/tests/test_network_virtual.py native/tests/test_capture_virtual.py native/tests/test_recovery_virtual.py native/tests/test_services_virtual.py -v \
  --basetemp=build/virtual-evidence/backend-pytest \
  --junitxml=build/virtual-evidence/backend-junit.xml
kill -INT "$capture_pid"
wait "$capture_pid" || true
capture_pid=
python native/tests/audit_pcap.py build/virtual-evidence/soa.pcap \
  --output build/virtual-evidence/pcap-audit.json
python native/tests/audit_offline.py build/virtual-evidence/soa.pcap \
  --reference build/virtual-evidence/pcap-audit.json \
  --output build/virtual-evidence/offline-audit.json
python -m pytest native/tests/test_audit_pcap.py -v \
  --basetemp=build/virtual-evidence/audit-pytest \
  --junitxml=build/virtual-evidence/audit-junit.xml
