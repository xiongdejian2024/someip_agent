#!/usr/bin/env bash
# 安装包验收只挂载测试和输出目录，禁止用源码路径冒充 wheel 验收。
set -euo pipefail
cd /workspace
if [ -n "${PYTHONPATH:-}" ]; then
  printf '%s\n' '安装包验收失败：不得设置 PYTHONPATH'
  exit 1
fi
python - <<'PY'
import importlib
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("安装包验收")
try:
    for name in (
        "someip_agent.runtime.network", "someip_agent.domain.models",
        "someip_agent.soa.operator", "someip_agent.soa.partner",
        "someip_agent.soa.supervision", "someip_agent.pcap.native",
        "someip_agent.pcap.importer", "soa_partner.src.base_partner",
        "someip_agent.runtime.services", "someip_agent.runtime.service_models",
        "someip_agent.api.services",
        "someip_agent.arxml.parser", "someip_agent.arxml.wire_types",
        "someip_agent.arxml.transformation", "someip_agent.soa.catalog",
    ):
        module = importlib.import_module(name)
        path = module.__file__
        if path is None or "site-packages/" not in path:
            raise RuntimeError(f"模块不是安装包版本：{name} {path}")
        logger.info("已验证安装包模块：%s %s", name, path)
except Exception:
    logger.exception("安装包模块来源验证失败")
    raise
PY
bash native/tests/run_virtual.sh
SOMEIP_AGENT_REQUIRE_NATIVE_TESTS=1 python -m pytest backend/tests \
  --junitxml=build/virtual-evidence/backend-regression.xml
