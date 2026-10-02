"""正式发行信任根；仅包含可公开的公钥，不包含签名私钥。"""

from __future__ import annotations

import logging
import os
import platform

logger = logging.getLogger(__name__)

RELEASE_PUBLIC_KEY = "EpEe8jb6SNIDMvMcxEwmcZw5I9ZMLtJeUb6RNA/OVfY="
LINUX_X86_64_MANIFEST_URL = (
    "https://github.com/xiongdejian2024/someip_agent/releases/latest/download/"
    "manifest-linux-x86_64.json"
)


def configure_linux_release_updates() -> None:
    """只为已验收架构提供缺省值，保留显式环境变量（包括空值）。"""
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        return
    os.environ.setdefault("SOMEIP_AGENT_UPDATE_MANIFEST_URL", LINUX_X86_64_MANIFEST_URL)
    os.environ.setdefault("SOMEIP_AGENT_UPDATE_PUBLIC_KEY", RELEASE_PUBLIC_KEY)
    logger.info("Linux x86_64 发行版已配置缺省升级源与公开验签信任根")
