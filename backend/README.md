# SOME/IP Agent Core

FastAPI 后端，负责 ARXML 建模、SOME/IP/SOME/IP-SD 编解码、PCAP 导入、信号仿真、实时监控、智能体编排和在线升级检查。

开发启动：

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e './backend[dev]'
someip-agent
```

Swagger UI：<http://127.0.0.1:8765/docs>
