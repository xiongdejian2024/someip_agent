import os
import shutil

import pytest


@pytest.fixture
def native_runtime():
    """本机不伪造原生运行；原生验收环境必须提供二进制并禁止跳过。"""
    binary = os.environ.get("SOMEIP_AGENT_NATIVE_BINARY", "soa_partner")
    if not shutil.which(binary):
        if os.environ.get("SOMEIP_AGENT_REQUIRE_NATIVE_TESTS") == "1":
            pytest.fail("原生验收环境缺少 soa_partner 二进制")
        pytest.skip("需要 Linux/Windows 的 vsomeip 二进制；由原生容器执行完整验收")
    return binary
