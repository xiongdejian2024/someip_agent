# vsomeip 重构执行记录

目标：在线 SOME/IP 通信、SD、订阅、方法、字段与周期仿真由 vsomeip 原生进程负责；Python
通过二进制和 socket 调用，保持 SAT 的 SOAOperator / S2sBaseClass 字典初始化及测试接口。
在 Linux 虚拟以太网上验证真实报文与每项功能，不以 mock 代替端到端证据。

## 2026-09-30

- 基线：工作树干净，提交 e113aa1；后端 58 项测试通过。
- SAT 参考：`/Users/xiongdejian/project/python_project/sat/test_case/soa`；实际实现来自
  `/Users/xiongdejian/project/python_project/ecu-simulator/ecu_simulator/soa_partner/src`。
- 已核对：控制通道 8 位十六进制长度 + JSON；`start_config` 字典，`start_config_get_args`
  增量启停；返回每个成员 socket 地址；成员通道为连续 JSON；request/event/response 的
  args/result 使用 JSON 字符串；事件名 `Update{Name}Event`；ServiceStatus 状态通知。
- 主机为 macOS arm64，Docker Linux 引擎已启动；虚拟以太网验证在隔离 Linux 容器完成。
- GitHub CLI 未认证，公开上游代码读取使用官方网页及匿名 git clone。
- 开始原生 C++ / Boost.Asio 桥接；在线协议状态机委托 vsomeip；离线 PCAP 解码保留分析用途。

## 2026-10-01

- 原生核心已在 Linux arm64 编译，CTest 的基础类型、字节序、数组与非法输入测试通过。
- 修复增量客户端丢失 TCP 配置、并发等待者互抢响应、定时器取消后继续发包的竞态。
- 修复原生 Listener 生命周期：accept 回调持有安全生命周期，不再用无限 retired_ 列表保活。
- 默认 SimulationManager 移除 Python 周期生成/编码/发包循环，改为控制二进制并读取原生监控。
  二进制缺失明确返回 503；监控标记 vsomeip_api，不把提交记录当线上抓包。
- 最新 `make native-test`：20 项 SAT 风格 UDP/TCP 功能测试 + 2 项默认后端授权/拒绝订阅测试通过。
- 最新 `make native-regression`：76 项后端测试通过，原生集成禁止跳过；前端构建成功。
- 抓包审计发现旧 Python SD Option 长度错误；对照 vsomeip 固定源码修复，新增 IPv4 Endpoint
  黄金字节测试。修复后 PCAP 中 Offer/StopOffer/Subscribe/Ack 与 UDP/TCP 黄金字节审计通过。
- 在线升级增加独立安装、prepared 确认、重启验版本、失败回滚和连续升级归档；本地真实进程测试
  验证 HTTPS 下载、Ed25519、哈希、API 触发、旧程序退出和新版本启动。
- 原生发生器后续 tick 异常会传到监控通道，默认后端标记异常并停止二进制，不把已停发生器显示成运行中。
- Windows 构建脚本增加固定 vsomeip、原生二进制/DLL 和独立升级器；尚未在 Windows 上执行。
- CI 增加原生核心和虚拟网作业，上传证据；尚未提交/运行远程 CI，不声称远程绿色。

## 2026-10-01 原生在线监听迁移

- 移除 `runtime/network.py` 的 UDP/TCP socket、逐报文 SOME/IP 解码路径；新增原生 `--network`
  模式，通过既有长度帧控制启动/停止和监控。复用固定 vsomeip 3.5.10 的导出解码器，而不是
  将 Python 自研解码器简单翻译为 C++。独立构建需指定匹配源码目录，库版本锁定 EXACT。
- Python 只管理生命周期、对象化监控与展示层 SD/信号分析；缺少二进制明确 503，无 Python 回退。
  端口冲突返回 422，不影响已有监听；最后一个监听停止后结束原生进程并释放端口。
- UDP 支持连续 SOME/IP、非法报文恢复、IPv4/IPv6；TCP 支持拆包/粘包、连接隔离、半帧截断和
  10 秒半帧超时，空闲连接不触发该超时。进程崩溃后状态标记失败，允许重启。
- 资源边界：128 个 TCP 连接，监听单帧少于 2MiB，IPC 单帧 4MiB、输出队列 1000 帧/16MiB。
  队列或帧超限记录原生堆栈；不把这些静态上限当作压力测试或性能证明。
- 验收容器原本禁用 IPv6；仅在临时网络命名空间启用，未改变宿主机或已有 Docker 容器。
- 抓包原先在结束前尚有数据停留在捕获缓冲；开启 tcpdump immediate-mode 后重新捕获并通过审计，
  保留 UDP、TCP、IPv4 组播、IPv6 UDP/TCP 黄金字节，不以监听 API 返回值替代线上证据。
- 最新 `make native-test`：CTest 1 项 + SAT 功能 20 项 + 默认后端 2 项 + 双栈监听 5 项均通过。
  PCAP 审计通过；仅为监听测试的短 TCP 流复核连续字节，不宣称产品级任意 TCP 重组。
- 最新 `make native-regression`：94 项通过，无原生集成跳过，含真实在线升级/回滚测试。
  Ruff、12 个源文件 mypy、版本一致性、前端类型检查/构建及 diff 空白检查通过。
- 前端监听面板和文档明确区分原生端口监听与网卡被动抓包。未运行浏览器端到端点击验收、
  Windows 实机验证、远程 CI 或性能基准；所有改动仍未提交。

仍必须继续的门禁，完整目标保持 active：

1. 已接通 Ethernet 被动抓包、成熟 TCP 与 IPv4 分片重组库；仍需 IPv6 分片、完整 IPv4 选项、
   离线 PCAP 原生迁移、更多链路类型、完整流量矩阵与长稳验收。
2. 已打通明确基础类型的 ARXML 到 catalog、服务配置和 Python client/server 初始化；仍需完整
   序列化/拓扑模型和服务页面/API 生命周期，不仅基础类型样例或单信号发生器。
3. 已补齐基础 SAT 周期发送、字段缓存、组合断言、实例命名、WTI 六接口和按请求关联的超时审计；
   已接通原始 PartnerKeyInfo 和 S2s 自有进程异常恢复；仍需心跳/阻塞检测、独立应用身份和全部
   OEM 用例的语义矩阵，不能将进程退出恢复泛化成所有任务/设备的活性恢复。
4. 所有发生器、数据类型、故障注入、长稳、多服务、多实例和异常重启的虚拟网矩阵。
5. Windows 原生构建、DLL 运行、完整安装/升级/回滚实机验证；实际发布源及供应链锁定/许可归档。
6. 相同机器、负载和监控策略的吞吐/丢包/抖动基准；当前短功能测试不作为性能承诺。
7. Linux 临时安装目录已实测升级按钮、签名、重启和版本刷新；仍需真实 Windows 发行包与生产更新源验收。

## 2026-10-01 SAT 虚拟网失败修复与语义核验

- 本次先检查已有 JUnit：30 项用例有 4 项失败，而后端 111 项回归已通过；未把未完成的运行
  记为成功。失败是 UDP/TCP 相同值周期字段观测、增量客户端字段初值各两项。
- PCAP 证明无效更新后旧值仍持续上网。对照固定 vsomeip 源码定位接收字段默认去重，使用公开
  `subscribe_with_debounce` API 显式取消去重，不修改上游代码，也不用 Python 生成假通知。
- 将协议栈字段缓存跨成员 socket 建立窗口交付；只在可用状态转变时订阅，订阅触发的初值仅交给
  等待初值的逻辑成员。修复重复初值污染旧客户端历史，以及 request_event/release_event 引用不对称。
- 周期测试禁止调用 Python 逐周期发送函数，并检查连续相同值、非法更新后旧计数继续、停止后计数
  不增长及对端无新通知；保留全部黄金字节审计，不删除失败断言。
- 自动响应默认改为 SAT 自定义处理器语义，支持实例后缀。缺少处理器不默认 echo，原 echo 互通测试
  改为显式 `echo=True`；新增单测与真实 UDP/TCP 超时用例。
- 原生正常响应解码异常返回相关的 DESERIALIZATION_FAILURE 并记录堆栈；空 ERROR 保留原始
  头和 payload 返回 OTHER_ERROR，不吞掉相关响应。虚拟网验证后续正常调用仍可成功。
- 最新 `make native-test`：CTest 1 项、SAT UDP/TCP 38 项、默认后端/双栈监听 7 项通过，PCAP
  审计通过，包含字段/getter、周期值、ERROR 和故意畸形 RESPONSE 黄金字节。
- 相关 Python 单测 24 项、12 个源文件 mypy、全后端 Ruff、版本一致性与 diff 空白检查通过。
  最新 `make native-regression` 114 项通过，无原生集成跳过；另有现有依赖弃用和测试序列化两项警告。
  网卡被动抓包的成熟重组库仅做能力调研，尚未加入依赖或实现。
- 完整目标仍 active：上述验证只覆盖列出的功能，不代替前述七项门禁、Windows 实机、
  浏览器升级按钮或吞吐/丢包/时序压力验收。未提交、未发布、未操作真实车辆网络。

## 2026-10-01 被动捕获联动与升级按钮实测

- 接通原生 `network_interfaces` / `network_start(mode=pcap)` / `network_stop`，枚举、捕获和
  TCP 重组均在二进制；Python 只控制和对象化展示。端口监听与捕获共享固定 vsomeip 解码器。
- 新增 libpcap 与 libtins 原生依赖，先检查现有依赖能力，再复用成熟 StreamFollower，而不是
  新写 TCP 序号/重传算法。Linux 实测 libtins 4.0-1+b1、libpcap 1.10.3，版本来自镜像包信息。
- 控制/API/前端区分捕获与端口监听；真实端点、软件时间戳、内核/接口丢包、资源上限与终止状态
  明确展示。BPF/网卡配置错误不影响现有来源；Fatal 资源错误只停止所属捕获，停止后释放流缓存。
- 最新虚拟网：CTest 2 项、SAT 38 项、默认后端/监听/捕获 12 项通过，均无失败或跳过。
  其中五项捕获测试使用独立客户端与已经占用端口的服务，覆盖双栈 UDP/TCP、过滤与异常隔离。
  PCAP 独立核对全部八组双向黄金字节；保留既有方法、字段、周期与错误报文审计。
- 原生构建第一次遇到 Docker Hub TLS 超时；复用已在本机验证的固定提交 SDK 构建成功，
  未重试真实网络发包、未改用 Python 仿真。默认 Dockerfile 仍提供完整上游源码构建路径。
- 一轮回归误与编译并发，重链接窗口产生 22 项执行权限失败；保留真实异常后串行重跑成功。
  `native-regression` 已增加编译前置关系，不能再把无效并发运行当成功报告。
- 全后端回归 118 项通过、无原生集成跳过；保留依赖弃用与故意序列化异常的两项已有警告。
- ego-browser 真实操作监控页：选择回环网卡/BPF、启动、双向通信、显示两条黄金报文、查看
  `41280000` Payload、停止。截图保存在 `build/virtual-evidence/capture-ui-*.png`。
- 真实升级浏览器测试使用临时安装目录及一次性签名 HTTPS 发行源，不升级用户实际安装。
  点击“检查更新”及“升级到最新版本”，独立升级器重启至 0.2.0，旧 0.1.0 备份保留、status=complete。
  发现设置页重启后版本徽标仍默认 0.1.0，改为后端 health 版本；实测侧栏及设置页均显示 0.2.0。
  证据在 `build/virtual-evidence/update-ui/`，JUnit 一项通过；只读挂载导致 pytest cache 写入警告，
  不影响升级断言。此证明不替代 Windows 安装器、DLL、驱动或实际发布源验收。
- Windows 增加 libtins/libpcap 和许可归档；核对官方 vcpkg port 后，明确要求配置 Packet_ROOT 的
  独立抓包 triplet，拒绝缺该配置的完整发行构建，避免 null 后端伪成功。未运行 Windows 实机。
- UI 测试进程和两个临时容器已清理，未操作四个 ats 用户容器，未提交或发布制品。
- 完整目标仍 active：完整 ARXML 映射、剩余 SAT 辅助/身份语义、IP 分片/离线路径、故障注入、
  长稳/性能、Windows 实机和生产更新源仍需继续；不以当前绿色用例缩小原目标。

## 2026-10-01 ARXML 原生目录与服务初始化

- 本轮先核对实际工作树：原生仿真已存在，完整 ARXML 目录初始化尚未接通。前一轮只读分析
  未新增实现；本轮继续原目标，不把 Python 原型分析当作重构完成。
- 服务、方法、事件、字段和实例按完整路径绑定，不再按短名称或部署名称后缀猜测；保留同名
  服务与一个接口的多个部署，显式 Major=0 不改成 1。INOUT 同时加入输入和输出。
- 用现有 lxml 解析真实 SW-BASE-TYPE 编码/位宽和 Implementation 引用链，不新增依赖。
  缺失、循环、不支持的类型保留浏览告警和异常堆栈，禁止利用名称推断构造发送配置。
- 新增目录生成组件、`POST /api/v1/model/native-catalog` 和 `S2sBaseClass.from_arxml`。
  导出接口不发包；实际初始化保存模型 SHA-256、部署路径及配置，使用原生二进制和既有控制
  socket/SAT 字典，未增加 Python 协议回退。在线模式复用发送开关、主机和 SD 白名单。
- 字段 Getter/Setter/Notifier 和事件组映射进入目录；原生固定类型处理请求、响应和通知。
  前端使用部署路径区分相同接口的多个部署，并显示路径；未声称完成服务启停页面验收。
- 新增虚拟网测试从真实 ARXML 生成两端目录，而不是复制手写 JSON：UDP/TCP 的方法、
  普通事件、字段 Getter/Setter/Notifier、显式大端/小端和编号客户端实测通过；独立 PCAP
  核对新增 32 个黄金向量。
- 已执行 `make native-test`：CTest 2 项、SAT/ARXML 虚拟网 42 项、后端/监听/捕获 12 项通过，
  无失败或跳过；原有 SD、周期、错误、捕获和双栈黄金检查全部保留。
- 第一轮全后端回归 130 项通过；后续又补充循环、缺失版本、冲突 ID、显式序列化属性、
  非法方向及长前缀 DOCTYPE 拒绝测试，最终全后端回归 137 项通过，无原生集成跳过。
- 全后端 Ruff、五个相关文件严格 mypy、前端类型检查/构建、服务适配器验证已通过。
  首次 mypy 未指定配置导致 lxml stubs 误报；指定现有配置后定位并修正已有布尔返回类型。
  本机原生用例明确跳过，Linux 容器用例已实际执行，不将本机跳过计作成功。
- 验收镜像重新构建成功，固定 SDK 源码提交检查和镜像内 CTest 均通过；镜像 ID 为
  `sha256:bf017b05227a020c929934e97b5c7465a8059f5338442aeaa856f6db466c3265`。
  不挂载源码、不设置 PYTHONPATH，确认目录组件来自 `/opt/venv/.../site-packages`，安装后的
  包执行 19 项 ARXML/初始化测试全部通过、无跳过；证据在 `packaged-arxml-catalog.xml`。
  这是 Linux 验收镜像，不是已经签名发布的 Windows 安装包。
- 当前自动映射是基础类型 scalar profile，不是完整 AUTOSAR 序列化实现：应用类型、复合类型、
  字符串、长度/对齐、TLV/E2E、端点拓扑及完整页面/API 仍有门禁。遇到已声明而未映射的序列化
  属性直接拒绝，不以默认布局冒充支持。全部目标继续 active，Windows/性能/生产发行未验收。

## 2026-10-01 SAT WTI、方法耗时审计与升级回滚就绪

- 核对实际 SAT `base_partner.py` 的六个 WTI 辅助接口、`method_default_timeout=5.1`、
  `method_is_timeout` 及 `ck_method_timeout()`。WTI 按既有事件和 Getter 编排，Python 不新增
  协议实现；保持状态转字符串、自动驾驶服务选择、新事件筛选和负向断言之后的 0.2 秒 Getter。
- 同步耗时用单调时钟按 correlation_id 关联，不按方法名覆盖同名并发；正常迟到响应和原生
  超时均审计。异步/无响应方法不审计，原生 ServiceStatus 显式提供无响应方法元数据。
  记录/在途容量有界，溢出不能消失成成功；在途超时被检查后即使清空记录仍会重新检查。
  方法耗时包含控制 IPC 和业务处理，不是硬件网卡时延或性能基准。
- 修正无事件/无特定提示断言：保留无关历史，覆盖完整观测窗口；socket 断开立即失败，不能
  把失去观测能力当作没有事件。初始化记录实际实例、字典和启动配置；原始 PartnerKeyInfo
  构造方式、心跳/自动重启及独立 application 身份仍未完全移植。
- `make native-test` 通过：CTest 2 项，SAT/ARXML/WTI veth 52 项，后端/监听/捕获 12 项；无
  失败或跳过。WTI 两服务 × UDP/TCP 各运行六接口；额外覆盖默认阈值、异步、无响应方法、
  同名并发一快一超时和观测时 socket 断开。PCAP 审计新增 24 个 WTI 人工黄金向量，原有
  20 个基础向量、32 个 ARXML 向量以及 SD/监听/捕获检查全部保留并通过。
- 第一轮全后端回归有 154 项通过、1 项失败：旧版本启动后、HTTP 监听就绪前立即访问导致
  ConnectionRefusedError。定位到实际升级器只恢复目录并 Popen，不验证旧版本恢复健康。
  增加旧 VERSION 与回滚进程健康确认；回滚健康失败记录完整堆栈并报告双重失败，而不是
  重跑或放宽测试。实际回滚用例移除等待 started.json 的间接证据，直接要求返回时 HTTP 已健康。
- 修复后 `make native-regression` 为 156 项通过、无跳过；已有 FastAPI/httpx 弃用和类型夹具
  Pydantic 序列化两条警告仍存在。Ruff、五个相关模块严格 mypy、版本一致性和 diff 空白检查通过。
- 验收镜像已重新构建，固定 vsomeip SDK 源码提交复核及镜像内 CTest 均通过；新镜像 ID 为
  `sha256:1fbf45b1a18686ab89df4aad23f25167b0d4809b99d9b3e8dc337a8efdf4b42d`。
  无源码挂载、无 PYTHONPATH，确认 timing/WTI/updater 来自 `/opt/venv/.../site-packages`。
  安装后 SAT 与真实升级/回滚测试共 48 项通过、无跳过，保留一条已有 FastAPI/httpx 警告；
  JUnit 证据为 `build/virtual-evidence/packaged-sat-updates.xml`。只挂载证据输出目录，不借用源码导入。
- WTI 测试使用明确的动态数组/UTF-8/四字节长度布局，不代表完整 OEM WTI 或 AUTOSAR
  序列化已经支持。完整底层、Windows 实机、性能、真实生产更新源等门禁仍保持未完成。

## 2026-10-01 SAT 成员恢复、共享事件注册与启动就绪修复

- 接通 SAT 原始 PartnerKeyInfo 四参数构造及独立状态；未连接对象明确报错，关闭安全幂等。
  S2s 仅监督本实例拥有的进程，沿用 5 秒检查及 0/1/-9 不恢复的策略，重启限制为 3 次/60 秒。
  恢复活动字典、回调、自动响应和最新周期配置，不复活已停止成员，不重放业务调用与历史。
- 订阅按原生成功回执维护和恢复；取消的事件不自动重新订阅，未知事件列表整份拒绝。
  correlation_id 等待者保留原有响应缓存；接收回调不等待自身线程，也不持有生命周期锁等待回执。
- 客户端恢复扩展到与服务端相反的物理节点。第一次验收 52 项通过，第二组 20 项中有 2 项
  客户端 UDP/TCP 失败；PCAP 证明通知已上网，客户端日志证明 vsomeip 因没有事件组订阅而丢弃。
  对照固定 SDK 的 routing_manager_impl：同一 ClientID 的 request_event 会去重，不逐次增加引用。
  之前假定每个逻辑成员增加引用的释放策略不成立，现改为首个消费者注册、最后一个退出才释放。
  不修改上游源码、不增加超时、不删除增量启停操作；停止成员时记录结构化日志。
- 服务端/客户端 × UDP/TCP 均使用真实 SIGUSR1 异常退出，核对新 PID、活动成员、订阅差异、
  周期通知、方法响应和无业务重放；另覆盖显式 kill 不恢复。新增共享事件测试验证停一个成员
  仍可接收、最后一个订阅者退出不破坏其他事件、全部成员停止后重新启动能重新订阅。
- 修复共享事件后，源码挂载的虚拟网和后端回归通过。随后无源码挂载的安装后验收又发现一次
  native.ready JSON 与 vsomeip 警告拼接：第一组 52 项通过，第二组 21 项通过、1 项初始化错误。
  失败证据保留在 build/packaged-virtual-evidence，未将这次运行记为全绿。
- 原生就绪 JSON 与换行改为一次输出；Python 按 JSON 文档边界读取完整就绪记录，兼容旧记录
  后拼接日志并警告。半记录等待，损坏 JSON/非法端口失败并记录堆栈。新增 9 项确定性回归，
  本机构造/监督单测 25 项通过；不会通过捕获并忽略 JSONDecodeError 掩盖启动失败。
- 最终源码验收：CTest 2 项、SAT/ARXML/WTI 虚拟网 52 项及后端/监听/捕获/恢复 22 项通过；
  全后端回归 181 项通过，无失败或跳过。保留已有 FastAPI/httpx 弃用和序列化夹具两条警告。
  Ruff、六个相关模块严格 mypy、版本一致性与 diff 空白检查通过。
- PCAP 保留全部基础、ARXML、WTI、SD、双栈监听与捕获审计，新增服务端/客户端 × UDP/TCP ×
  恢复前后请求/响应共 16 个黄金向量。该审计仍限完整可解析段存在性，不是吞吐/抖动基准。
- 验收镜像重新构建，固定 SDK 提交和镜像内 CTest 通过；镜像 ID 为
  sha256:c1b76f51fd3813f4105505a83962a9173e9893f5268085faf1d3d927022acfca。
  无源码挂载、无 PYTHONPATH，确认 info/supervision/partner/operator 来自 site-packages；
  安装后的 SAT、动态端口及真实升级/回滚测试 73 项通过，无跳过，保留一条已有依赖警告。
- 同一新镜像不挂载源码、不设置 PYTHONPATH，只挂载独立证据目录，完整重跑虚拟以太网：
  52 + 22 项全部通过，PCAP 黄金审计通过，包括上述 16 个恢复向量。最终证据在
  build/packaged-recovery-evidence；与保留失败的 build/packaged-virtual-evidence 分开，避免覆盖。
- 完整目标继续 active：心跳/暂停进程检测、独立应用身份、完整 ARXML 序列化/拓扑与服务页面、
  IP 分片/离线迁移、完整故障/长稳/性能矩阵、Windows 实机和生产发布源等门禁未完成。
  这轮没有重新做浏览器升级按钮实测；安装后升级测试不替代真实 Windows 发行验收。
  未提交、发布或操作真实车辆网络，所有虚拟网仅位于临时隔离容器。

## 2026-10-01 IPv4 分片原生重组与安装包路径验收

- 复用已有 libtins 的 IPv4Reassembler，没有在 Python 或 C++ 自写分片拼接算法。
  外围按单向地址、协议、标识及完整 VLAN 序列隔离；补齐重复去重、重叠/内容/末片/头长度
  冲突隔离、非法边界、128 上下文、256 唯一片段及 4MiB 跟踪 Payload 配额。
  隔离键保留至固定 30 秒超时，不因重复片段续期。捕获轮询执行维护，无新流量也清理残片。
- UDP 在重组后复用 vsomeip 头解码，TCP 接入已有 libtins StreamFollower。
  新增当前上下文/字节及累计重组/错误统计；停止和故障清理当前资源，保留累计计数。
  Python 和前端透传这些状态及促成本次消息的 IP 包来源，不把 TCP 历史分片数伪造成总数。
  页面用“分片上下文”而非“待重组数据报”，因为它包含已隔离的键。
- 本轮增加中文阶段日志定位了新增 CTest 失败：单 NOP 序列化为 `01 00 00 00` 选项，
  libtins 4.0 在到达最后字节前见 EOL 会抛 Malformed packet，尚未进入我们的头长度冲突检查。
  原始复现帧为
  `00000000000100000000000208004600002800722000801103b70a4d00020a4d000101000000a0287790001cc998456780030000000c000000000000`。
  [固定版本上游源码](https://raw.githubusercontent.com/mfontanini/libtins/v4.0/src/ip.cpp)与实际失败一致。
  GitHub CLI 无认证、Jina 读取超时后，使用网页工具读取公开主来源；没有安装包或修改上游源码。
  保留负向测试意图，改用四个 NOP 填满选项区，显式断言 24 字节头；所有直接模块夹具也先经过
  线上序列化/解析，避免默认 IHL 与线上值不一致。EOL 补零兼容性仍列门禁，不宣称完整选项支持。
- 最新源码 `make native-test native-regression` 通过：CTest 2 项、SAT/ARXML/WTI veth 52 项、
  后端/监听/捕获/恢复 veth 37 项、全部后端 183 项，无失败或跳过；保留两条既有依赖/夹具警告。
  新的 15 项分片用例覆盖 UDP/TCP 顺序、乱序、重复、重叠、缺片、合法 NOP 选项、头长度冲突，
  以及实际无流量 30 秒过期后复用同标识。PCAP 独立复核 15 组确定向量、头长度/选项和黄金字节；
  本轮源码过期复用实际抓包间隔为 30.095323 秒，不是模拟时钟测试或性能保证。
- Ruff、相关两个后端模块严格 mypy、版本一致性、前端类型/构建、服务及分片状态适配器检查通过。
  前端保留既有大 chunk 提示；本轮未做浏览器渲染或升级按钮点击验收。
- 验收镜像重新构建，固定 vsomeip SDK 提交与镜像内 CTest 通过，镜像为
  sha256:7cc10ae45761a420b025b895ceb269ba1ed1919afaea0a65d819077b1ff5a170。
  Python wheel SHA-256 为 a809203f4fbb925c183091eb8a61b7598dea818a6c102f9c96c037a4c33b4390。
  不挂载源码、不设置 PYTHONPATH，确认 network/models/operator/partner/supervision 都从
  site-packages 加载；安装后的完整后端回归通过 183 项，无跳过，包含升级/回滚端到端测试。
  同一镜像的安装包虚拟网 52 + 37 项也全部通过，PCAP 再次独立复核 15 组分片及 16 组恢复向量。
  无流量过期复用的实际抓包间隔为 30.139145 秒。证据独立保存在 build/packaged-ipv4-evidence，
  源码证据在 build/virtual-evidence；逐项复核两套 JUnit 的数量、零失败和零跳过，不仅看工具退出码。
  本机 Python 3.10 捕获控制测试另通过 6 项，保留两条既有依赖弃用警告。
- 完整目标继续 active：IPv6 分片、完整 IPv4 选项、离线 PCAP 原生迁移、完整 ARXML 序列化/拓扑、
  服务页面生命周期、SAT 心跳/独立应用身份、故障/长稳/性能矩阵、Windows 实机及正式发布源仍未完成。
  所有网络验收限临时隔离容器，不连接车辆，不修改宿主机网络或其他业务容器；未提交或发布。
