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

## 2026-10-01 离线 PCAP 原生迁移与真实虚拟网文件验证

- 上一只读分析回合复核了当前原生/控制面分工及未验证的性能边界；本轮继续原目标实施。
  离线适配器替换中断曾留下缺失文件，本轮先补齐并检查工作区，再进行实际集成验收。
- `native/src/offline.cpp` 用 libpcap 读取 PCAP/PCAPNG，复用同一 CaptureProcessor 和 vsomeip
  解码器；原生异步输出帧记录和完整结束统计。Python 只写临时文件、管理二进制与 socket、
  校验结果、聚合端点/SD 和监控，不保留 dpkt 产品解析或 Python TCP/IP 重组回退。
- 增加文件时间驱动的 IPv4 分片过期、EOF 残片/半帧报告、原始帧/消息/Payload 配额、
  失败整份不提交、结束统计一致性检查与确定的进程清理。离线来源标记文件时间和
  `wire_verified=false`，不冒充线上重放、实时捕获或硬件时延。
- 旧 mixed-TCP 夹具真实带 SYN 数据；最初导入失败揭示固定 libtins 4.0 首字节交付问题。
  未将夹具改成普通 ACK 来绕过：仅规范化库输入副本，保持原始帧字节/时间/序号来源不变，
  重组仍由库承担。原生及集成覆盖拆段、重传、IPv4/IPv6 和序号回绕；增加实际跨 veth 的
  IPv4 分片 SYN 数据用例，并独立核对原始 SYN 标志与黄金字节。
- 34 项离线产品集成全部通过：PCAP/PCAPNG、Ethernet/RAW/SLL、IPv6 及明确 IPv6 链路，
  纳秒时间、空文件、乱序/重传、半帧 EOF、文件时间过期、残片 EOF、非法/截断/不支持格式、
  聚合配额、二进制缺失 503、失败不提交与后续有效导入。首次新证据目录缺失导致 34 项
  setup 错误，保留 `build/offline-evidence/setup-failure-junit.xml`，修正目录后再验收，未将错误当通过。
- 源码验收：CTest 2 项、SAT/ARXML/WTI 虚拟网 52 项、监听/捕获/恢复 38 项、完整后端 215 项
  均通过，JUnit 逐项复核零失败、零错误、零跳过。后端包括升级/回滚端到端测试。
  真实 PCAP 原生导入核对 6534 原始帧、1461 消息、92 组黄金向量及 16 组分片场景；
  黄金向量覆盖 SAT、ARXML 两种字节序的端口、WTI 和两节点恢复身份，并核对八组捕获方向。
  同四元组/序号的独立 live 分片用例另按独立原始帧子集导入，不把重传伪计成新消息。
- 新镜像 `someip-agent-vsomeip:offline-test` 固定 SDK 提交核验、编译和 CTest 均通过，镜像为
  sha256:dfad77efcf101b79b91f3690607ea379ef11d08b7f156d8df728c7bcc3150e48。
  wheel SHA-256 为 bb700f543b9fbf7cf109d240b7a3a05286e9dbb79ee19f5f6fd0192bbfc4044f。
  不挂载产品源码、不设置 PYTHONPATH，确认 PCAP/network/SAT 模块从 site-packages 加载；
  安装包路径虚拟网 52 + 38 项和后端 215 项再次通过，无跳过，离线审计通过
  6307 原始帧、1508 消息、92 组黄金向量及 16 组分片。证据在 `build/packaged-offline-evidence/`。
- 新增可重复的 `make native-installed-test`：仅挂载测试和独立证据目录，先核对八个产品模块
  来自安装包，再运行虚拟网/原生离线审计和全部后端回归。使用上述镜像实际运行该入口，
  52 + 38 项虚拟网及 215 项后端再次通过，逐项复核零失败/错误/跳过；原生离线审计通过
  6356 帧、1572 消息、92 组黄金向量及 16 组分片。入口证据在 `build/installed-evidence/`。
  已接入 CI；远程 CI 尚未运行。
- 前端保留真实原生来源、链路类型及分片统计，演示和未知来源不伪标 vsomeip。
  Ruff、PCAP/API 四模块严格 mypy、前端类型/构建及来源适配器检查通过；大 chunk 提示保留。
  本轮未做浏览器页面渲染或升级按钮点击验证，不冒充前端端到端验收。
- 完整目标仍 active：IPv6 分片、完整 IPv4 选项、完整 ARXML 序列化/拓扑、服务页面生命周期、
  SAT 心跳/独立应用身份、故障/长稳/性能矩阵、Windows 实机和正式发布源仍未完成。

## 2026-10-01 服务页面原生生命周期与安装包验收

- 继续原目标，复核上一进程确实结束：原生 CTest 2 项、源码虚拟以太网 52+46=98 项、
  后端当时 219 项均通过。新增八项页面同 API 用例不是原有 SAT 用例的重复状态报告。
- 新增模型绑定的服务会话 API，复用字典实例化、二进制和控制/成员 socket；提供启停、状态、
  方法调用、事件发送、请求读取及按 request_id 人工响应。冻结模型 ID/SHA-256 与原生目录，
  新导入模型不替换已有会话；缺少二进制不回退，线上授权在启动前校验。
- 原生新增可选提交回执，成功只表示编码与 app 提交；普通方法仍等真实响应。旧 SAT 未请求
  回执的调用保持不变。非法事件参数原生拒绝返回 422，不以 socket 写入成功冒充发送成功。
- 首轮四项 API 集成有两项失败：field getter/setter 没有显式 fire_and_forget 字段，初始化状态
  构造抛 KeyError。失败证据保留 `build/service-evidence/field-contract-failure.xml`；按原生相同
  默认值处理并在注册会话前完成状态构造，复测通过，不遗留半注册会话。
- 增加故障身份保留/可释放和内部 Trace 来源测试；五项服务 API 集成均通过。
  明确 `active` 与 `running`，故障会话不会因 PID 为空而失去释放按钮。初始化取消等待后台线程
  完成后清理，避免取消后启动孤儿进程；连接关闭错误记录完整堆栈。
- 页面接通上述 API；明确字节序、部署实例及应用 ID，支持同一内部会话加入测试对端。
  不同内部会话仍隔离，不通过改变 routing 共用或自动业务 echo 绕过真实通信。
  等待 client 方法期间可切换 server 回复，最近八项操作结果避免并发提示互相覆盖。
- 独立 PCAP 审计新增 56 组页面 API 黄金向量，限制端口 30520-30523、实际 ClientID 和请求/
  响应/事件方向。原生离线审计同步核对，共 148 组黄金向量和 16 组分片。首次手动审计误填
  不存在的 traffic.pcap 明确失败，改用 run_virtual.sh 实际输出 soa.pcap 后通过，没有重发测试流量。
- 重新构建镜像 `someip-agent-vsomeip:services-test`，镜像 SHA-256
  `82464fe4f1078ff2eaf1047daa015f5360b1d5d715ad5b0aaa58f4e585fe38ac`；wheel SHA-256
  `9b6980f11a718afe0ed3087d4516408e3bfb0880aa9ce63bb458b980b7393239`。
  安装包入口只挂载测试，增加 services/API 的 site-packages 来源检查；输出目录可配置。
- 源码和安装包均核对 JUnit：虚拟网各 98 项，后端各 220 项，零失败/错误/跳过。
  源码 PCAP 7047 帧、1669 条消息；安装包 PCAP 6833 帧、1706 条消息。两者均通过 148 组
  黄金消息和 16 组分片审计。证据分别位于 build/virtual-evidence 和 build/packaged-services-evidence，
  不覆盖旧 build/installed-evidence 离线迁移验收。
- 使用 ego-browser 原有验收空间，真实 Vite 页面连接隔离 Docker 后端和 Linux 原生二进制。
  按钮实测导入 ARXML、初始化两个成员、SetSpeed Speed=384、读取待响应请求、人工 Accepted=true、
  显示真实方法响应、发送 24.5 事件，以及停止后 health 活动会话为零。内部 Trace 请求 0180、
  响应 01、事件 41c40000 均来自原生，内部不伪标线上抓包。原生日志、审计库及截图在
  build/service-ui-evidence；截图已经实际查看。浏览器仅内部模式，跨网卡证据由 veth 提供。
- Ruff、三个服务模块 mypy、前端类型/构建、适配器与版本一致检查通过；保留现有大 chunk 警告。
  每轮修改需提交 GitHub 的新增要求已纳入流程；先核查账号、仓库权限及推送预检，不保存凭据。
- 全目标仍未完成：完整 ARXML/OEM 序列化与拓扑、IPv6 分片和完整 IPv4 选项、SAT 心跳/阻塞检测
  及独立身份、故障/长稳/性能矩阵、Windows 实机安装升级和正式签名发布源仍需继续。
  离线 errors 数组最多显示前 100 条，各原生异常保留堆栈，不以数组长度代表全部错误数。
  所有网络操作仅在临时隔离容器，没有连接车辆或发布安装包。本轮修改与验收记录一并提交
  GitHub；远程 CI 状态单独核验，不将本地通过视为远程 CI 通过。

## 2026-10-01 CI 实际失败与抓包审计边界回归

- 上一轮实际提交并推送 `58f3524`，本轮工作区以该提交的远程 CI 为依据继续。
  run `36805951543` 的前后端任务通过；原生构建、98 项虚拟网及 220 项后端也通过，
  但安装包阶段不能新建 build/installed-evidence，报告上传不能遍历 backend-pytest。
  原因是 root 容器在宿主创建了输出父目录及私有 pytest 目录；整个 CI 结论为失败，
  Windows 任务因此跳过，不能将 Linux 单步绿色说成完整 CI 或 Windows 验收成功。
- 历史 run `36788458285` 的抓包审计报 tcp ordered [0, 0, 24]，原始制品未保留，不能断言
  当时唯一原因。确认审计只按地址/协议/IP ID 收集而漏查 MF/偏移；以当前真实 PCAP 的副本
  加入同 ID 完整 TCP 包，执行上一提交审计源码，确实复现相同错误。
- 独立审计与原生导入分片子集都要求实际 MF/偏移，且核对源/目标方向。新增六项回归：
  完整 TCP/UDP 的 IP ID 碰撞、其他来源/反方向分片不混入黄金向量；额外真实首片和缺失尾片
  仍严格失败。使用真实文件的测试副本，不将人为副本说成网络发送或设备故障注入。
- 首次临时目录缺失导致五项 setup 错误；第二次四项原生字节核对失败揭示复制器的默认
  snaplen=1500 不适合 veth 大帧。23 帧超过 1500 字节，恰好解释差异 8862 字节；仅修正
  测试复制器的 snaplen，不放宽原生原始帧/字节核对。失败报告在 build/audit-regression-evidence。
- Make 在宿主创建输出父目录，并向 root 验收传入调用方 UID:GID；EXIT trap 只将该证据
  目录归还调用方，保留 700/600 私有权限，不追随符号链接，也不覆盖原始失败退出码。
  五项实际 Linux UID 回归验证非 root 能读取报告和创建后续目录，非法所有者/根符号链接拒绝，
  清理失败保留原错误，目录内符号链接目标不改所有权。最初误用 Path.chown 的 setup 错误
  保留报告，改用标准 os.chown 后再验收。此类 shell 权限回归不替代协议互通测试。
- 修正后正式执行 make native-test：原生 CTest 2 项、虚拟网 52+46=98 项、审计回归 6 项和
  权限回归 5 项全部通过。独立 PCAP/原生审计核对 7094 帧、1670 条消息、148 组黄金向量、
  16 组分片；证据在 build/virtual-evidence，原始上一轮证据仍有独立安装包目录保留。
- make native-installed-test 使用未变更的 services-test 产品镜像，只挂载测试和证据，
  site-packages 来源核查、98 项虚拟网、6 项审计、5 项权限及 220 项后端全部通过；
  原生审计核对 6751 帧、1635 条消息、148 组黄金向量及 16 组分片。
  证据独立保存在 build/audit-permissions-full-evidence，各 JUnit 核对零失败/错误/跳过。
- Ruff、shell 语法及 diff 空白检查通过，CI 静态检查接入新的审计/权限脚本。本轮未改产品
  二进制、未发布制品，修复与验收记录一起提交 GitHub；新远程 CI 仍须独立核验。
  Windows 构建所需获授权 Npcap SDK/构建环境已向用户询问，不降格为 null 捕获后端。
  完整 ARXML/OEM 布局与拓扑、IPv6 分片/完整 IPv4 选项、SAT 活性/独立身份、故障长稳与
  性能矩阵、Windows 安装升级和正式签名发布源仍未完成，完整目标保持 active。

## 2026-10-01 原生控制活性与真实暂停恢复

- 沿用已有原生 ping，不重写 SOME/IP 数据面、不增加依赖。Python 通过独立短连接检查
  自己拥有的原生控制循环，不占用业务控制锁或消费业务回执。连接/发送/接收共享绝对时限；
  使用已连接数值地址避免 DNS 重解析。回执校验运行模式/协议，不兼容回执停止监督并记录堆栈。
- S2s 默认 5 秒检查、1 秒探测时限、连续 3 次失败才恢复。失败后成功清连续计数，支持
  liveness_timeout=None 保留仅退出检测；沿用 3 次/60 秒恢复预算及 0/1/-9 不恢复策略。
  持续无响应只结束仍由本实例拥有的同一 Popen 对象；attach 不启动监督、不控制远端。
  恢复活动配置、已确认订阅、回调和最新周期通知，不重放业务请求/响应或历史事件。
- 新增控制/监督单测 29 项，与既有 IPC/监督共 58 项通过：独立连接、业务锁占用、绝对
  时限、非法参数、网络模式、不兼容回执、短暂失败不重启、持续失败、恢复风暴限额、关闭不复活
  和进程对象身份变化。fake process 仅证明策略，不当作原生故障注入或协议互通证据。
- 新增四项真实 veth 故障注入：UDP/TCP × owned client/server。向临时容器自有子进程发送
  SIGSTOP，实际读 /proc 的暂停状态且 poll 仍为 None；监督恢复后核对旧退出码 -SIGKILL、新
  PID、配置/周期通知/取消订阅保留、在途请求失败且等待队列清空、业务只执行 57/99 各一次。
  关闭后进程与监督线程都结束，每组独立保存 liveness-*-audit.json。没有暂停或杀死宿主进程。
- 首次暂停验收 49/50 通过，owned client UDP 在信号送达前立即读 State 的断言失败；改为
  有界等待内核暂停状态后复核，未删掉实际暂停断言。失败证据保留 build/liveness-source-evidence。
  首次完整后端 238/240 通过，容器未启用 IPv6 回环导致两项 Errno 99；启用已有 Make 同等
  sysctl 后 240/240 通过。最终增加单测后源码完整后端 249/249 通过，无跳过。
- 复核上轮远程 CI run 36807760442：前后端、原生构建、源码虚拟网和后端通过，安装包首套
  WTI UDP Auto 事件超时，整体 failure，Windows skipped。原始制品实际已上传，下载保留在
  build/ci-648fd36-installed-failure；首次 rg 文件清单受忽略规则过滤，改为 no-ignore 后确认
  PCAP/日志完整存在，不能把搜索漏列说成证据缺失。
- 该 CI 抓包显示 Auto 服务于 03:02:49.074806 已 Offer，但第一次 SD 订阅/ACK 在
  03:02:49.514223/514375 才出现，期间没有对应 WTI 业务通知。客户端日志在 49.075
  已调用订阅，原测试只等固定 0.3 秒；证据支持订阅准备竞态，不等于已证明所有丢包原因。
  WTI 夹具改用有界空列表准备探针，收到实际通知后才做一次性业务断言；探针不计入黄金向量，
  不重试业务调用、不增加断言时限来掩盖故障。四项 WTI 用例在源码和安装包路径均重新通过。
- 独立 PCAP 增加 16 组恢复前后真实 57/99 请求/响应，黄金向量合计 164；另严格要求
  暂停 owned client 中排队的 123 不出现在 UDP/TCP 线上。第七项审计回归向真实文件副本
  人为加入 123 请求，必须拒绝，不能只凭 Python 回调计数宣称没有线上重放。
- 新镜像 someip-agent-vsomeip:liveness-test 核验固定 SDK 提交，编译及 CTest 2 项通过。
  镜像 sha256:152ff3d8a11087b8bbd361302fe8090afbcfca7349100488fe21bdec578d6bce，
  wheel SHA-256 为 8bca34544ef143fb248bd2dfb64091c11cb0570e491194a69fa4e1890c360acb。
  首次误将 SDK 标签 sdk-3.5.10 当作完整镜像名触发 registry TLS 超时；检查本地镜像后用
  someip-agent-vsomeip:sdk-3.5.10 正确复用，没有更换或放松固定版本检查。
- 最终源码与安装包各通过 52+50=102 项虚拟网、7 项审计回归、5 项权限回归；完整后端
  各通过 249 项。安装包正式入口仅挂载测试/证据、无 PYTHONPATH，11 个产品模块确认来自
  site-packages。逐份 JUnit 核对零失败/错误/跳过，所有临时容器已结束。
  源码 PCAP 原生导入核对 7528 帧/1780 消息，安装包为 7300 帧/1819 消息；两套均通过
  164 组黄金向量、16 组分片及无在途业务重放审计。证据分别为
  build/liveness-source-final-evidence、build/liveness-installed-evidence；源码后端独立报告在
  build/liveness-evidence/backend-final-regression.xml。新增异常/故障用例使原始统计包含预期
  拒绝记录，不将这些故障向量误说成全部原始帧都有效。
- 四个产品模块严格 mypy、Ruff、版本一致性与 diff 空白检查通过。前端未修改，本轮未做
  浏览器点击或 Windows 实机验收。厂商 heartbeat 单位/内部语义仍未知，不将控制循环活性
  等同于所有成员通道或车辆服务健康；不承诺硬实时、吞吐、丢包率和调度抖动性能。
  完整 ARXML/OEM 布局与拓扑、IPv6 分片/完整 IPv4 选项、独立应用身份、完整故障/长稳/
  性能矩阵、Windows 安装升级及正式签名发布源仍未完成，完整目标继续 active。

## 2026-10-01 ARXML 复合类型与显式序列化部署

- 先检查已有 lxml、类型解析器和原生 codec，未增加依赖、未重写 SOME/IP 数据面。
  Implementation STRUCTURE/ARRAY 按完整引用和声明顺序解析；固定数组必须明确长度，
  变长数组必须明确上界。应用类型通过唯一 DATA-TYPE-MAP 绑定 Implementation，不选
  歧义的第一个目标。类型图缓存深拷贝并限制深度/节点/字段数，避免不同信号相互污染。
- AP transformation 属性按完整服务元素引用绑定，明确大小端和数组/结构长度前缀。
  按官方 Manifest/Transformer/Communication Management 文档核对字段声明顺序和
  前缀表示正文字节数的语义；原生 codec 支持结构前缀、定长数组前缀和变长数组上界，
  仍由 vsomeip 处理真实 SOME/IP/SD。缺失、歧义、未知属性和未支持细粒度覆盖全部拒绝，
  不自动用 UI 默认大端覆盖源布局。复合类型不能进入现有标量信号发生器。
- 新增 17 项后端测试；相关 ARXML 单测本机为 38 通过/1 跳过，该跳过项需要 Linux 二进制，
  已在最终完整容器回归中执行。最终源码及安装包后端各 266 项通过，零失败/错误/跳过。
  既有 3 项弃用/预期序列化警告未掩盖为无警告通过。五个产品模块严格 mypy、Ruff、
  shell 语法、版本一致性和 diff 空白检查均通过；前端类型检查、适配器及构建通过，
  仍有既有的大体积 chunk 警告，未做浏览器交互或性能验收。
- 新的项目 ARXML 夹具包含嵌套结构、带前缀固定数组、有界变长数组、Application primitive
  映射、INOUT 方法和周期事件。8 项真实 veth 覆盖 UDP/TCP × 大端/小端 × 结构前缀 0/2
  字节，直接从 ARXML 生成两端目录；非法定长数组请求失败且不执行服务回调。
  另用 C++ 手工黄金字节核对编码/解码及变长数组超限、非法/不一致长度；CTest 2 项通过。
- 最终源码与不挂载产品源码的安装包各通过 60+50=110 项虚拟网、7 项抓包审计和 5 项权限
  回归。安装包无 PYTHONPATH，15 个产品模块确认来自 site-packages。所有 JUnit 核对
  零失败/错误/跳过；两套独立 PCAP/原生导入各验证 204 组黄金向量（新增复合类型 40 组）、
  16 组分片。源码为 8349 帧/1904 条消息，安装包为 8013 帧/1977 条消息；包含预期非法
  输入/故障帧，不把原始全部流量说成有效消息。证据分别保存在 build/composite-source-evidence、
  build/composite-installed-evidence；最终源码后端报告 build/composite-backend-final-regression.xml。
- 产品镜像 someip-agent-vsomeip:composite-test 复用已核验的固定 3.5.10 SDK，重新真实编译。
  最终镜像 sha256:334fa78f871eef21a5f83f823ba908f54c20d61618b415e954a9ba6dfb0de3c0，
  wheel SHA-256 为 37eb5cdc0fef5281ecf6877327f3facbcf6d866013d27a1b408fb16ef454f0f3。
  首次镜像构建后同步了信号字节序显示，再构建最终镜像并重新验收；不将首次 SHA 冒充最终制品。
- 上一提交 03ce324 的远程 CI run 36809647605：前后端及完整 Linux 原生/安装包任务通过，
  Windows 任务失败。原日志为 build-native.ps1 的乱码 UnexpectedToken/ParserError，
  不是已确认的 Npcap SDK 缺失。Microsoft 文档确认 Windows PowerShell 5.1 对无 BOM 的
  非 ASCII UTF-8 脚本按 ANSI 误读；CI 改用 pwsh 并在构建前解析 Windows 脚本。
  本机没有 pwsh，仅完成 YAML/shell 配置静态验证，远程语法和完整 Windows 构建仍待实际 CI。
  不为通过构建绕开获授权 SDK/triplet 门禁，亦未生成正式 Windows 发布包。
- 本轮支持的是明确受限的投影/profile，不是完整 XSD/OEM 认证；复杂应用 record/array 语义、
  字符串/BOM、union、TLV/E2E、细粒度上下文、非 8-bit 对齐、变体和端点拓扑仍未完成。
  较长数组/结构的向后兼容扩展跳过未实现；真实复合字段及全部前缀宽度组合未全部互通验收。
  IPv6 分片/完整 IPv4 选项、独立应用身份、完整故障/长稳/吞吐/抖动矩阵、Windows 实机
  安装升级及正式签名最新版发布源仍有门禁，完整目标保持 active。改动和记录一起提交推送，
  下一次远程 CI 状态独立核验，不将本机通过说成整体完成。

## 2026-10-01 源布局页面联动、内联二维数组与复合字段矩阵

- 核对 88f88fa 工作区和远程状态后继续，没有把上一轮局部通过当作整体完成。发现服务页面
  仍固定发送默认大端，明确小端 ARXML 会被后端拒绝；改为默认“遵循 ARXML”，主成员和
  内部对端都省略覆盖字段，明确大小端才发送。切换服务恢复源布局选项，配置转换单独成模块。
  原生目录已有冲突拒绝策略保持不变，没有放宽后端或改回猜测布局。
- 内联二维数组解析只读取当前维的 ARRAY-SIZE/ARRAY-SIZE-SEMANTICS。原有后代查询会将
  2×3 不同维长度视为歧义，也可能借用内层缺失元数据；补齐直接子节点读取及两项缺失拒绝
  单测。夹具加入真实 2×3 内联数组和复合 EnvelopeState 字段，验证 Getter/Setter/Notifier
  的类型、字段名和事件组从同一 ARXML 图正确生成；相关新增文件共 19 项后端单测通过。
- 真实 veth 矩阵扩展为 UDP/TCP × 大端/小端 × 结构前缀 0/1/2/4 × 数组前缀 0/1/2/4，
  共 64 项。数组宽度 0 的夹具明确改为固定数组，其余宽度包含上界为 3 的变长数组，不把
  非法的变长零前缀说成支持。每项执行方法、应用标量映射、周期事件、更新前 Getter、一次
  Setter、真实字段更新通知和更新后 Getter；先收到真实准备通知后才执行一次性业务。
- 每项还提交非法定长、变长超上界、内层维数请求（标记 tag=99），要求原生失败、服务业务
  回调不增加。独立手工黄金构造不导入产品 schema/Codec，三份固定十六进制锚点验证拼接；
  64 组共 768 个复合黄金向量，严格核对端口、方向和客户端身份。两次 Getter 的最低消息数
  同时传入原生离线审计，不能由重复 TCP 段冒充两次业务。任何非预期 Transform 请求都拒绝，
  不仅凭回调计数说没有发包。新增三项审计逻辑回归；首次静态检查发现 required 元数据误加
  到另一组向量的未定义名称，在完整验收前修正，未屏蔽 Ruff 或放宽黄金报文检查。
- 最终源码与不挂载产品源码的安装包各通过 116+50=166 项虚拟网、10 项审计、5 项权限、
  268 项后端回归；逐份 JUnit 零失败/错误/跳过。安装包 15 个产品模块来自 site-packages，
  无 PYTHONPATH。两套 PCAP/原生导入各通过 932 个黄金向量、16 组分片；源码 16618 帧/
  3139 条消息，安装包 16053 帧/3145 条消息。证据分别在 build/composite-matrix-source-evidence、
  build/composite-matrix-installed-evidence，源码后端报告 build/composite-matrix-backend-regression.xml。
  非法输入/故障帧是预期验证对象，不等于全部原始流量有效；这些不是吞吐/抖动基准。
- 产品镜像 someip-agent-vsomeip:composite-matrix-test 核验固定 3.5.10 SDK，并真实重新编译、
  CTest 2 项通过。镜像 sha256:167dd8d3e77def10b094d1e661521942f84a776da3de71cbc5f51d619de527bd，
  wheel SHA-256 为 ce8bc6d7fa746aa43a3ff57c0c0a25d7eae3a2d01fb36930b37205f3b9c8a59a。
  Ruff、严格 mypy、前端类型/构建及适配器检查通过；仍有既有 chunk 体积及后端 3 项警告。
- 使用既有 ego-browser 空间 5 实际操作新构建页面：在只暴露 127.0.0.1:18766 的临时容器
  导入明确小端模型，默认初始化 client/内部 server 对端，两成员 START/socket 已连接；
  捕获页面真实 POST，两成员均无 byte_order 覆盖。停止后明确大端初始化，被真实后端以源
  部署冲突拒绝；明确小端重新初始化成功，再实际停止，两成员 OFFLINE/socket 断开。
  只挂载前端构建及隔离证据，后端来自 wheel，无外部发送权限。浏览器因会话停止使旧 ref
  过期，刷新观察后用当前选择器继续，没有重复启动业务。临时容器停止前确认只剩 uvicorn，
  浏览器转空白停止轮询；完整目录、页面请求/观察和服务端日志保留 build/composite-matrix-ui-evidence。
  容器无系统凭据库的 NoKeyringError 堆栈也保留，未接入 LLM 或加明文凭据兜底。
- 88f88fa 的远程 CI run 36811911063 已终态：前后端和完整 Linux 原生/安装包任务通过，
  Windows 中文脚本语法也通过，证实上一轮 pwsh 修复有效。整体仍为 failure：Windows
  build-native.ps1 第 27 行明确要求 CaptureTriplet/OverlayTriplets/Npcap Packet_ROOT 配置，
  当前未提供；安装器/上传跳过。没有跳过 SDK 门禁、使用 null 捕获后端或宣称 Windows 已成功。
- 本轮证明明确投影模型的所列复合字段、数组形状和全部四种长度宽度组合；不证明任意 OEM
  类型、全部变体、细粒度上下文、字符串/BOM、union/TLV/E2E、拓扑或向后兼容扩展。
  独立线上应用身份、完整 IPv4 选项/IPv6 分片、故障/长稳/吞吐/抖动矩阵、厂商心跳内部语义、
  Windows 实机安装升级和正式签名最新版发布源仍有门禁，完整目标保持 active。本轮代码、
  测试与验收记录提交推送；新远程 CI 独立观察，不能用本机成功替代远程结论。

## 2026-10-01 分层短负载基线与发生器配置复制优化

- 确认工作树从 a8e8950 开始，仅继续本轮自有改动。现有 SAT 适配器、原生 PCAP 导入器、
  dpkt、pytest 和标准库足够测量，无新增包、自写 socket 客户端或协议栈。把既有 veth 建网
  抽成共享 helper，仍只在 --network none 临时容器使用，未修改宿主/车辆网卡。
- 新增安装包性能入口 make native-performance：UDP/TCP 各 4 个 RPC 阶段（32/1024 字节
  blob、1/4 并发、各 200 次）和 4 个事件阶段（Python 逐条/原生 sequence，10/1 ms，3 秒），
  共 16 阶段。完整保留 PCAP、手工黄金字节、逐序号线上/客户端核对、采集丢包、P50/95/99、
  软件时间、各自原生 PID CPU/RSS 和 Python 总 CPU，核对 PID starttime 与安装包真实路径。
  RPC 包含 Python 请求、socket、业务 echo 回调和返回，不能称纯原生延迟；Python 通知调用
  无提交回执，原生 emitted_count 也只是尝试通知数，均与真实线上/客户端观察分别比较。
- 优化前发现 generator 每 tick 深复制整个配置、generator 与 sequence；先保存同机基线，
  再改启动时一次复制 shared_ptr<const Json> 不可变快照和 const 引用。保留字典接口、
  定时器跳过策略与停止 epoch，新增空/缺失序列、调用方后续字典修改和停止后不再通知的
  UDP/TCP 回归。单次 1 ms 对照服务 CPU 从 UDP 51.5%/TCP 53.1% 降至 34.2%/35.2%，
  仍约 999 条/秒；低频 UDP P99 反而变差，不宣称所有抖动改善。原始数据、全部表格和
  计数/时间边界写入 docs/performance.md，不把短测当最大吞吐、硬实时、HIL 或长稳承诺。
- 第一份基线 profile-04 抓包内核丢 2362 帧，仅解出 93/2869 条事件，客户端却收到完整
  2869 条，明确标 observation_failed。保留 build/performance-baseline-evidence，去除
  逐 packet 的 -U 强制刷新、增加 16 MiB 缓冲，仍 immediate-mode、SIGINT 后等待 flush；
  重新测得 build/performance-buffered-baseline-evidence 和
  build/performance-optimized-comparison-evidence 各 4 阶段全部完整、采集丢包为 0。
- build/performance-installed-evidence 的完整 16 阶段均 verified：1600 RPC 成功返回，
  3200 条请求/响应与 13204 条事件逐序号核对，全部采集内核丢包为 0。线上间隔与客户端
  回调时间分别保留，TCP 合并消息的零间隔及并发 RPC 数字序号乱序不隐藏。verified 仅表示
  此次已产生消息完整核对，不代表达到目标频率、CPU/P99 门限或所有线上负载条件。
- 功能验收首次使用旧 -U 采集器，170 项虚拟网通过但抓包缺失复合黄金向量；日志确认采集
  丢了 466 帧，失败目录 build/performance-functional-installed-evidence 保留，不称完整
  验收通过。功能 runner 同步改采集缓冲并增加就绪等待、退出状态和唯一内核丢包计数门禁。
  初版就绪判断抢读尚未创建的日志，失败清理 SIGINT 早于处理器安装后进入等待；两份部分
  证据 build/performance-source-evidence、build/performance-final-installed-evidence 保留，
  仅结束自己启动的 tcpdump。修正文件存在判断、TERM+五秒有界等待/KILL 保底。新权限
  夹具复用原样 helper，实测提前退出和忽略信号的清理分支，禁止覆盖既有 report/profile。
- 最终源码 build/performance-final-source-evidence 与不挂产品源码安装包
  build/performance-verified-installed-evidence 各通过 120+50=170 虚拟网、10 审计、10 权限、
  268 后端；逐份 JUnit 核对零失败/错误/跳过。各有 932 个黄金向量和 16 组分片；源码
  17284 帧/3261 条解码消息，安装包 16730 帧/3289 条；两份日志与审计均确认内核采集丢包 0。
  另有 build/performance-runner-regression-evidence 的最终 12 权限/清理+10 统计共 22 项
  回归，以及 build/performance-final-runner-evidence 的最终入口同样 22 项+4 负载阶段通过。
  使用临时哨兵报告实测旧证据目录被拒绝、原内容不变，不拿旧失败目录反复重写。
- 产品镜像 someip-agent-vsomeip:performance-test 重新编译，CTest 两项通过；镜像 SHA-256
  a4ee828f0d7a998954c46576ff1f5d8ef913e717e06fad56e685df35f0584813。源码与镜像二进制 SHA
  一致为 aff31f69de53d7409887e7ef71fe470739f285fc69afbcbaf6a28b17d234606d；Python 产品未改，
  wheel SHA 保持 ce8bc6d7fa746aa43a3ff57c0c0a25d7eae3a2d01fb36930b37205f3b9c8a59a。
  后续测试脚本通过只读挂载验收；Ruff、shell/YAML 语法、版本一致性、diff 空白检查通过。
  CI 接入完整 16 阶段和独立证据 artifact，不对共享 runner 凭空指定性能阈值。
- a8e8950 的远程 run 36813869182 已终态：Linux 原生/安装包、前后端通过，Windows 中文
  pwsh 语法通过；整体 failure 仍在 build-native.ps1 第 27 行 CaptureTriplet/OverlayTriplets/
  Npcap Packet_ROOT 未配置门禁，安装器与上传跳过。未绕过 SDK/null 捕获后端门禁。
  Windows 实机、长稳/多服务/故障完整性能矩阵、独立线上应用身份、完整 IP/ARXML/OEM 语义
  以及正式签名最新版发布源仍需继续；完整目标保持 active。本轮代码与记录提交推送，新
  提交的远程 CI 独立核对，不能把本机短负载结果说成远程或整体完成。
