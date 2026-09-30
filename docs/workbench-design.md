# 高密度测量工作台

本次设计参考 Vector 的 Trace/Graphics 工作流：显示过滤与采集分离，报文列表与详情分离，
按需挑选波形，并允许冻结后分析。以下是本项目的具体实现选择，不代表与 CANoe 全功能等价。

## 显示与数据边界

- 前端采集缓存按报文接收顺序保留最近 2,000 帧，100 ms 批量发布视图，避免每帧触发 React 渲染。
- 波形保留最近 3,000 个有效数值采样点；每个点仅包含该帧真实携带的信号值。
  不把其他信号的旧值复制成新采样，不猜测单位和不存在的物理量。
- 首次连接显示空状态；断线保留已收到的数据并重连，不向实际采集中混入演示信号。
- 后端缓冲与前端显示窗口独立；界面的“冻结显示”和筛选不停止采集，也不修改原始报文。
- Trace 的分页、稳定行高、列宽和独立详情区限制 DOM 数量。长名称可完整查看，但不挤压相邻列。
- Graphics 按需选择少量信号，默认分轨并使用独立纵轴，支持叠加、时间范围和缩放。
- 仿真按服务目录、候选信号、工作集和参数编辑组织；大量信号用分页与局部滚动呈现。

前端显示窗口是有界的最近数据窗口，不作为无损长期记录器。时间窗口实际覆盖范围取决于
消息速率与缓存点数；完整抓包分析由 PCAP 工作流承接。

## 流式智能体

在线回复使用 OpenAI-compatible `stream=true`，后端以 SSE 转发增量文本与工具状态。
前端逐段追加并渲染 Markdown，支持停止生成和部分内容保留；异常断流有明确错误状态。
原有非流式 API 保持兼容。

## 验证入口

```sh
node scripts/check_monitor_buffer.mjs
node scripts/check_agent_stream.mjs
npm --prefix frontend run typecheck
npm --prefix frontend run build
.venv/bin/python -m pytest backend/tests
```

开发服务的 `/qa/workbench.html` 提供独立内存夹具，包含 131 个服务、200 路信号、
20,000 帧初始报文、可切换的 1,000 帧/秒输入，以及延时分块的 Markdown 回答。
页面拦截全部网络接口，不调用模型网关、不启动真实网络仿真，且不作为生产构建入口。

### 本次验收记录（2026-09-19）

- 后端 35 项测试通过，Ruff 和 Mypy 检查通过；前端类型检查与生产构建通过。
- 缓存脚本验证容量、顺序、清空、快照替换、稀疏采样；流式脚本验证 UTF-8 单字节分片、
  CRLF、保活、完成、异常断流及取消。
- 浏览器验收：高流量下 Trace 每页 50 行，冻结时后台继续收包且历史行保持不变；
  200 路信号按需选择最多 6 路，图轨独立纵轴、内部滚动，无整页横向溢出。
- 仿真夹具验收：每页 40 个候选信号；配置两路激励并启动后，单独停止其中一路不影响另一路，
  再停止全部后任务归零。真实页面原有 131 个服务仍正常加载。
- Markdown 标题、表格、代码块、引用正常渲染，生成未完成时已有可见内容；
  点击停止后中断数据流并保留部分回答，原始 HTML 不执行。
- 真实已配置网关的 `qwen3.5-plus` 最小术语问答联调成功：首段约 3.11 秒、48 次文本增量，
  约 5.73 秒正常结束。本次只验证该模型与当时链路，不代表全部模型或网络条件下的性能保证。

## 设计依据

- [Vector：Trace Window Features（2017）](https://support.vector.com/kb/sys_attachment.do?sys_id=3aada43b1b6a58548e9a535c2e4bcbed)
  ——显示过滤、有界历史、详情分区、暂停分析。
- [Vector：CANoe/CANalyzer Analysis and Test（2016，ESA 托管原始讲义）](https://indico.esa.int/event/120/contributions/473/attachments/664/710/13_Vector_Comprehensive_CAN_Network_Analysis_and_Test.pdf)
  ——Trace/Graphics 分窗、信号选择、曲线测量、交互激励。

这些资料用于工作流设计，不据此推断当前 CANoe 版本的完整功能或性能。
