# SOME/IP Agent

面向车载以太网研发与测试的企业级 SOME/IP 工作台。项目采用前后端分离架构：后端负责
ARXML 建模、SOME/IP / SOME/IP-SD 编解码、PCAP 导入、信号仿真与智能体编排；前端负责
服务浏览、实时监控、波形展示、仿真控制和模型配置。

> 当前版本是 `0.1.0` 的可运行 MVP，不宣称与 CANoe 功能等价。已经落地与规划中的能力
> 在[路线图](docs/roadmap.md)中分别标记，避免把设计目标误认为已交付能力。

## 核心能力

- ARXML 安全导入，抽取服务、方法、事件、字段、部署 ID；基础类型、明确部署的嵌套结构/数组可解析到原生目录，缺失或歧义布局禁止猜测发包；
- SOME/IP 报文与 SOME/IP-SD 基础条目/选项编解码；
- PCAP / PCAPNG 离线导入，解析 IPv4/IPv6、UDP/TCP 和 SOME/IP；
- 原生 UDP/TCP/IPv4 组播端口监听，vsomeip 解码 SOME/IP 并汇入统一监控流（不是网卡被动抓包）；
- 基于 WebSocket 的有界监控数据流、Trace 分页/冻结/详情和可选信号的 ECharts 分轨波形；
- vsomeip 原生信号仿真与服务发现，Python 通过控制 socket 管理二进制，周期生成和编码不再由 Python 执行；
- OpenAI 兼容模型配置、SSE 流式智能体、Markdown/表格/代码块显示与停止生成；
- 根版本、Python 包版本、前端版本与源码版本的语义版本一致性校验；
- Docker 开发环境和 PyInstaller Linux 完整 ZIP 发行包；Windows 打包/调试暂缓，保留手动流水线；
- 签名更新清单、下载哈希校验、独立升级器准备确认、重启验证与失败回滚；正式发布源需配置。

Linux 独立发行包构建、真实 HTTPS 升级/失败回滚和无 Python/SDK 的干净运行验收，见
[Linux 打包与升级](docs/linux-packaging.md)。

## 架构概览

```text
React + Vite + ECharts
          │ REST / WebSocket / SSE
          ▼
FastAPI 应用层 ── 智能体编排 ── OpenAI 兼容网关
          │
          ├── ARXML 投影模型 ── 原生服务配置
          ├── 控制 socket ── vsomeip 原生服务 / 周期发生器 / 端口监听 / 网卡捕获
          └── 离线 PCAP / 原生监控 socket ── 展示分析与监控缓冲区
```

详细设计、安全边界和数据流见[架构文档](docs/architecture.md)。

原生迁移正在进行，不代表整个底层已经验收完成：默认仿真、端口监听和 Ethernet 被动抓包已切换。
ARXML 基础类型及明确 AP 序列化部署的嵌套结构、定长/有界变长数组已接通原生目录和 Python 字典初始化；服务页面已接通启停/调用/人工响应；
页面字节序默认遵循 ARXML，显式覆盖与源布局冲突时拒绝。复合字段及二维数组已在长度前缀 0/1/2/4 字节、UDP/TCP 和两种字节序的受限模型矩阵中验证。
原生 Codec 已支持变长非末尾的 32/64-bit 绝对位置对齐；Classic I-SIGNAL 显式 payload
属性已归一化，但真实车型的缺失长度字段、消息类型和 session 仍有门禁，不能据此声称全部可发包。
SAT 适配器可监督本实例拥有的原生进程，检测控制循环持续无响应并恢复活动配置，不重放业务请求；
成员字典可显式选择独立 vsomeip application/Client ID；默认编号 client 仍共享旧身份。
显式身份启动先协商桥接能力，再核对实际 ID，旧二进制不能静默忽略选择；双身份已补充
真实退出/暂停恢复及独立线上抓包核验，但不代表任意规模、跨代迟到响应和长稳均已验收。
页面支持独立成员身份选项，具体生命周期、上限和虚拟网验收边界见原生运行时文档。
服务名保持源 ARXML 的 SHORT-NAME，完整路径只用于选取；SAT 只参考调用形式，不作为业务
目录。现有 V6.12.0 ARXML 的 131 个服务与指定 comm/config 下 H47A/V_6_12_0 两份 JSON
已同 ID/同名核对；真实车型复合布局仍有解析门禁，名称一致不代表全部服务可发包。
完整序列化映射、SAT 辅助 API、IPv6 分片、IPv4 选项专用语义及 Windows 实机验收仍有门禁。
IPv4 EOL 零补齐、NOP、通用 TLV 长度边界及带选项首片已接通，限定测试矩阵见原生运行时文档。
离线 PCAP 已接入相同原生重组/解码路径，Python 仅聚合展示，不保留纯 Python 解析回退。
IPv4 分片已接入成熟重组库，但不代表支持所有 IP 流量。当前状态与复现命令见
[原生运行时](docs/native-runtime.md)和[执行记录](docs/vsomeip-progress.md)。
已补充同机的 RPC、逐条 Python 通知和原生序列发生器短负载证据，分别核对线上与客户端
交付；数值与口径见[性能证据](docs/performance.md)，不作为硬实时、线速或长稳承诺。

## 本地开发

要求 Python 3.10+、Node.js 20+。推荐 Python 3.12 与 Node.js 22 LTS。
`make install` 会自动创建项目内的 `.venv`，后续后端命令固定使用该虚拟环境，避免系统
Python 与项目依赖混用。

```bash
make install
make check-version
```

分别启动两个进程：

```bash
make dev-backend
make dev-frontend
```

请在两个终端中分别运行；先看到后端输出 `Uvicorn running on http://127.0.0.1:8765`，再启动
前端。不要在 `backend/` 或 `frontend/` 子目录执行这些 `make` 命令。

- Web 控制台：<http://127.0.0.1:5173>
- OpenAPI：<http://127.0.0.1:8765/docs>
- 健康检查：<http://127.0.0.1:8765/api/v1/health>

如果启动失败，先在项目根目录执行：

```bash
make install
.venv/bin/python -c "import keyring, uvicorn; print('后端依赖正常')"
curl http://127.0.0.1:8765/api/v1/health
```

- 出现 `ModuleNotFoundError`：重新执行 `make install`，不要直接使用系统 `python3` 启动后端；
- 出现 `Address already in use`：端口 8765 或 5173 已被其他进程占用，先关闭旧进程；
- 页面显示后端离线：确认 8765 健康检查可访问，并保留后端终端持续运行；
- Windows PowerShell 本地开发请使用 `.venv\Scripts\python.exe`，安装包构建见下文专用脚本。

也可以使用 Docker 启动开发环境：

```bash
docker compose up --build
```

Docker 模式适合 ARXML/PCAP/页面联调。实时抓包、组播、VLAN、硬件时间戳等功能依赖宿主机
网卡能力，不能假定普通 Docker Desktop 网络与车载以太网测试网等价。

## 智能体模型配置

默认网关地址为：

```text
https://voyahgpt-gateway.voyah.cn/api/gateway/v1
```

支持的模型：

- `qwen3.5-plus`
- `deepseek-v4-pro`
- `deepseek-v4-flash`
- `glm-5.2`
- `deepseek-v4.1-flash`

复制示例配置后，通过环境变量或设置页配置密钥：

```bash
cp .env.example .env
```

API Key 不应进入 Git、前端环境变量、日志、崩溃报告或导出的工程文件。生产环境应写入操作
系统凭据库或企业密钥管理系统，并通过短期凭据访问。

回复内容通过 `/api/v1/agent/chat/stream` 增量显示。生成过程中可点击停止，已经显示的内容会保留。
Markdown 支持列表、表格、引用和代码块；代码块与宽表格在气泡内单独滚动。

## 高密度仿真与监控

- 在仿真页按服务搜索候选信号，勾选加入工作集，在属性面板配置激励，再启动测量；
- 在监控页用 Trace 过滤协议、方向、错误或关键字，选中一行查看报文头、Payload 和信号；
- “冻结显示”会保留当前报文与波形，后台采集继续；翻阅历史页时也固定当前视图；
- Graphics 默认分轨，选择最多 6 路信号，使用时间窗口和底部范围条缩放；
- 前端保留最近 2,000 帧及 3,000 个数值采样点，每 100 ms 批量刷新，避免持续累积界面节点。

设计依据与边界见[高密度工作台设计](docs/workbench-design.md)。开发时可访问
`http://127.0.0.1:5173/qa/workbench.html` 进行隔离验收：所有 API、流式回答和高流量输入均为
内存夹具，不调用真实网关，也不会启动实际网络仿真。该页面不进入生产构建。

## 测试与构建

```bash
make lint
make test
make build-frontend
make ci
make native-image
make native-test
make native-regression
make native-installed-test
node scripts/check_monitor_buffer.mjs
node scripts/check_agent_stream.mjs
```

Windows 安装包需在 Windows 的 PowerShell 7 (`pwsh`) 上构建，先准备获授权抓包 SDK/triplet：

```powershell
.\packaging\windows\build-native.ps1 -VcpkgRoot C:\vcpkg -CaptureTriplet x64-windows-npcap -OverlayTriplets C:\approved-triplets
.\packaging\windows\build.ps1 -Clean -NativeRuntimeDir .\.build\native-windows\runtime
```

输出位于 `packaging/windows/output/`。生产发布前必须完成代码签名、安装包签名、恶意软件
扫描和签名更新清单生成；开发流水线不会伪造或硬编码签名私钥。

## 安全默认值

- 默认监听 `127.0.0.1`；
- 默认禁止向真实网络发包；
- ARXML 禁止 DTD、外部实体和网络解析；
- PCAP 导入是被动分析，不会自动重放；
- 智能体默认只读分析，高风险动作必须经策略校验和用户确认；
- 在线升级只接受 HTTPS、受信公钥签名、哈希匹配且版本单调递增的清单。

## 文档

- [系统架构](docs/architecture.md)
- [CANoe SOME/IP 能力对标](docs/canoe-comparison.md)
- [高密度工作台设计](docs/workbench-design.md)
- [开源底座选型与许可证风险](docs/open-source-selection.md)
- [产品路线图](docs/roadmap.md)
- [Windows 打包与发布](docs/windows-packaging.md)
- [第三方软件通知](THIRD_PARTY_NOTICES.md)

## 法律说明

CANoe 是 Vector Informatik GmbH 的商标。本项目不是 Vector 产品，不包含 CANoe 代码，相关
对标仅依据公开资料进行产品能力分析。AUTOSAR 相关规范和商标的使用应遵守其各自条款。
