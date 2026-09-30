# 开源底座选型与许可证风险

## 1. 决策摘要

截至 **2026-09-19**，MVP 采用“纯 Python 可审计核心 + 可插拔原生数据面”：

- 保留现有 `lxml` 解析目标 ARXML 子集，`dpkt` 处理 PCAP，FastAPI 提供控制面；
- 自研的代码仅覆盖项目特有的规范化模型、信号映射、安全策略和工作流，不重写成熟的 XML、
  HTTP、加密、UI 图表等通用能力；
- COVESA `vsomeip` 作为后续高保真运行时适配器，不在 MVP 中强耦合；
- `pysomeip` 用于测试/互操作参考，暂不直接作为生产内核；
- GPL 组件不嵌入闭源发行包；Wireshark 可作为独立开发分析工具。

许可证判断是工程风险初筛，不构成法律意见。发布前仍需法务复核、SBOM 与逐文件许可证扫描。

## 2. SOME/IP 与 ARXML 候选

| 项目 | 能力与维护信号 | 许可证 | 结论 |
|---|---|---|---|
| [COVESA/vsomeip](https://github.com/COVESA/vsomeip) | C++ SOME/IP、SD、E2E；Linux/Windows 构建；行业使用广 | MPL-2.0 | 适合作为可选原生运行时。修改 MPL 文件并分发时需提供相应源代码与通知；与闭源模块保持清晰边界 |
| [afflux/pysomeip](https://github.com/afflux/pysomeip) | asyncio、wire format、SD 与 socket；README 明确列出 SubscribeAck/Nack、TCP 订阅等缺口 | MIT | 适合参考实现、测试 oracle 或轻量适配；不能直接假定生产完整性 |
| [autosar-data-py](https://github.com/DanielT/autosar-data-py) / [autosar-data-abstraction](https://github.com/DanielT/autosar-data-abstraction) | Rust AUTOSAR 模型的 Python 绑定；抽象层覆盖 Ethernet、PDU、信号、SOME/IP transformation、E2E 等常用域 | MIT OR Apache-2.0 | 当自有投影解析达到覆盖瓶颈时的首选评估项；先验证目标 ARXML、Python/Windows wheel、API 稳定性与包体 |
| [cogu/autosar](https://github.com/cogu/autosar) | Python AUTOSAR XML 生成与部分解析，面向 Classic；新 API 文档/覆盖仍演进 | MIT | 更适合生成与补充适配；系统描述解析尚不足以直接替换当前数据面模型 |
| [Technica-Engineering/FLYNC](https://github.com/Technica-Engineering/FLYNC) | 以声明式模型覆盖 SOME/IP、拓扑、TSN、安全、E2E 等 | Apache-2.0 | 新兴项目，适合研究中间模型与校验思路；引入前需稳定性和格式锁定评估 |
| [Open SOME/IP Specification](https://github.com/some-ip-com/open-someip-spec) | 公开核心 RPC、SD、Publish/Subscribe、TP 规范；说明 TLV/E2E 等边界 | Community-Spec-1.0 + 附加条款 | 作为规范参考；贡献或复制文本前必须阅读项目附加条款 |

`vsomeip` 的仓库许可为 MPL-2.0，但项目治理、支持窗口和贡献策略可能变化。企业采用时应固定已
验证版本/提交，保留源代码镜像、许可证文本和安全维护方案，不能只跟随 `master`。

## 3. 当前依赖为什么足够完成 MVP

| 依赖 | 用途 | 许可证关注点 |
|---|---|---|
| [lxml](https://github.com/lxml/lxml) | 高性能 XML、禁用实体/网络、XPath/命名空间 | BSD-3-Clause；保留通知 |
| [dpkt](https://github.com/kbandla/dpkt) | PCAP/PCAPNG 与常见链路/IP/传输层 | BSD 风格；发布时以实际 wheel 中 LICENSE 为准 |
| [FastAPI](https://github.com/fastapi/fastapi) | 类型化 REST/OpenAPI/WebSocket 入口 | MIT |
| [Pydantic](https://github.com/pydantic/pydantic) | 配置和 API schema 校验 | MIT |
| [HTTPX](https://github.com/encode/httpx) | OpenAI 兼容网关与更新下载 | BSD-3-Clause |
| [cryptography](https://github.com/pyca/cryptography) | Ed25519 更新签名验证 | Apache-2.0 OR BSD-3-Clause；关注原生 wheel 与出口合规 |
| [keyring](https://github.com/jaraco/keyring) | Windows Credential Manager 等系统密钥库 | MIT；不同平台 backend 能力不同，失败时不得明文回退 |
| [Apache ECharts](https://github.com/apache/echarts) | 大规模交互式波形与图表 | Apache-2.0；保留 NOTICE |
| [React](https://github.com/facebook/react) | Web UI | MIT |
| [PyInstaller](https://github.com/pyinstaller/pyinstaller) | Windows 可执行目录 | GPL-2.0-or-later with Bootloader Exception；保留许可并审计 hooks/捆绑依赖 |
| [Inno Setup](https://jrsoftware.org/isinfo.php) | Windows 安装/升级/卸载 | 自定义许可，允许商业发行但不是 SPDX 常见许可证；发布前归档许可文本 |

优先用现有依赖的具体原因：

- `lxml` 已能安全、容错地抽取本工具当前所需的 ARXML 投影；引入完整模型库不会自动解决
  OEM 方言、引用完整性与 SOME/IP 部署覆盖；
- `dpkt` 足以做离线 PCAP 的低开销解析，避免为几个协议层引入更重依赖；
- ECharts 已具备波形、缩放和高数据量绘图基础，无需自研 Canvas 图表引擎；
- HTTPX 与 cryptography 已覆盖模型网关和签名更新需要，无需自行实现 HTTP/密码算法。

## 4. 暂不嵌入的项目

### Scapy

[Scapy](https://github.com/secdev/scapy) 功能强、包含 SOME/IP contrib，适合交互式构包和研究，
但主项目为 GPL-2.0。闭源企业发行包若直接链接/捆绑会引入强 copyleft 风险。本项目不嵌入；
开发者可在独立环境用于生成互操作测试流量。

### Wireshark 与第三方 dissector

[Wireshark](https://gitlab.com/wireshark/wireshark) 与若干 SOME/IP dissector 通常采用 GPL。
推荐把 Wireshark 作为独立进程/开发工具，不复制代码到产品。若分发 Wireshark 或插件，需独立
履行 GPL 源码、通知和对应源代码义务。

### 未知或无许可证仓库

没有明确许可证不等于“可自由使用”。这类仓库只能用来定位公开概念，禁止复制代码、样例、
测试向量或资源进入产品。例如 [ESR Labs someip-payload](https://github.com/esrlabs/someip-payload)
当前 GitHub 元数据未识别到许可证，在取得明确授权前不得复用其代码。

## 5. 高性能与交互分析参考

- [ESR Labs Chipmunk](https://github.com/esrlabs/chipmunk) 是 Apache-2.0 的日志分析工具，可参考其
  大文件流式处理、过滤、时间线与插件设计；它不是完整 SOME/IP 仿真栈，也不建议整仓改造成
  本项目的前后端底座；
- 当性能数据证明 `dpkt` 成为瓶颈后，可评估 Rust 的
  [pcap-file](https://github.com/courvoisier/pcap-file)、
  [etherparse](https://github.com/JulianSchmid/etherparse) 和
  [someip-parse-rs](https://github.com/JulianSchmid/someip-parse-rs) 组合，并通过窄接口接入；
- 未做基准前不因“可能更快”引入 Rust/PyO3 工具链。先建立同一批 PCAP 的吞吐、内存、错误
  容忍和解析一致性基线，再决定替换。

## 6. 架构隔离策略

```text
Proprietary UI / Domain / Agent / Test Workflow
                     │ 稳定 IPC/gRPC/JSON 契约
          ┌──────────┴──────────┐
          │                     │
 Pure-Python adapter      Native vsomeip adapter
 (in-process)             (prefer separate process)
```

- MPL/GPL/厂商 SDK 分别作为独立构建单元，许可证文件随制品归档；
- 原生适配器优先独立进程，接口只传规范化数据，不暴露第三方类型；
- 每个适配器维护支持矩阵、上游版本、补丁集和互操作测试；
- 插件崩溃、超时或版本不匹配时，控制面仍可读取工程与离线 PCAP。

隔离有助于维护和合规，但不能自动消除许可证义务，最终判断应由法务结合实际链接、修改与分发
方式作出。

## 7. 供应链门禁

发布候选必须完成：

1. Python 与 npm 依赖锁定、哈希校验和干净环境重建；
2. 生成 CycloneDX 或 SPDX SBOM；
3. OSV/Dependabot 等漏洞扫描，高危漏洞需阻断或有书面豁免；
4. 许可证扫描覆盖源码、wheel、npm 包、PyInstaller 收集物和安装器；
5. `THIRD_PARTY_NOTICES` 与许可证原文随安装包分发；
6. 产物 SHA-256、构建来源、提交、工具链和签名证书可追溯；
7. 对 vsomeip 等原生依赖保存对应源代码、构建脚本与本地修改。

## 8. 引入新依赖的检查表

- 当前依赖是否已经提供所需能力？
- 上游最近发布/提交、维护者数量、Issue 响应和安全政策如何？
- 是否有明确许可证、NOTICE、专利或商标附加条款？
- 是否改变闭源分发、动态链接、SaaS 或修改后分发义务？
- Windows/Linux、x86_64/arm64 是否有可验证构建？
- 包体、启动时间、吞吐、内存和攻击面增量是否有数据？
- 能否通过适配器隔离并提供替代实现？
- 是否新增网络下载、编译器、驱动或管理员权限？
