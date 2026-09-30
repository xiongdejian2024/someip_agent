"""PyInstaller 入口；业务启动逻辑保持在后端包内。"""

import os

os.environ.setdefault("SOMEIP_AGENT_OPEN_BROWSER", "true")
os.environ.setdefault("SOMEIP_AGENT_HOST", "127.0.0.1")

from someip_agent.main import run


if __name__ == "__main__":
    run()
