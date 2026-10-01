#!/usr/bin/env bash
set -euo pipefail
cd /workspace
if [[ -L build/performance-evidence ]]; then
  printf '%s\n' '性能证据根目录不得是符号链接' >&2
  exit 1
fi
if [[ -n "${SOMEIP_AGENT_EVIDENCE_OWNER:-}" && ! "$SOMEIP_AGENT_EVIDENCE_OWNER" =~ ^[0-9]+:[0-9]+$ ]]; then
  printf '%s\n' '性能证据所有者必须为显式 UID:GID' >&2
  exit 1
fi
mkdir -p build/performance-evidence
cleanup() {
  local result=$?
  trap - EXIT
  if [[ -n "${SOMEIP_AGENT_EVIDENCE_OWNER:-}" ]]; then
    if ! chown -hR "$SOMEIP_AGENT_EVIDENCE_OWNER" build/performance-evidence; then
      printf '%s\n' '性能证据归还所有权失败' >&2
      if [[ "$result" == 0 ]]; then result=1; fi
    fi
  fi
  exit "$result"
}
trap cleanup EXIT
python -m pytest native/tests/test_evidence_permissions.py -v \
  --junitxml=build/performance-evidence/permissions-junit.xml
source native/tests/virtual_network.sh
setup_virtual_network
export SOMEIP_AGENT_NATIVE_BINARY=/workspace/build/native/soa_partner
python -m pytest native/tests/test_performance_metrics.py -v \
  --junitxml=build/performance-evidence/metrics-junit.xml
python native/tests/benchmark_virtual.py --output build/performance-evidence --require-installed "$@"
