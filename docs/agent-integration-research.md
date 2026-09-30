# SOME/IP 智能体集成调研与设计边界

检索日期：**2026-09-19**。

本文区分已核实的外部能力、建议的产品工作流和本项目的实施范围。设计建议不代表当前版本已
完整实现，不代表通过 Vector、AUTOSAR 或功能安全认证。

## 1. 结论

智能体应围绕工程对象工作：**当前上下文 → 确定性工具取证 → 解释与方案 → 受控行动 → 工作台
定位与复核**。Markdown 和流式输出只是呈现方式，不能替代 SOME/IP 模型理解、真实报文证据
和权限校验。

本次通过 `SOME/IP + MCP`、`someip + agent + LLM`、CANoe AI 和 Wireshark MCP 等检索，未找到
可验证成熟、通用的 SOME/IP 专用开源 LLM/MCP 成品。这是本次检索的边界，不是“此类项目不存在”
的结论。协议运行时、抓包分析工具与智能体是不同层，不能混为一谈。

本轮不新增外部 MCP 服务，也不引入 `someipy` 或 `vsomeip` 依赖；优先利用现有工程模型、
协议解析、仿真接口、流式调用和前端工作台。

## 2. 已核实的原始来源

### Vector CANoe AI：工作流而非独立聊天窗口

- [CANoe AI Features](https://www.vector.com/en/product/canoe/canoe-ai-features/)
- [2026-08-19 官方发布说明](https://www.vector.com/en/company/press/press-releases/canoe-automates-development-and-testing-workflows-with-ai-agents/)

Vector 官方将 CANoe 20 SP2 的 MCP Server 与 AI Package 描述为开放的智能体、技能和工具组合，
允许使用自有模型端点。公开角色包括 Discovery、Project、Simulation、Coding、Analysis；能力
覆盖配置、仿真、代码、日志和测试工作流。结果回到同步的 CANoe 视图，由用户复核。

本项目借鉴的是对象上下文、透明工具调用、结果定位和审核流程，不复制专有代码、CAPL 生态或
厂商资产，也不宣称达到同等自动化成熟度。

### Wireshark-MCP：有界证据工具的设计参考

- [bx33661/Wireshark-MCP 官方仓库](https://github.com/bx33661/Wireshark-MCP)
- [项目的分析工作流说明](https://github.com/bx33661/Wireshark-MCP/blob/main/docs/prompt-engineering.md)

该开源项目封装 tshark，提供摘要、聚合、分页报文、帧详情和字段提取等工具；强调先统计后下钻、
使用真实输出验证、保留帧级证据，并区分读操作与生成文件的操作。它是通用网络分析 MCP，
不是 SOME/IP 专用智能体。

适合借鉴工具契约和结果限额，不等于本项目已安装或接通该服务。统计总量应使用聚合，不能
把分页样本当作整个抓包的分布。

### Wireshark 官方 SOME/IP 与 SD 字段

- [SOME/IP Display Filter Reference](https://www.wireshark.org/docs/dfref/s/someip.html)
- [SOME/IP-SD Display Filter Reference](https://www.wireshark.org/docs/dfref/s/someipsd.html)

官方字段覆盖 Service/Method/Session、返回码、Payload 错误和 TP 重组；SD 字段包括
Find/Offer/StopOffer、Subscribe/ACK/NACK、版本、TTL、端点及 Reboot Flag。

这些资料可用于分析规则和过滤器模板的校验。字段可用性随 Wireshark 版本变化，不能让模型
猜测字段名，也不能把 Wireshark 的完整解析能力当作本项目现有解析器的能力。

### someipy：原型与测试协议库，不是智能体

- [chrizog/someipy](https://github.com/chrizog/someipy)
- [官方文档](https://someipy.readthedocs.io/en/latest/)
- [许可证原文](https://github.com/chrizog/someipy/blob/master/LICENSE.md)

someipy 提供 Python SOME/IP/SD、客户端/服务端和 Payload 序列化。项目说明适合原型与测试，
并明确尚未覆盖完整规范。检索时仓库许可证原文是 **GNU GPL v3**，不能将其误写为 MIT 或
其他宽松许可证。企业发行前须对锁定版本、修改及分发方式做许可证审查；本轮不引入。

### COVESA/vsomeip：可选协议运行时

- [COVESA/vsomeip](https://github.com/COVESA/vsomeip)
- [官方架构与通信模式说明](https://github.com/COVESA/vsomeip/wiki/vsomeip-in-10-minutes)
- [官方配置文档](https://github.com/COVESA/vsomeip/blob/master/documentation/vsomeipConfiguration.md)

vsomeip 提供 SOME/IP、SD、配置和 E2E 等原生模块，仓库许可证为 MPL-2.0。官方明确说明它
不负责应用数据结构序列化，并指向 CommonAPI SOME/IP 绑定。它是运行时底座，不是智能体；
未来需要通过适配器和互操作测试接入，不能仅添加依赖便宣称获得完整 ARXML 仿真能力。

## 3. 建议工作流与证据契约

以下为设计候选，不是本轮已实现清单。每项是否可用，应以对应接口、前端入口和测试为准。

| 场景 | 必需证据 | 预期输出与边界 |
|---|---|---|
| ARXML 导入体检 | 模型、解析告警、引用与类型支持信息 | 缺失映射、支持范围及可定位对象；不静默补造 ID |
| 当前报文解释 | 选中帧、头字段、Payload、模型映射 | 头字段与业务含义，明确已解码/未解码部分 |
| 无报文或无波形排查 | 连接状态、过滤器、信号键、采样范围 | 区分未采到、被过滤、非数值、模型未匹配 |
| SD 订阅链路诊断 | Find/Offer/Subscribe/ACK/NACK、TTL、端点 | 可见时序和缺失证据；不凭局部窗口断言 ECU 故障 |
| 周期与错误分析 | 指定范围内帧、确定性统计、模型周期 | 异常候选、阈值来源、证据样本；不捏造周期要求 |
| 仿真方案生成 | 类型、范围、已有任务、执行器能力 | 可审核计划与预检结果；不自动发送真实网络流量 |
| 测试与复现辅助 | 接口定义、捕获事实、确定性断言 | 前置条件、激励、预期与复现步骤；LLM 不独立判通过 |
| 模型版本差异 | 两版模型、稳定对象键与工程标识 | 对解码、仿真和测试的影响；缺少基线则明确告知 |

建议工具结果包含：

- `scope`：工程/模型标识、服务/方法、时间范围、来源和采样边界；
- `summary`：服务端确定性计算的计数或统计，不由模型心算大批数据；
- `evidence`：稳定帧 ID、对象键、时间戳和必要字段；
- `limitations`：缓存截断、无基线、未解码或尚未支持的能力；
- `next_actions`：结构化的定位或计划建议，不能携带任意脚本执行内容。

结果应明确来自“当前有界缓冲区”还是“完整离线文件”。未观察到 ACK/响应，应表述为“在当前
证据范围内未观察到”，而不是直接判定服务故障或测试失败。

## 4. 前端嵌入原则

- 使用可收起的工作台侧栏，并保留独立诊断工作区；避免聊天面板永久挤占报文和波形主体。
- 显示随请求发送的当前页面、服务、方法、选中帧和范围标签；允许用户取消上下文。
- 从服务/报文/信号旁进入分析时显式携带对象，不要求用户反复复制 ID。
- 回答采用“结论、证据、限制、下一步”结构；证据可定位 Trace，建议可联动过滤或波形。
- 新消息不能无条件打断用户的历史阅读；长表格、代码和工具结果使用局部滚动或折叠。
- 错误、停止、工具执行与回答完成分别显示；保留已输出内容，不把断流当作成功。

以上是交互设计要求，不意味着所有联动已完成。

## 5. 权限、隐私与供应链边界

1. 默认只读。仿真计划先展示目标、数值、周期、持续时间、模式与能力限制，经校验和确认后
   才能执行。虚拟内部仿真与真实 UDP/硬件发送必须分开授权。
2. 授权绑定具体计划参数；执行前重新检查模型、任务和参数，不能把一个复选框视作任意后续
   写操作的无限授权。
3. 不开放任意 shell、任意路径写入或任意目标地址。文件操作限制在明确的工程目录；网络
   目标、速率、持续时间和停止机制由确定性执行器限制。
4. ARXML、PCAP 内容和工具结果都是数据，不是指令。不得根据文件中的文本改变权限或泄露
   密钥。发送到模型的数据应最小化；默认使用摘要和必要样本，不上传整个抓包或工程。
5. 审计记录工具、输入摘要、结果、授权及异常完整堆栈；密钥不进代码、日志和模型上下文。
6. 提示词不替代后端策略；LLM 负责解释和规划，协议正确性、参数验证和执行边界由代码负责。

### 已发现的上游风险

[Wireshark-MCP 安全公告 GHSA-3r68-x3xc-rxpg](https://github.com/bx33661/Wireshark-MCP/security/advisories/GHSA-3r68-x3xc-rxpg)
列出 `<= 1.1.5` 的 `export_objects` 在未设置允许目录时存在任意路径写入风险；检索当日公告
显示没有已修复版本。该公告不等于所有版本和配置必然可被利用，但足以阻止未经审计直接
集成。本轮仅参考设计，不安装该服务。

检索还发现名为 `VectorCANoe/CANoe-mcp` 的仓库，但其名称不能证明 Vector 官方身份。没有
厂商官方页面的背书时，不将其作为官方实现或成熟度依据。

someipy 与 vsomeip 的许可证结论只属于工程初筛，不构成法律意见。独立进程隔离也不能自动
消除许可证义务；发布前仍须审查锁定版本、SBOM、源码修改及实际分发方式。

## 6. 核验记录

- GitHub CLI 检索因未登录不可用；Exa 路由所需 `mcporter` 未安装，使用官方网页与项目原始
  仓库页面补充检索；未为此新增登录、安装插件或配置凭证。
- 已直接核对 someipy 的许可证文件与 vsomeip 仓库许可证说明。
- 已检查 `docs/open-source-selection.md` 和 `docs/canoe-comparison.md`：两者未包含 someipy
  许可证的错误陈述，故不为此次研究改动它们。
- 未调用用户模型密钥；未新增外部 MCP、someipy 或 vsomeip 依赖。
- 本文不作为实现验收记录。实际交付仍应通过接口测试、权限拒绝测试、流式异常测试和浏览器
  对象联动验收，分别记录通过项与未实现项。
