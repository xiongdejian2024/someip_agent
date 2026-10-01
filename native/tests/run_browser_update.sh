#!/usr/bin/env bash
# 浏览器真实发行包验收；只在带 NET_ADMIN 的独立测试容器内执行。
set -euo pipefail
cd /workspace
browser_evidence=/workspace/build/browser-update-evidence
browser_case=${1:-linux}
case "$browser_case" in
  linux|linux-rollback) ;;
  *) printf '%s\n' '浏览器验收只接受 linux 或 linux-rollback' >&2; exit 1 ;;
esac
if [[ -L "$browser_evidence" ]]; then
  printf '%s\n' '浏览器证据根目录不得是符号链接' >&2
  exit 1
fi
if [[ -n "${SOMEIP_AGENT_EVIDENCE_OWNER:-}" && ! "$SOMEIP_AGENT_EVIDENCE_OWNER" =~ ^[0-9]+:[0-9]+$ ]]; then
  printf '%s\n' '浏览器证据所有者必须为显式 UID:GID' >&2
  exit 1
fi
if [ -n "${PYTHONPATH:-}" ] || [ -e "$browser_evidence/browser" ] || [ -e "$browser_evidence/junit.xml" ]; then
  printf '%s\n' '不得设置 PYTHONPATH 或覆盖既有浏览器验收证据' >&2
  exit 1
fi
mkdir -p "$browser_evidence"
cleanup() {
  local result=$?
  trap - EXIT
  if [ -n "${SOMEIP_AGENT_EVIDENCE_OWNER:-}" ]; then
    # fixture 可以来自只读挂载；只归还本次写入的证据，不改复用发行包。
    while IFS= read -r -d '' evidence_entry; do
      if ! chown -hR "$SOMEIP_AGENT_EVIDENCE_OWNER" "$evidence_entry"; then
        printf '%s\n' '浏览器证据归还所有权失败' >&2
        if [[ "$result" == 0 ]]; then result=1; fi
      fi
    done < <(find "$browser_evidence" -mindepth 1 -maxdepth 1 ! -name fixture -print0)
  fi
  exit "$result"
}
trap cleanup EXIT

# 默认出口拒绝，仅允许本容器回环及已建立请求的响应。不给浏览器测试公网出口。
# 不清空宿主规则；Docker 启动必须使用专属网络命名空间，禁止 --network host。
test "$(iptables -S OUTPUT | wc -l)" -eq 1
iptables -A OUTPUT -o lo -j ACCEPT
iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -P OUTPUT DROP
iptables -P FORWARD DROP
iptables -A INPUT -i lo -j ACCEPT
iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A INPUT -p tcp --dport 18765 -j ACCEPT
iptables -P INPUT DROP
iptables-save > "$browser_evidence/firewall.rules"

new_archives=("$browser_evidence"/fixture/new/*.zip)
if [ "${#new_archives[@]}" != 1 ] || [ ! -f "${new_archives[0]}" ]; then
  printf '%s\n' '请先在隔离构建容器中生成唯一的真实新发行包夹具' >&2
  exit 1
fi
export SOMEIP_AGENT_UPDATE_UI_TEST=1
export SOMEIP_AGENT_UPDATE_UI_PORT=18765
export SOMEIP_AGENT_LINUX_RELEASE_DIR=/opt/linux-package/release
export SOMEIP_AGENT_LINUX_NEW_RELEASE_ZIP="${new_archives[0]}"
printf '%s\n' '容器出口已隔离；等待真实浏览器操作本机回环端口 18765，不调用安装 API 代替点击。'
python -m pytest "backend/tests/test_update_e2e.py::test_signed_https_upgrade_api_restarts_into_new_release[$browser_case]" \
  -v -s --basetemp="$browser_evidence/browser" --junitxml="$browser_evidence/junit.xml"
