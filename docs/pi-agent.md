# Pi Agent 与内置控制台接口

## 开发与发布节奏（用户于 2026-10-02 更新）

每完成一个小块功能即提交并推送 main。开发阶段提交带 `[skip ci]`，避免提前触发统一发行验证。
全部功能开发完成后再统一验证，随后更新版本号、构建签名包并发布 GitHub Release。
待发布版本为 0.1.2，不覆盖已有 0.1.1 Release；本地包仅为隔离夹具，不对外发布。
Pi 执行链路、受控操作 API、页面命令消费者、打包接入及生命周期/审计收尾已完成开发并逐块提交。
旧 Python 模型客户端与自研流式解析器已移除。统一验证已开始，发行门禁尚未全部通过，
不能据开发提交或此前阶段测试宣称目标已全部完成。

统一验收已通过前后端静态检查/构建、真实 SDK 模型与工具循环和实际 HTTP 断连清理。
断连回归同时覆盖 ASGI 2.3 的 Starlette 取消作用域和 ASGI 2.4 的主动断连监听。
正式发布以本版本提交的完整 Linux x86_64 CI、签名与公开附件复验结果为准。

此前本机联调曾通过设置接口写入 `distribution-test-only-key`，该明确的临时条目已删除。
因事前没有保存该条目原值，不能确认是否覆盖先前模型凭据；若曾配置真实模型密钥需重新配置。
后续浏览器夹具进程禁止宿主 keyring 写入，干净发行测试只在独立容器写入测试配置。
该事件不涉及 Ed25519 发布签名私钥或 GitHub Actions 发布 Secret。

## 实现与边界

智能体默认使用 `@earendil-works/pi-agent-core` / `pi-ai` 1.0.0（MIT）实际执行模型和工具循环，
不是改名或兼容外壳。版本及依赖完整性锁在 `agent-runtime/package-lock.json`。
仅引入核心 Agent 和 OpenAI Chat Completions 协议，使用项目原有网关、模型、温度与密钥配置。
不引入 coding CLI，不发现或加载本机插件、Skills、AGENTS.md，也不注册 bash/read/write 工具。
这是产品的运行行为；开发时查上游文档所用的技能不属于产品依赖。

Python 保留 ARXML、原生运行时、证据分析、权限校验和审计；Node Pi 负责模型与工具调度。
每次请求独立子进程，通过私有 stdin/stdout JSONL 桥接。密钥不进入命令行、前端、审计或源码。
最多同时 4 个会话、每会话 4 轮模型请求、16 次执行，JSONL 每行上限 1 MiB，回答上限 256 KiB。
会话（包含排队、模型、工具）受设置页超时限制，取消/断连会清理对应进程。
HTTP 重定向禁用，远程网关仍应使用 HTTPS；现有模型不支持 SSE/工具调用时会明确报错。
工具名称按标准 OpenAI 流的首帧完整名称处理；参数 JSON 支持跨帧分片。

未配置 API Key 时，保留标记 `local-evidence-engine` / `degraded=true` 的只读证据模式。
`/agent/chat` 与 `/agent/chat/stream` 共享 Pi 执行链路。完成事件包含 `runtime=pi-agent-core`；
长度截断、异常退出、轮次超限不伪装为成功。

## 程序化操作控制台

在后端 `/docs` 查看 OpenAPI。所有接口前缀均为 `/api/v1`：

| 接口 | 用途 |
| --- | --- |
| `GET /console/actions` | Pi 运行时状态、所有内置操作、参数 JSON Schema、写操作标记 |
| `POST /console/actions/{name}` | 执行同一套受控工具；不需要模型密钥 |
| `GET /console/commands` | 获取页面命令当前游标，不重放历史命令 |
| `GET /console/commands?after_id=N` | 读取游标之后的页面命令；最多保留最近 100 条 |

读操作包括服务 schema、报文/SD/信号分析、仿真/原生会话/监听状态及网卡枚举。
写操作包括 internal 信号仿真启停、原生会话启停/RPC/通知/回复、指定监听启停和清空监控。
这些操作复用现有管理器，不绕过 ARXML 布局、网络发送授权、目的地址白名单或原生能力门禁。
停止操作只接受单个具体 ID，不提供全停。未知工具、额外字段或无效参数会被拒绝。
文件导入继续使用已有 `/arxml/import`、`/pcap/import` multipart 接口，不接受任意本地路径。
更新与模型凭据设置保持在已有专用 API，不授权智能体读写私钥、模型密钥或升级信任根。

读取监控：

```bash
curl -sS http://127.0.0.1:8765/api/v1/console/actions/get_monitor_summary \
  -H 'Content-Type: application/json' -d '{"arguments":{}}'
```

只有本次请求显式 `allow_mutation=true` 才能执行写操作，授权不从历史聊天或工具文字继承：

```bash
curl -sS http://127.0.0.1:8765/api/v1/console/actions/stop_simulation \
  -H 'Content-Type: application/json' \
  -d '{"arguments":{"simulation_id":"明确的任务ID"},"allow_mutation":true}'
```

`navigate_console` 的参数仅支持 `dashboard/services/simulation/monitor/pcap/settings`，
前端每 1.5 秒读取并应用命令。返回 `queued` 只表示已排队，不表示浏览器已应用；
只有已连接且完成初始游标同步的页面会接收新命令，单机多个页面会共同接收。
不能导航外部 URL、执行 JavaScript 或任意 DOM selector。

每次执行/权限拒绝分别记录 `console.<工具名>` 审计，即使后续模型连接失败也保留操作记录。
默认后端只绑定本机回环；`allow_mutation` 是操作授权参数，不是远程用户身份认证。
不要将端口直接开放到公网；跨主机部署需要已有组织认证/反向代理访问控制。

## 安装与验证

源码：`make install-agent` 自动 npm ci 并构建单文件 Pi 运行时，不需要用户安装 Pi CLI。
服务管理员可用 `SOMEIP_AGENT_PI_NODE_BINARY` / `SOMEIP_AGENT_PI_RUNTIME_PATH` 指定部署路径，
这些配置不是远程 API 参数。Docker 构建及 Linux 打包已加入内置 Node 22.19.0 与运行时。
打包保留 Node 许可、真正进入 bundle 的依赖许可、lockfile 与运行时 SHA-256。
当前改造尚须完成最新提交的独立 Linux 发行包与干净环境验收，不能把旧 0.1.1 当 Pi 包。

真实 SDK 集成测试（本地 HTTP 模型夹具，不消耗企业模型配额）：

```bash
make install-agent
.venv/bin/python -m pytest backend/tests/test_pi_agent.py backend/tests/test_agent_streaming.py
```

该测试不是只 mock Pi 类：实际启动 Node SDK，读取 HTTP SSE，验证工具执行/结果回传、
未授权写拒绝、schema 拒绝、四轮上限、截断失败、同步与流式共用链路及进程清理。
车型源冲突、Bootes、Windows 及已有原生未完成边界按先前范围保留，不用本次改造解决。
