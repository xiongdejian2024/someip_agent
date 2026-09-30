# NEXUS SOME/IP 智能工作台前端

面向车载以太网 SOME/IP 工具链的 React + TypeScript 前端。界面采用前后端分离架构，覆盖 ARXML 服务建模、信号仿真、实时波形、报文监听、PCAP 离线分析、智能诊断与模型安全配置。

## 本地运行

要求 Node.js 20+。依赖由项目根目录统一管理时，直接使用已有依赖即可。

```bash
npm run dev
npm run typecheck
npm run build
```

开发服务器默认位于 `http://127.0.0.1:5173`，`/api` 与 WebSocket 会代理到 `http://127.0.0.1:8765`。如需修改后端地址，复制 `.env.example` 为 `.env.local`，仅配置 `VITE_BACKEND_URL`。

> 不要把模型 API Key 放进 `.env`、Vite 环境变量或前端源码。Vite 的客户端环境变量会被打包进静态资源。

## 功能页面

- 系统总览：服务、报文、SOME/IP-SD 会话、实时波形与诊断摘要。
- ARXML 服务模型：导入 `.arxml`，浏览 Service / Method / Event / Field 定义。
- 信号仿真：选择服务、配置虚拟总线或 UDP 传输，并将手动信号转换成后端生成器任务。
- 实时监控：管理 UDP/TCP 监听器、WebSocket 流、信号波形、协议过滤、报文详情与 CSV 导出。
- PCAP 分析：导入 `.pcap` / `.pcapng`，展示协议分布、SD 时序和通信端点。
- 智能体：在任意页面打开诊断侧栏，调用兼容 OpenAI 格式的后端模型网关。
- 模型设置：支持预置模型、Base URL、超时和凭证配置；API Key 只写入后端，前端不回显。

## API 契约

前端使用以下 `/api/v1` 接口：

| 功能 | 方法与路径 |
| --- | --- |
| 健康检查 | `GET /health` |
| 服务列表 / ARXML | `GET /model/services`、`POST /arxml/import` |
| 报文快照 / 实时流 | `GET /monitor/messages`、`WS /monitor/ws` |
| 网络监听器 | `GET /network/listeners`、`POST /network/listeners/start`、`POST /network/listeners/stop` |
| 仿真 | `POST /simulation/start`、`POST /simulation/stop` |
| PCAP | `POST /pcap/import` |
| 智能体 | `POST /agent/chat` |
| 模型设置 | `GET /settings/llm`、`PUT /settings/llm` |

`src/api/adapters.ts` 负责将后端 snake_case、数字 ID 与协议枚举转换成 UI 模型；不要在页面中重复实现转换逻辑。

## 断线与安全策略

- 健康检查失败时，界面明确显示“演示模式”，并使用内置演示服务、报文和波形。
- WebSocket 断开后使用指数退避自动重连，最多延迟 15 秒。
- 演示 PCAP 结果会显著标记为“非真实解析”，避免与实际分析结果混淆。
- API Key 输入框默认空白。读取设置只接受 `api_key_configured`，保存后立即清空本地输入状态。
- 捕获到的异常会记录名称、消息和 stack，便于企业环境诊断。

## 目录结构

```text
src/
├── api/          # API 客户端、契约适配与结构化日志
├── components/   # 壳层、图表、文件拖放、智能体等组件
├── data/         # 明确标识的演示数据
├── hooks/        # 健康检查与 WebSocket 实时流
├── pages/        # 六个业务页面
├── App.tsx
└── index.css
```

生产构建输出到 `dist/`，可由桌面壳（如 Tauri / Electron）或后端静态资源服务纳入 Windows 安装包。
