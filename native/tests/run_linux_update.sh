#!/usr/bin/env bash
# 只操作本次临时安装目录；真实发行包不借用 PYTHONPATH 或指定测试升级器。
set -euo pipefail
cd /workspace
evidence=/workspace/build/linux-update-evidence
if [ -n "${PYTHONPATH:-}" ]; then
  printf '%s\n' 'Linux 发行包验收不得设置 PYTHONPATH' >&2
  exit 1
fi
if [ -e "$evidence/fixture" ] || [ -e "$evidence/junit.xml" ]; then
  printf '%s\n' 'Linux 升级验收证据已存在，请选择新目录' >&2
  exit 1
fi
mkdir -p "$evidence"
cleanup() {
  local result=$?
  if [ -n "${SOMEIP_AGENT_EVIDENCE_OWNER:-}" ]; then
    if [[ ! "$SOMEIP_AGENT_EVIDENCE_OWNER" =~ ^[0-9]+:[0-9]+$ ]] ||
      ! chown -hR "$SOMEIP_AGENT_EVIDENCE_OWNER" "$evidence"; then
      printf '%s\n' 'Linux 升级证据归还所有权失败' >&2
      result=1
    fi
  fi
  exit "$result"
}
trap cleanup EXIT
python packaging/linux/prepare_update_fixture.py \
  --output "$evidence/fixture" --native-binary /workspace/build/native/soa_partner \
  > "$evidence/fixture-build.log" 2>&1
baseline_archives=(/opt/linux-package/*.zip)
new_archives=("$evidence"/fixture/new/*.zip)
if [ "${#baseline_archives[@]}" != 1 ] || [ "${#new_archives[@]}" != 1 ]; then
  printf '%s\n' '升级验收必须具有唯一的新旧发行包' >&2
  exit 1
fi
cp "${baseline_archives[0]}" "$evidence/baseline.zip"
cp /opt/linux-package/release/build-info.json "$evidence/baseline-build-info.json"
export SOMEIP_AGENT_LINUX_RELEASE_DIR=/opt/linux-package/release
export SOMEIP_AGENT_LINUX_NEW_RELEASE_ZIP="${new_archives[0]}"
python -m pytest backend/tests/test_update.py backend/tests/test_update_worker.py \
  backend/tests/test_update_e2e.py -v --basetemp="$evidence/pytest" \
  --junitxml="$evidence/junit.xml"
printf '%s\n' '真实 Linux 发行包 HTTPS 签名升级、原子替换、重启及失败回滚验收完成'
