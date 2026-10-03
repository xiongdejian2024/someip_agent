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
    # 初始化失败时 tcpdump 可能尚未安装 SIGINT 处理器；TERM + 有界等待防止清理挂死。
    kill -TERM "$capture_pid" 2>/dev/null || true
    if ! timeout 5 tail --pid="$capture_pid" -f /dev/null >/dev/null 2>&1; then
      printf '%s\n' '自有采集进程五秒内未退出，强制结束并保留失败证据' >&2
      kill -KILL "$capture_pid" 2>/dev/null || true
      if [[ "$result" == 0 ]]; then result=1; fi
    fi
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
source native/tests/virtual_network.sh
setup_virtual_network
ip -j link show > build/virtual-evidence/interfaces.json
# 大数组验收同时承载业务与IPC流量；使用有界64MiB采集缓冲，不过滤掉失败流量。
# 仍要求采集内核丢包为0，不能以扩大缓冲替代抓包完整性审计。
printf '%s\n' '启动完整UDP/TCP观测：64MiB采集缓冲，仍严格检查内核丢包'
tcpdump --immediate-mode -i soa-bridge -s 0 -B 65536 -w build/virtual-evidence/soa.pcap 'udp or tcp' > build/virtual-evidence/tcpdump.log 2>&1 &
capture_pid=$!
capture_ready=0
for attempt in {1..500}; do
  if ! kill -0 "$capture_pid" 2>/dev/null; then
    printf '%s\n' '采集进程提前退出，不能开始虚拟网验收' >&2
    exit 1
  fi
  if [[ -f build/virtual-evidence/tcpdump.log ]] && [[ "$(<build/virtual-evidence/tcpdump.log)" == *"listening on"* ]]; then
    capture_ready=1
    break
  fi
  sleep 0.01
done
if [[ "$capture_ready" != 1 ]]; then
  printf '%s\n' '五秒内采集未就绪，不能开始虚拟网验收' >&2
  exit 1
fi
export SOMEIP_AGENT_NATIVE_BINARY=/workspace/build/native/soa_partner
python -m pytest native/tests/test_virtual.py native/tests/test_arxml_virtual.py native/tests/test_arxml_composite_virtual.py native/tests/test_large_array_virtual.py native/tests/test_sat_wti_virtual.py native/tests/test_applications_virtual.py -v --junitxml=build/virtual-evidence/junit.xml
ip netns exec soa-server python -m pytest native/tests/test_backend_virtual.py native/tests/test_network_virtual.py native/tests/test_capture_virtual.py native/tests/test_ipv4_options_virtual.py native/tests/test_recovery_virtual.py native/tests/test_identity_recovery_virtual.py native/tests/test_services_virtual.py -v \
  --basetemp=build/virtual-evidence/backend-pytest \
  --junitxml=build/virtual-evidence/backend-junit.xml
kill -INT "$capture_pid"
wait "$capture_pid"
capture_pid=
python native/tests/audit_pcap.py build/virtual-evidence/soa.pcap \
  --capture-log build/virtual-evidence/tcpdump.log \
  --output build/virtual-evidence/pcap-audit.json
python native/tests/audit_offline.py build/virtual-evidence/soa.pcap \
  --reference build/virtual-evidence/pcap-audit.json \
  --output build/virtual-evidence/offline-audit.json
python native/tests/audit_applications.py build/virtual-evidence/soa.pcap \
  --capture-log build/virtual-evidence/tcpdump.log \
  --output build/virtual-evidence/application-audit.json
python native/tests/audit_identity_recovery.py build/virtual-evidence/soa.pcap \
  --capture-log build/virtual-evidence/tcpdump.log \
  --output build/virtual-evidence/identity-recovery-audit.json
python -m pytest native/tests/test_audit_pcap.py -v \
  --basetemp=build/virtual-evidence/audit-pytest \
  --junitxml=build/virtual-evidence/audit-junit.xml
python -m pytest native/tests/test_audit_identity_recovery.py -v \
  --basetemp=build/virtual-evidence/identity-audit-pytest \
  --junitxml=build/virtual-evidence/identity-audit-junit.xml
python -m pytest native/tests/test_service_names.py -v \
  --junitxml=build/virtual-evidence/service-names-junit.xml
# 激励类型专项使用独立 catalog；旧黄金 PCAP 已封存，避免不同 payload schema 混流。
python -m pytest native/tests/test_signal_sources_virtual.py native/tests/test_event_stimulus_virtual.py -v \
  --junitxml=build/virtual-evidence/signal-sources-junit.xml
ip netns exec soa-server python -m pytest native/tests/test_csv_recovery_virtual.py -v \
  --junitxml=build/virtual-evidence/csv-recovery-junit.xml
ip netns exec soa-server python -m pytest native/tests/test_state_recovery_virtual.py -v \
  --junitxml=build/virtual-evidence/state-recovery-junit.xml
python -m pytest native/tests/test_sync_virtual.py -v \
  --junitxml=build/virtual-evidence/sync-junit.xml
ip netns exec soa-server python -m pytest native/tests/test_sync_recovery_virtual.py -v \
  --junitxml=build/virtual-evidence/sync-recovery-junit.xml
