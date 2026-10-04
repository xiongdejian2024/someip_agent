# 本轮交付范围与收尾依据

本表按用户当前明确要求核对，不把路线图上的未来能力自动加入交付门槛。
用户于2026-10-01明确排除后续IPv6改动；Bootes与Windows也不在当前范围。
既有IPv6 UDP/TCP能力保留，不删除已有代码；IPv6分片仍拒绝并记录异常。
全AUTOSAR/OEM覆盖、任意厂商扩展和固定8/24/72小时长稳不是本轮自动追加的要求。

## main 新增工作台与正式发行的区别（2026-10-04）

2026-10-03 新一轮 P0–P2 开发已增加工程保存／恢复、产品自动场景与 CLI、连续记录／
只读回放、结果比较／完整证据，以及真实 ARXML 动态激励与公共时钟控制；详情和逐项
验收见 [P0–P2 交付记录](p0-p2-delivery.md)。这不是修改下方 0.1.2 历史发行基线。
CI 37138783052 的源码／安装包完整原生后端各 878 项及原生虚拟网、升级回滚已通过；
包含新冻结 CLI 门禁的 CI 37140970491 也已成功，原始报告和成功／失败／取消／篡改证据
已核对。技术验收完成；新的保存／加载视觉验收仍待补，整个目标尚未完成。
main 保持产品版本 0.1.2，但本轮没有重签或替换正式 Release，也没有操作正式密钥。
不能因源码版本号相同，就宣称公网 v0.1.2 附件已含全部新增功能或最新 CI 已完成。

2026-10-04 用户追加的 VLAN／网卡环境和保存／加载独立页已实现于 main；独立工程页
e682bdc、环境后端 2d47572、跨业务生命周期保护 d0744c6、独立环境页 d0ff926 分块提交并
推送。当前本地完整原生后端 911 项通过、零失败／跳过，真实隔离网卡 3 项通过，不当作
硬件标签帧或浏览器视觉证据。[新增基线 CI 37172363275](https://github.com/xiongdejian2024/someip_agent/actions/runs/37172363275)
正在运行；前端构建与新增环境页面门禁已成功，后端／原生构建和安装发行验收尚未全部结束。
两个独立页的真实交互与有效桌面／窄屏截图仍待验证，因此本轮追加后的整体验收未完成。
旧 CI 的绿色状态不能替代追加功能的最新整套验收，正式 0.1.2 Release 与密钥保持不变。

继续补验已实际复现并修正工程加载初始化竞争、取消后线程仍执行、以及解除绑定按钮误套
主机网卡写权限的问题。块 28／29 的当前完整原生后端 917 项通过、零失败／跳过，真实
网卡 3 项通过；更早的 CI 37172363275 虽仍运行，但不含这两块修复，不称为最新代码成功。
最新整套 CI 及两个独立页面的真实交互／有效视觉仍未完成，目标保持 active。

## 当前收尾结论（2026-10-02）

### Pi 改造：0.1.2 已正式发布

Pi 执行链路、无插件/Skills 的内置运行时、受控控制台 API、页面命令、生命周期和审计
已分块开发提交并推送。旧自研模型循环已移除；具体接口和权限边界见 `docs/pi-agent.md`。
四处产品版本和前端 lockfile 已统一为 0.1.2，不覆盖已有 0.1.1 Release。

正式发行基线为 `160f53793781dc2d1838b6db59c4e118251e65e7`，
[CI 37012384581](https://github.com/xiongdejian2024/someip_agent/actions/runs/37012384581)
已 completed/success。实际日志确认源码/安装包各 266 功能、615 后端、59 审计/名称与
12 权限/清理通过，CTest 5 项、16 短负载阶段、38 真实发行包升级/回滚及干净运行均通过。
Windows 按范围跳过；不将普通非原生后端的 119 跳过与完整原生口径混淆。
[签名准备 37023180116](https://github.com/xiongdejian2024/someip_agent/actions/runs/37023180116)
成功后创建草稿，重新下载三份附件并逐字节核对，再公开为 latest。
[v0.1.2](https://github.com/xiongdejian2024/someip_agent/releases/tag/v0.1.2)
已于 2026-10-02 15:03:57 UTC 公开，标签准确指向上述 CI 基线。
完整 x86_64 ZIP 为 155195456 字节，SHA-256：
`947bc5a60a9b27713967960537b5857e59552793b87445ad770e96d9fa42ad21`。
通过产品自身逻辑、不携带登录凭据复验公网 latest 清单、正式签名、HTTPS 重定向和完整下载哈希。
ZIP CRC、版本、内置 Node/Pi/原生/升级器摘要、执行权限与许可均另行核对。
签名沿用仓库外保存的私钥，不重新生成、不随客户端发布。
本地独立 Linux 安装包后端 615 项通过、零跳过；升级与失败回滚 38 项通过。
最新清理构建上下文的发行包复验在
`build/pi-012-linux-update-clean-context` 与 `build/pi-012-linux-context-clean-evidence`：
命令退出 0，干净容器无系统 Python/Node/npm/Pi/SDK，包内 Node 22.19.0 实际执行
Pi 模型/工具循环与 HTTP 取消清理，原生 payload 解码和网页/发生器验收通过。
这些本机 aarch64 制品只是隔离证据，正式附件使用上述 GitHub x86_64 基线 ZIP。

真实浏览器另验证模型工具导航、控制台 API 页面命令消费者和未授权写拒绝，测试空间已关闭。
仅使用本地模型网关夹具，不消耗企业模型配额，不将其结果当作所有模型兼容性证明。
车型源冲突、Bootes、Windows 及新增 IPv6 仍不在本次改造范围。

### 已完成的 0.1.1 正式签名发布

本轮密钥生成、签名发布与正式按钮升级请求已完成：正式签名密钥保存在仓库外受限目录并同步Actions Secret，
[v0.1.1](https://github.com/xiongdejian2024/someip_agent/releases/tag/v0.1.1)
已公开。正式包来自成功CI36960055104的9fdaadf提交；公网产品验签、SHA-256、ZIP
CRC与版本检查通过。独立Linux安装目录的真实页面按钮完成0.1.0→0.1.1升级，
页面Console/健康接口均为0.1.1，安装ID`c9cc6af6fbc043bc83e0746cf05d373a`状态complete，
备份VERSION为0.1.0。证据在`build/production-release-0.1.1-evidence`。
默认Actions令牌创建Release实际HTTP403；工作流只准备签名制品，公开发布采用
已经核验成功的授权CLI流程，不新增GitHub登录令牌Secret。

车型源差异按用户要求保留、不解决，未宣称实际车型全部运行通过。Bootes、Windows、
新增IPv6与全AUTOSAR/OEM覆盖不在本轮范围。以下历史过程保留，涉及正式信任源尚缺、
未发布或目标active的旧记录以本节最新结论为准，不把旧夹具当正式发行包。

## 原始要求与当前证据

| 要求 | 当前状态 | 可核验依据与限制 |
|---|---|---|
| 使用vsomeip重构底层 | 在线协议、发生器、监听、PCAP及观测payload均已接入原生 | 固定vsomeip 3.5.10；libpcap读取、libtins TCP/IPv4重组；SD复用上游原生模型，业务payload复用项目已有原生Codec，无Python解码回退。真实业务ARXML布局仍需覆盖，不能因此宣称所有车型服务可用 |
| 虚拟以太网逐功能验收 | 正式包基线CI已成功 | CI36960055104源码与安装包各266功能、593后端、59审计/名称和12权限通过；CTest5项、38项完整包升级/回滚及干净无Python/SDK运行通过，不宣称规模长稳或车型全量互操作 |
| 参考SAT soa_partner调用方式 | 二进制/socket/字典接口已实现并回归 | SOAOperator/S2sBaseClass；字典初始化client/server、成员通道、RPC/事件/字段、回调及恢复。控制长度帧与成员连续JSON契约保留；本轮575项安装包后端回归通过 |
| 点击升级按钮升级最新版 | 正式GitHub源真实按钮已验收 | 公网签名/哈希与隔离安装目录0.1.0→0.1.1成功，页面、健康、关联安装状态和旧版备份分别核对；失败回滚另有历史真实按钮证据 |
| 每轮修改提交GitHub | 按轮提交推送，分别报告各提交CI | 正式包9fdaadf的CI36960055104和发布器4ee90e7的CI36963589527均success；后续文档/工作流提交不得冒充已发布包源码 |
| 服务名来自ARXML和指定comm配置，不用SAT业务服务 | 名称/Service ID已只读核对，真实运行覆盖未完成 | V6.12.0与H47A/V_6_12_0共131个服务同名同ID。SAT仅参考接口；名称一致不证明payload/部署已可启动 |

## 车型限制与升级过程记录

### 1. 用户业务源的实际运行验收

`build/large-array-dispatch-source-evidence/names.json`中，131个源服务名称/ID一致，
73个明确VSA类型图全部解析；仍记录103条布局错误和177条部署错误，
`runtime_verified=false`。这些是诊断条目数量，不是“还差280个功能”。

本节保留尚未完成的车型实际运行边界；用户延期的是源冲突，不把它扩大成所有布局问题均已解决。
后续如需车型实测，应以业务实际需要的成员为对象，补齐Classic部署、文本与长度元数据解释，
再从原ARXML/comm名称生成隔离测试目录，执行SAT字典初始化、RPC/事件/字段和独立抓包
验收。不得复制车辆端点到宿主网卡、凭名称猜测序列化、用SAT示例业务替代。
这不等于承诺实现所有AUTOSAR/OEM扩展，但未支持的用户业务输入必须保持可见。

2026-10-02 新的逐事件组审计另发现两份业务源存在部署差异：330 条 comm 通知声明对应
323 个唯一事件，ARXML 投影有315个；262个EventGroup集合一致、53个不一致、8个缺失。
证据为`build/classic-event-groups-source-retry-evidence/names.json`。
已修复解析器把服务全部组复制到每个事件的问题，改为完整 provider/routing 引用逐成员绑定；
这不消除源文件之间的矛盾，也没有解除Classic门禁。用户已明确要求车型源冲突先保留、
暂不解决；不将它作为本轮发布渠道配置的阻塞项，不修改两份源或擅自选择优先级。
仍不能以名称一致宣称车型业务运行完成，发布说明须保留兼容性限制。

### 2. 真实升级按钮与正式最新版源

`frontend/src/pages/SettingsPage.tsx`已接通检查、验签后显示升级按钮、安装与健康恢复轮询。
用户已选择现有仓库的GitHub Releases作为发布源，不再等待发布平台选择。
2026-10-02用户已授权生成正式Ed25519签名密钥、保存本地并同步到Actions Secrets。
已配置正式信任根及发布流水线，0.1.1需重新构建、验收再签名发布；不能将测试密钥
或此前测试0.1.1夹具作为正式最新版发布，也不需要用户在聊天中提供私钥。
最新版清单按Linux架构使用固定附件名称；版本ZIP使用不可混淆的版本标签下载地址，
具体格式见`docs/linux-packaging.md`。当前没有正式Release，不宣称正式源已经可下载。

已在独立测试安装目录完成真实浏览器点击、下载/验签、升级重启与显示新版本的端到端
验收，证据位于`build/browser-update-917bbff-retry-evidence`；启动失败后的真实回滚另在
`build/browser-update-rollback-917bbff-evidence`。两项JUnit各通过一项，页面与健康版本核对
一致。成功用例当时脚本清理失败（只读夹具chown）单独保留，修正后回滚命令退出0；
8项脚本门禁/清理回归分别在macOS与Linux通过，不把替身命令测试冒充真实点击。
上述旧证据的回滚页面当时仅有通用提示。后续`build/rollback-status-browser-retry-evidence`
真实点击已显示“升级失败，已回滚至v0.1.0”，恢复后页面服务正常，状态/健康与备份分别核对，
JUnit一项通过、命令退出0；正式发布信任源仍未配置。
`build/rollback-status-browser-success-evidence`另以真实点击确认自动重载为0.1.1，
健康/状态/旧版备份断言通过，JUnit一项通过、命令退出0；
`build/rollback-status-linux-retry-evidence`34项升级回归完整通过、零跳过，1项既有warning。
页面SSR、API/升级器和真实浏览器操作证据各自有效，不能相互冒充。

## 已核验的上一提交CI

截至2026-10-01 12:58 UTC，提交
`78ae35368c76c538c18a4e19cd26bdaa2223a054`的
[CI 36861199457](https://github.com/xiongdejian2024/someip_agent/actions/runs/36861199457)
为completed/success，前端、后端、原生/虚拟网、Linux发行包、升级与干净运行均通过。
Windows按当前范围跳过，不作为失败项。已读取实际任务日志，不仅依据作者描述：

- 源码与安装包各266项虚拟网、47项审计/名称和12项权限/清理回归通过；
- 两次后端完整回归各431项通过，3项既有warning保留；
- 既有1316组黄金向量、16组IPv4分片、40组选项以及双身份/恢复审计通过；
- Linux x86_64完整ZIP构建成功，19项升级/回滚测试通过，干净运行通过；
- 独立测试0.1.1只用于升级夹具，不证明正式最新版已发布。

本地查询保存于`build/scope-closeout-evidence/prior-ci-status.json`与
`prior-ci-summary.log`。每个提交的证据单独保留，后续变更不能沿用此前结果。

## 2026-10-01 后续复验边界

原生接收有界FIFO、现有Boost十六进制转换和无人消费trace的优化，中间镜像完整安装包
266虚拟网、435后端及48审计/名称通过，但并行复测仍出现大数组成员断连。
随后采用Asio公开完成条件避免默认64KiB反复续写，4项CTest及3轮各10项大数组专项通过。
该版本完整虚拟网266项通过，采集却丢61帧，因此整次命令失败；未称后续审计/后端通过。
失败证据保留于`build/ipc-large-write-installed-evidence`。功能验收tcpdump缓冲改为64MiB，
全流量范围与零丢包门禁不变，必须新目录复验；产品IPC与性能脚本参数不变。

Classic原始header属性另保留2223条去重记录，仍有103布局错误和177部署错误，
不猜OEM枚举或解除车型服务初始化门禁。正式升级信任源也仍待确认，完整目标未完成。

后续64MiB独立复验`build/ipc-large-write-capture64-installed-evidence`已完整退出0，
7份JUnit共266功能、435后端、48审计/名称及12权限，零失败/错误/跳过。
同一原生二进制的`build/ipc-large-write-performance-evidence`16阶段均verified：
1600次RPC、13198条已产生事件逐序号完整核对，16份采集内核丢包均零；不代表严格周期或长稳。
aed13eb的CI36881157501为completed/success，源码/安装包各266功能与435后端、16负载阶段、
19发行包升级测试及干净运行通过，日志保留在`build/ipc-large-write-ci-evidence/ci.log`。

2026-10-02 已核对上一升级状态提交`908cf05dabbda4afdded6f716983b8a790539284`的
[CI 36887461838](https://github.com/xiongdejian2024/someip_agent/actions/runs/36887461838)：
completed/success。实际日志`build/rollback-status-ci-evidence/ci.log`确认源码/安装包各
266项虚拟网、450项原生后端，16个短负载阶段verified、34项Linux升级/回滚及干净发行包
验收通过；普通非原生后端370通过/80跳过是另一口径。Windows按当前范围跳过。
这是上一提交的证据，不作为本轮EventGroup修改的新CI结果。

2026-10-02 已核对EventGroup提交096eef7的CI36892657351为completed/success；
实际日志在`build/classic-event-groups-ci-evidence/ci.log`，源码/安装包各266虚拟网与466后端，
16短负载阶段、34Linux升级/回滚及干净发行包通过。

后续SD原生模型迁移的本地独立安装包验收位于`build/sd-final-installed-evidence`：
266虚拟网、530后端、59审计/名称与12权限/清理通过，7份JUnit无失败/错误/跳过；
26628帧采集内核丢包0。监听/捕获/离线及智能体不再用Python解码SD字节，缺元数据明确报错。
这不代表Python信号解码、车型源差异或正式升级信任源已解决；该SD版本CI仍须独立跟踪。

源码ZIP版最终镜像另在`build/sd-source-zip-backend-evidence`完成533项安装包后端回归，
零失败/错误/跳过；原生二进制和wheel构建SHA与上述266项虚拟网验收一致，未重复宣称新跑。
`build/sd-linux-source-zip-update-evidence`的34项真实签名升级/回滚全部通过，
`build/sd-linux-source-zip-clean-evidence`确认无Python/SDK的网页及原生发生器运行通过。
全部命令退出0；此前目录归档版32通过/2失败和完整异常日志继续保留，不放宽准备超时。
正式最新版发布信任源仍未配置；这些测试夹具结果不证明正式在线最新版可下载。

2026-10-02 监控/PCAP与智能体的原始payload解码已迁到同一原生Codec，生产模块不再调用
旧Python SignalCodec；Python仍负责比例换算、统计及业务回调，本地socket并非零开销。
本轮build/native-payload-installed-evidence完整退出0，266虚拟网、575后端、59审计/名称、
12权限/清理通过，7份JUnit零失败/错误/跳过；26601帧采集内核丢包0。
build/native-payload-linux-update-evidence的34项真实发行包升级/回滚通过；
build/native-payload-linux-clean-final-evidence在无系统Python/编译器/SDK镜像中，经真实HTTP
导入现有ARXML/PCAP黄金夹具，验证标量、嵌套、截断错误、原始payload和真实返回码，保留原生堆栈。
这些人工夹具不证明车型业务源已全部运行，正式更新信任源也仍缺；新提交CI另行跟踪。
