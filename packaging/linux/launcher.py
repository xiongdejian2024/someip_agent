"""Linux 发行版入口；持久数据不放在原子替换的安装目录中。"""

import os
import sys
from pathlib import Path

bundle = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
os.environ.setdefault("SOMEIP_AGENT_HOST", "127.0.0.1")
os.environ.setdefault("SOMEIP_AGENT_OPEN_BROWSER", "true")
os.environ.setdefault("SOMEIP_AGENT_DATA_DIR", str(data_home / "someip-agent" / "data"))
os.environ.setdefault(
    "SOMEIP_AGENT_NATIVE_BINARY", str(bundle / "native" / "soa_partner")
)

from someip_agent.main import run

if __name__ == "__main__":
    run()
