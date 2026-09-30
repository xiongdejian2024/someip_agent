"""PyInstaller 入口；业务启动逻辑保持在后端包内。"""

import os
import sys
from pathlib import Path

os.environ.setdefault("SOMEIP_AGENT_OPEN_BROWSER", "true")
os.environ.setdefault("SOMEIP_AGENT_HOST", "127.0.0.1")
os.environ.setdefault("SOMEIP_AGENT_NATIVE_BINARY", str(Path(sys.executable).parent / "native" / "soa_partner.exe"))
os.environ.setdefault(
    "SOMEIP_AGENT_DATA_DIR",
    str(Path(os.environ["LOCALAPPDATA"]) / "SomeIpAgent" / "data"),
)

from someip_agent.main import run


if __name__ == "__main__":
    run()
