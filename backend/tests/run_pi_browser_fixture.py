"""真实浏览器联调夹具：本地 HTTP 网关 + 真 Pi SDK + 真控制台，不外连企业网关。"""

import argparse
from pathlib import Path

import keyring
import uvicorn
from pi_gateway import completion, gateway, tool_call

from someip_agent.config import Settings
from someip_agent.main import create_app

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--data-dir", type=Path, required=True)
parser.add_argument("--port", type=int, default=19065)
args = parser.parse_args()
# 测试进程禁用宿主凭据库写入；产品凭据管理不受影响。
keyring.set_password = lambda *_args: None
with gateway(
    [
        tool_call("navigate_console", {"page": "monitor"}),
        completion("Pi 已将打开报文监控页面的命令交给控制台。"),
        tool_call("clear_monitor", {}),
        completion("工具返回写操作未授权；没有清空缓存。"),
    ]
) as (url, _, _):
    uvicorn.run(
        create_app(
            Settings(
                _env_file=None,
                data_dir=args.data_dir,
                llm_base_url=url,
                llm_api_key="isolated-browser-test-key",
            )
        ),
        host="127.0.0.1",
        port=args.port,
    )
