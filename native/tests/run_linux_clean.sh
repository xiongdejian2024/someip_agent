#!/usr/bin/env bash
# 干净运行镜像中无 Python/SDK，验证发行包自身的网页和原生运行库。
set -euo pipefail
linux_smoke_image=$1
linux_smoke_evidence=$2
if [ -e "$linux_smoke_evidence" ]; then
  printf '%s\n' '干净运行验收目录已存在，请使用新目录' >&2
  exit 1
fi
mkdir -p "$linux_smoke_evidence"
linux_smoke_container=$(docker create --init --network none --label someip-agent.test=linux-clean "$linux_smoke_image")
cleanup() {
  local result=$?
  trap - EXIT
  docker logs "$linux_smoke_container" > "$linux_smoke_evidence/application.log" 2>&1 || result=1
  docker stop --time 10 "$linux_smoke_container" >/dev/null || result=1
  docker rm "$linux_smoke_container" >/dev/null || result=1
  exit "$result"
}
trap cleanup EXIT
docker inspect "$linux_smoke_container" > "$linux_smoke_evidence/container.json"
docker start "$linux_smoke_container" >/dev/null
request() {
  docker exec "$linux_smoke_container" bash -c '
    exec 3<>/dev/tcp/127.0.0.1/8765
    printf "%s %s HTTP/1.0\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n%s" "$1" "$2" "${#3}" "$3" >&3
    cat <&3
  ' bash "$1" "$2" "${3:-}"
}
ready=false
for attempt in $(seq 1 30); do
  if request GET /api/v1/health > "$linux_smoke_evidence/health.http" 2> "$linux_smoke_evidence/probe.log"; then
    ready=true
    break
  fi
  sleep 1
done
if [ "$ready" != true ]; then
  printf '%s\n' '干净发行包启动失败，保留应用与探测日志' >&2
  exit 1
fi
docker exec "$linux_smoke_container" bash -c '
  for linux_clean_tool in python python3 node npm pi gcc g++ cmake; do
    if command -v "$linux_clean_tool"; then exit 1; fi
  done
'
docker exec "$linux_smoke_container" env LD_LIBRARY_PATH=/opt/someip-agent/_internal \
  /opt/someip-agent/_internal/native/soa_partner --version > "$linux_smoke_evidence/native-version.txt"
docker exec "$linux_smoke_container" sha256sum /opt/someip-agent/_internal/native/soa_partner \
  > "$linux_smoke_evidence/native-sha256.txt"
docker exec "$linux_smoke_container" cat /opt/someip-agent/build-info.json \
  > "$linux_smoke_evidence/build-info.json"
request GET / > "$linux_smoke_evidence/index.http"
docker cp scripts/test_pi_distribution.mjs "$linux_smoke_container:/tmp/test_pi_distribution.mjs"
docker exec -e SOMEIP_AGENT_TEST_ISOLATED=1 "$linux_smoke_container" \
  /opt/someip-agent/_internal/pi/node \
  /tmp/test_pi_distribution.mjs > "$linux_smoke_evidence/pi-result.json" \
  2> "$linux_smoke_evidence/pi-errors.log"
request POST /api/v1/simulation/start '{"service_id":4660,"method_id":32770,"transport":"internal"}' \
  > "$linux_smoke_evidence/start.http"
request POST /api/v1/simulation/stop > "$linux_smoke_evidence/stop.http"
python3 native/tests/check_linux_payload_package.py "$linux_smoke_container" "$linux_smoke_evidence" \
  > "$linux_smoke_evidence/payload-run.log" 2>&1
docker cp "$linux_smoke_container:/var/lib/someip-agent/native-payload" \
  "$linux_smoke_evidence/native-payload"
python3 native/tests/check_linux_workbench_package.py "$linux_smoke_container" "$linux_smoke_evidence/workbench" \
  > "$linux_smoke_evidence/workbench-run.log" 2>&1
python3 - "$linux_smoke_evidence" <<'PY'
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("干净Linux发行包验收")
root = Path(sys.argv[1])
try:
    def body(name):
        response = (root / name).read_bytes()
        header, content = response.split(b"\r\n\r\n", 1)
        if b" 200 " not in header and b" 201 " not in header:
            raise RuntimeError(f"请求失败：{name} {header.decode()}")
        return content
    health = json.loads(body("health.http"))
    provenance = json.loads((root / "build-info.json").read_text())
    assert health["status"] == "ok" and health["version"] == provenance["version"]
    assert (root / "native-sha256.txt").read_text().split()[0] == provenance["native_sha256"]
    assert b"<html" in body("index.http")
    assert json.loads(body("start.http"))["running"] is True
    assert json.loads(body("stop.http"))[0]["running"] is False
    assert "vsomeip 3.5.10" in (root / "native-version.txt").read_text()
    payload_result = json.loads((root / "payload-result.json").read_text())
    assert payload_result["status"] == "verified"
    workbench_result = json.loads((root / "workbench/result.json").read_text())
    assert workbench_result["status"] == "verified" and workbench_result["frozen_cli"]
    (root / "result.json").write_text(json.dumps({
        "status": "verified", "python_installed": False, "network": "none",
        "version": health["version"], "native_started": True,
        "native_payload_decoded": True, "nested_json_preserved": True,
        "frozen_scenario_cli": True, "frozen_evidence_verify": True,
    }, ensure_ascii=False), encoding="utf-8")
    logger.info("无 Python/SDK 的发行包网页、发生器、原生解码与冻结场景／证据 CLI 均验证通过")
except Exception:
    logger.exception("干净 Linux 发行包验收失败")
    raise
PY
