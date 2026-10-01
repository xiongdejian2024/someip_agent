# SOME/IP Agent Core

FastAPI 控制面，负责 ARXML 建模、原生进程/socket 管理、PCAP 展示聚合、实时监控、智能体编排和在线升级检查。
SOME/IP 在线协议、周期生成、网卡捕获及离线 PCAP 重组/解码由 C++/vsomeip 与 libpcap/libtins 承担；
Python 不保留底层发包或 PCAP 解析回退。完整原生支持矩阵及验收边界见 `docs/native-runtime.md`。

开发启动：

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e './backend[dev]'
someip-agent
```

Swagger UI：<http://127.0.0.1:8765/docs>
