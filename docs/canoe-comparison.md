# CANoe SOME/IP 能力对标

## 1. 调研范围

本文仅基于截至 **2026-09-19** 可公开访问的 Vector 产品资料进行功能级对标，不使用逆向
工程、非公开文档或受限资产，也不声称本项目通过 Vector 或 AUTOSAR 认证。

公开资料显示，CANoe 是覆盖分布式系统分析、仿真、激励、测试和诊断的通用平台，支持 SIL、
HIL、自动化测试、图形/文本结果分析以及接口集成。CANoe.DiVa 的 SOME/IP 能力公开列出：
基于 AUTOSAR XML / Franca IDL 生成测试，覆盖 Find、Offer、Subscribe、Methods、Events、
Fields，以及服务可用性、参数期望值、状态组合、网络/电气故障和 DTC 验证。

主要来源：

- [Vector CANoe 产品页](https://www.vector.com/en/product/canoe/)
- [Vector CANoe.DiVa 产品页](https://www.vector.com/en/product/canoediva/)
- [Vector MICROSAR QuickCom SOME/IP 帮助](https://help.vector.com/QuickCom/current/en/Help/html/some_ip.html)

## 2. 能力矩阵

状态含义：`MVP` 为当前仓库已有基础实现，`下一阶段` 为已排入近期路线图，`远期/插件` 为
需要专门协议、硬件或合规投入，`非目标` 为不尝试复制厂商专有生态。

| 能力域 | CANoe 公开能力参考 | SOME/IP Agent 策略 | 状态 |
|---|---|---|---|
| 网络描述 | 数据库驱动配置；DiVa 支持 ARXML / Franca IDL | ARXML 安全导入与规范化服务模型；Franca 适配器后续加入 | MVP / 下一阶段 |
| SOME/IP 基本操作 | Methods、Events、Fields | 报文编解码，方法请求/响应、事件与字段模型 | MVP / 下一阶段 |
| Service Discovery | Find、Offer、Subscribe | SD 条目/选项编解码，provider/consumer 生命周期与 TTL 状态机 | MVP / 下一阶段 |
| 总线仿真 | 节点、通信过程、剩余总线仿真 | 多 ECU provider/consumer、周期/条件信号源、确定性种子 | 下一阶段 |
| 激励与故障注入 | 期望响应、网络与电气错误、状态组合 | 延迟、丢包、重复、乱序、错误返回码、TTL/会话异常；电气故障交给硬件插件 | 下一阶段 / 插件 |
| 在线分析 | 图形/文本分析与 Trace | 报文表、筛选、服务/信号解码、WebSocket 实时流 | MVP / 下一阶段 |
| 波形 | 图形化结果与信号分析 | ECharts 波形、游标、缩放、导出、服务端降采样 | MVP / 下一阶段 |
| 记录与回放 | 测量、分析与可复现测试 | PCAP/PCAPNG 导入；时间缩放/过滤回放需安全门控 | MVP / 下一阶段 |
| 自动化测试 | 可复用测试、vTESTstudio、CI/CT、SIL/HIL | YAML/JSON 场景、断言、JUnit/HTML 报告、CLI/REST 自动化 | 下一阶段 |
| 诊断 | ECU 诊断；DiVa 面向协议与 DTC 验证 | DoIP / UDS / diagnostics-over-SOME/IP 作为独立插件域 | 远期/插件 |
| 硬件接入 | Vector 网络接口、台架与实时能力 | 通用 NIC 优先；Npcap、AF_PACKET、硬件时间戳和厂商设备走适配器 | 插件 |
| 安全访问 | OEM 安全扩展与 Security Manager 集成 | 企业认证、RBAC、密钥库、目标白名单、审计；OEM 算法仅通过授权插件 | 下一阶段 / 插件 |
| 智能体 | DiVa 20 公开描述测试结果 AI 分组与解释 | 工程问答、报文解释、场景生成、异常归因和工具调用审批 | MVP / 下一阶段 |
| 扩展编程 | CAPL、C#、Python 与工具接口（视产品/选件） | Python 插件 SDK、REST/WebSocket、声明式场景；不兼容 CAPL | 下一阶段 |
| 报告与追溯 | 结果筛选、比较、注释、需求关联 | 工程哈希、场景版本、审计 ID、JUnit/HTML、需求映射接口 | 下一阶段 |

## 3. 优先追平的工作流

### P0：数据库驱动的可解释通信

- 从 ARXML 定位服务、实例、端点、事件组和 payload 类型；
- 报文列表可下钻到 SOME/IP 头、SD 条目和信号；
- 未解析引用和不支持的数据类型必须明确告警，禁止静默给出错误值。

### P1：可复现仿真与分析

- provider/consumer 一键生成；
- Find/Offer/Subscribe/SubscribeAck 生命周期可视化；
- 常量、阶跃、斜坡、正弦、随机、CSV 和表达式信号源；
- 监控筛选、冻结、波形、标记、导出与 PCAP 时间线联动。

### P2：测试自动化

- 场景前置条件、动作、等待、断言、清理；
- 正常/边界/无效值和协议异常模板；
- CLI 无界面运行、并行隔离、JUnit/HTML 报告；
- 将 ARXML 哈希、程序版本、配置、随机种子和证据报文写入报告。

### P3：台架与企业集成

- NIC/抓包/硬件时间戳插件；
- HIL I/O、XCP/CCP、诊断与需求系统连接器；
- LDAP/OIDC、RBAC、审批、审计导出和离线部署；
- CI 工作节点、许可证/资源调度和集中制品管理。

## 4. 差异化方向

本项目不应逐像素复制 CANoe，而应优先发挥开放架构优势：

1. 可审计的自然语言到测试场景/过滤器/波形配置；
2. 在 Linux、Windows、容器和 CI 中使用相同的 API 与工程格式；
3. 公开的插件接口，隔离不同协议栈、硬件和 OEM 扩展；
4. 可导出的中间模型与测试证据，降低厂商锁定；
5. 核心协议与 AI 解耦，断网或禁用模型时仍可完成确定性测试。

## 5. 明确不承诺的等价能力

- 不承诺与 Vector 专用硬件、CAPL、vTESTstudio 或专有数据库格式兼容；
- 不承诺未经验证的实时性、硬件时间戳精度或总线满载能力；
- 不把基础 SOME/IP 编解码等同于 AUTOSAR、TC8、ISO 26262 或 ISO/SAE 21434 认证；
- 不实现或分发未获授权的 OEM 安全算法；
- 不把 LLM 输出作为测试通过、功能安全或合规判定的唯一证据。
