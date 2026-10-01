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

## 2026-10-01 IPv4 选项边界、固定库补丁与独立证据

- 继续 c909f96 后尚未提交的选项迁移，未修改 Python 产品控制接口。保留成熟 libtins 解析、
  TCP/IPv4 重组；固定到 4.6 提交 2d2f7012d9f3a16d684a55ba39f1215b6aef5429，而非自写解析器。
  上游固定源码仍有 EOL 提前结束/零补齐及完整 type 分类缺口，局部补丁修复解析、长度字节
  IHL 边界和序列化尺寸三个位置。未静默改写原始帧，未将路由/时间戳承载解析当作路由执行。
- Linux/Windows 统一固定提交与补丁后完整 src/ip.cpp 哈希；CMake 要求 libtins 4.6 EXACT
  配置包，防止旧头文件/新库混用。Windows 缓存有额外修改时拒绝，不 reset；补丁 LF 固定，
  LICENSE/补丁随构建制品收集。固定输入、来源及哈希见 docs/native-dependencies.md。
- CTest 两项通过，选项子矩阵覆盖 4–40 字节选项区 220 个 NOP 前缀/EOL 补零位置、未知
  TLV 长度/复制位分类、带数据 TLV 往返和 UDP/TCP 带选项首片乱序重组，畸形长度不得借用
  Payload。此前两段补丁的 hunk 行数错误已由 git apply 门禁拒绝并修正，未忽略构建失败。
- 新增 40 项真实 veth 用例：UDP/TCP × 普通包/乱序分片 × EOL、NOP/EOL、复制位 TLV、
  最大补齐区、路由记录、时间戳、非零补齐、缺长度、过短长度和越界长度。
  发送器仅在临时 netns 内通过 AF_PACKET 注入，避免 IP 栈提前拒绝或改写畸形向量；不属于
  产品 Python 发包回退。合法向量唯一交付，非法首片产生完整异常堆栈，停止后释放残片缓存。
- 独立 PCAP oracle 手工列出选项黄金字节，不引用发送夹具；核对头/传输校验和、IHL、
  MF/偏移与顺序、端口及完整 SOME/IP 字节。产品原生离线导入逐例验证消息来源、完成帧
  软件时间、重组/残片统计和 malformed 拒绝，非法向量不能算成功解码。
- 最终源码 build/ip-options-source-evidence 与不挂产品源码的安装包
  build/ip-options-installed-verified-evidence 各通过 120+90=210 虚拟网、17 审计、12 权限/清理、
  268 后端；逐份 JUnit 核对零失败/错误/跳过。线上及原生离线各核对 932 个原黄金向量、
  16 组分片、40 组选项（24 个合法唯一消息，16 个非法拒绝向量；非法分片 EOF 残片计数 8）。
  源码 17296 帧/3312 条消息，安装包 16766 帧/3251 条；两份内核采集丢包均为 0。
  安装包来源确认为 site-packages，Python 不解码底层作为回退；既有后端三项警告保留。
- 新库产品独立运行 build/ip-options-performance-evidence 的完整 16 阶段，均 verified：
  1600 RPC 成功、3200 条请求/响应及 13201 条事件逐序号核对，全部采集丢包为 0；另有
  12 权限/清理+10 统计口径回归通过。1 ms 原生 UDP/TCP 线上间隔 P99 为 1.500/1.542 ms，
  原生 10 ms TCP 约 99.5 条/秒，不把已产生消息完整交付等同于目标频率或硬实时。
  运行前功能验收容器均已退出；完整结果与计数边界更新到 docs/performance.md。
- 首次完整安装包运行 120+90=210 项功能通过，但普通 UDP 与时间戳向量碰巧共用 IP ID，
  原始审计误借其他端口的包而失败。保留 build/ip-options-installed-evidence；修复 oracle
  普通包/首片的专用端口及实际分片标志边界，尾片仍严格按方向/协议/ID/分片选择。
  未放宽序列、帧数或黄金字节断言。build/ip-options-audit-repair-evidence 只读复核原始失败
  抓包：16891 帧/3461 条消息、932 个原黄金向量、16 组原分片和 40 组选项通过；17 项审计
  回归通过，含同 ID 异端口碰撞、篡改选项/校验和/Payload、缺失、重复和截断的严格拒绝。
- 产品镜像 someip-agent-vsomeip:ip-options-test SHA-256 为
  b107a8cc9afe3d6c97984d4e4187661bb825b92f0b3862f3a9f3b16c02a34484。
  镜像和源码编译二进制一致：d89a1abd3e8b2b800c6196196edbae059d4f3b9abd0a39963ce2d17fc838309e；
  ldd 确认实际加载 /usr/local/lib/libtins.so.4.6。Python 产品未改，wheel SHA 仍为
  ce8bc6d7fa746aa43a3ff57c0c0a25d7eae3a2d01fb36930b37205f3b9c8a59a；后续测试脚本只读挂载。
- c909f96 远程 CI run 36817354455 已终态：Linux 原生/安装包/短负载、前后端通过，Windows
  仍在 build-native.ps1 第 27 行因 CaptureTriplet/OverlayTriplets/Packet_ROOT 未配置拒绝。
  该结果属于旧提交，不能替代本轮新库回归。Windows 实际编译、驱动、安装升级、生产签名
  最新版发布源、IPv6 分片、选项专用语义/跨片复制位一致性、完整 ARXML/OEM 与长稳仍未完成。
  Ruff、shell/YAML 语法、版本一致性和 diff 空白检查通过；本机没有 pwsh，不能宣称本轮
  Windows 脚本已实际执行或通过语法解析。代码、测试与记录提交推送，新远程 CI 单独观察。

## 2026-10-01 同进程独立 application 与 SAT 身份隔离

- 上一分析轮仅只读核对，没有代码进展；本轮继续完整目标，处理一个二进制内多个逻辑
  client 原先共用线上 Client ID 的边界。核对固定 vsomeip 3.5.10 的公共 application/runtime
  API 和 SAT 原始 Operator/base_partner 后，使用具名 application，不自写身份/订阅协议栈。
  新 ApplicationStack 模块负责 init/start/stop/join；默认路由宿主最后退出。
- 成员字典可选择已声明 application；ARXML/from_arxml 与服务 API 成对接收名称/ID，生成
  所有声明并保持 SAT 的标准成员键、控制帧和方法/事件调用形式。旧字典/tuple 默认共享原
  application，编号 client 不自动等同于独立线上身份。页面可显式选择独立成员身份，生成
  宿主 ID +1/+2，状态展示原生返回的实际名称/ID；纯配置函数验证边界、保留 ID 和默认兼容。
- 消息、可用状态、订阅引用计数、待响应请求和停止操作按 application 隔离；响应消费请求槽
  前检查所属 application、服务、实例、方法、版本及传输。停止成员取消并删除其请求槽，
  保留具名上下文到进程退出，以免成员重启重置会话。16 个上下文上限包括路由宿主，不能
  声称 16 位 Session ID 永不回绕。重复 server、运行中切换身份/部署和未声明选择明确拒绝。
  Python 服务 API 将子 application 纳入跨会话名称/ID 冲突和 0x1101 保留检查；原生状态和
  监控保留 application 身份，不把标准通知 Client ID 0 冒充 application ID。
- 新增 10 项 UDP/TCP veth 用例，使用具名 provider 和两个 client，覆盖并发、取消订阅、
  停止/重启、拒绝非法身份及迟到响应。独立 oracle 不导入发送 fixture，专用端口手工列出
  黄金向量，核对 0x7741/0x7742 两个线上 Client ID 和 114 对请求响应的会话、方向与字节；
  UDP/TCP 均与产品原生 PCAP 导入交叉核对。真实副本篡改身份/会话/Payload、删包或重复
  必须失败，新增 5 项严格审计，未放宽原 932 黄金向量、16 分片和 40 选项矩阵。
- 首轮 focused-evidence 在客户端夹具没有 services 时失败，retry-evidence 又因编号成员
  未显式指定基础 service/name 不能按 SAT 标准键找到成员；均保留异常堆栈与失败抓包。
  修正夹具而不改生产命名规则后，applications-focused-verified-evidence 的 10 项通过，
  1880 帧、114 对方法请求响应及 4 条通知独立/原生一致，内核采集丢包 0。服务 API 测试
  原误用 DELETE 停止路由，改为现有 POST /stop，关闭后同身份的新进程可重新初始化。
- applications-source-evidence 通过 130+90=220 虚拟网、22 审计、12 权限/清理、286 后端，
  逐份 JUnit 零失败/错误/跳过；19285 帧/3807 条消息，原矩阵与新增 114 对请求响应均核对
  完整，采集内核丢包 0。监控身份字段补齐后单独重跑后端；最初命令漏启容器 IPv6 导致
  ::1 bind 两项失败，保留 backend-regression-final.xml；正确参数的
  backend-regression-final-ipv6.xml 286 项通过。未修改宿主网卡、未跳过 IPv6 测试。
  vsomeip TCP 停止时 accept 回调的 Operation canceled 日志原样保留，不称全部日志无 error；
  新成员夹具确认自有原生进程正常退出码 0。
- 最终镜像的不挂产品源码安装包 applications-installed-evidence 同样通过 220 虚拟网、
  22 审计、12 权限/清理及 286 后端，JUnit 零失败/错误/跳过；18751 帧/3870 条消息，
  原矩阵与新增两身份的 114 对方法请求响应、4 条通知核对完整，采集内核丢包 0。
  全部产品模块来源经 site-packages 校验，测试与证据之外没有挂载源码，未设置 PYTHONPATH。
- 最终产品镜像 someip-agent-vsomeip:applications-test SHA-256 为
  0a7bd959047aaa66f83c5e2a9e7558435d26864c3e881163bc97d9de9cf51e0a；源码和镜像的二进制
  一致为 6ac843c9ef6871576f2d40aab43df02d7792639425af3c608dc2dc1e2c3dbd92。
  最终 wheel 构建 SHA 为 b4a36ba05d52260ecb5699eed81059be93bea0327b6657238ddacd60f47b538e，
  不复用旧 Python 产品哈希。两项 CTest 通过，固定 vsomeip/libtins 来源及补丁保持不变。
- applications-performance-evidence 的最终安装包独立完成 16 阶段，1600 RPC 成功、3200
  请求/响应及 13193 条事件逐序号核对完整，16 份采集内核丢包均为 0，另有 12 权限/清理和
  10 统计口径回归通过。该负载沿用默认 application，不冒充双身份长稳；本次 10 ms 原生
  UDP 仅 291 条/约 96.9 条每秒，RPC P99 14.21–51.98 ms，低频抖动及完整 Python/IPC
  开销均保留，不因短测 verified 就宣称周期、性能或硬实时达标。详情见 docs/performance.md。
- 本轮修改文件 Ruff 格式及检查、前端类型检查/构建、配置适配器、版本一致性及 diff 空白
  通过。全目录格式检查发现四份未修改旧测试的既有格式差异，保留不做无关机械改写。
- 34cba18 的远程 run 36821436040 已终态：Linux 原生/安装包/短负载、前后端通过，Windows
  在 build-native.ps1 第 28 行因 CaptureTriplet/OverlayTriplets/Packet_ROOT 未配置拒绝。
  未绕过授权 SDK/null 捕获门禁。Windows 实际编译、驱动、安装升级、正式签名最新版源、
  完整 SAT 厂商心跳、更多应用数量/多实例/故障长稳、IPv6 分片和完整 ARXML/OEM 仍需继续；
  整体目标保持 active，不能把本轮限定矩阵当成全部功能和发布完成。

## 2026-10-01 独立身份能力门禁与真实进程故障恢复

- 当前 GitHub 登录已核对有效；继续此前完整重构目标。上一提交 6a6f9a8 已在远程 main，
  本轮开始时工作区干净，没有重复推送。参考 SAT 原始监督逻辑，保留 0/1/-9 不自动重启的
  历史语义；只在自有 `--network none` 容器创建虚拟网，未操作宿主或车辆网卡。
- 修复新 Python/旧二进制错配风险：显式身份启动前检查能力和声明，启动后、业务建链前
  核对原生实际名称/ID。默认 SAT 字典不增加能力要求，增量重启与进程恢复不缓存握手结果。
  独立 identity 模块与原生 ping 只负责控制契约，不自写 SOME/IP/SD/Client ID 分配。
- 新增 29 项真实控制 socket 正反向回归，覆盖能力缺失/错误类型、协议版本、声明重复、
  未声明选择、错误 ID、实际身份缺失/不一致、增量保留选择和更换运行时后重新检查。
  拒绝必须保留完整异常堆栈；不能以期望身份填充实际观测。attach 错误不 reset/kill 外部进程，
  启动后核对不冒充整批原生成员配置的事务回滚。
- 用 ip-options-test 中真实旧二进制（SHA d89a1abd3e8b2b800c6196196edbae059d4f3b9abd0a39963ce2d17fc838309e）
  验证新安装包：旧版本会接受新增成员字段且 ping 同为 vsomeip 3.5.10；新适配器在启动成员
  前明确拒绝，既有成员状态未变、attach 外部进程未终止，默认字典仍可启动，自有进程退出码 0。
  证据位于 build/identity-legacy-evidence/check；没有用 mock 冒充旧版本二进制。
- 新增 8 项 UDP/TCP × 拥有 server/client × SIGUSR1/SIGSTOP 的真实故障恢复，focused 的
  8 项通过。暂停先确认内核 State T，随后只结束无响应自有进程。恢复后两具名 Client ID、
  差异订阅、回调及最新周期值保持；已停止第三成员不复活，未完成业务请求不重放。
- 独立黄金审计使用专用服务/端口、手工黄金字节，不引用发送夹具；核对故障前后 32 对
  RPC 和 16 个故意未应答请求。Client ID 为 0x7841/0x7842，方向、载荷、会话及时间阶段
  与原生离线导入一致。TCP 按 SYN 分代，不将跨进程 Session ID 重置误判为永久唯一约束。
- 源码 build/identity-source-evidence 与不挂产品源码安装包 build/identity-installed-evidence
  各通过 130+98=228 虚拟网、315 后端、22 既有审计、7 新审计及 12 权限/清理；逐份最终
  JUnit 零失败/错误/跳过，既有后端三项警告保留。源码 20961 帧/4289 条消息，安装包
  20553 帧/4137 条消息。原 932 黄金向量、16 分片、40 选项、独立正常身份 114 对 RPC
  和新增故障身份 32 对均完整核对，两份采集内核丢包为 0。新 7 项反向审计随后单独执行；
  完成后纳入正式入口，不把后补测试冒充首次完整入口已有步骤。
- 首次新反向审计的 handshake 用例失败：删的是未承载业务连接的握手，正常业务流仍完整。
  保留源码 identity-audit-junit.xml（6 通过/1 失败）；仅修夹具，删除实际业务流的全部 SYN。
  最终 identity-audit-verified-junit.xml 与安装包 identity-audit-junit.xml 各 7 项通过，
  覆盖篡改身份/会话/Payload、缺失、重复、错误故障阶段和业务握手缺失，未放宽审计器。
- 新产品镜像 someip-agent-vsomeip:identity-test 为
  f48d99c4b88501ab7a0bdb242a26584c895aa635c675aedeab0611274f666eb2；源码与镜像二进制均为
  b9d8a1edc5295e8df66469fd31c888cc49629893671a63a9651fb9f588faa141，wheel 构建 SHA 为
  2fcf9cc18f4bc2207b44882db99113671cbe30ad4fe0ace4fe3d89078a557670。vsomeip/libtins 固定
  输入与补丁未变，两项 CTest 通过；安装包模块来源为 site-packages，无产品源码/PYTHONPATH。
- Ruff 检查及本轮修改文件格式、版本一致性、前端类型/构建、服务适配器、shell 语法与 diff
  空白检查通过。首次适配器命令误用 frontend/scripts 路径，实际根目录 scripts 命令通过；
  非产品故障未掩盖。前端已有大 chunk 警告保留，不做无关代码改写。
- 新安装包 build/identity-performance-evidence 独立完成 16 阶段：1600 RPC 成功、3200
  请求/响应与 13198 条事件逐序号核对，所有采集内核丢包为 0，另有 12 权限/清理和
  10 统计回归通过。默认 application 基线不冒充双身份长稳；RPC P99 14.043–63.037 ms，
  10 ms 原生 UDP/TCP 299/298 条，严格周期仍未达标。完整数值与口径见 docs/performance.md。
- 上一提交 6a6f9a8 的远程 run 36825941351 已终态：Linux 原生/安装包/短负载、前后端成功，
  Windows 在 build-native.ps1 第 28 行因 CaptureTriplet/OverlayTriplets/Packet_ROOT 未配置
  拒绝，异常堆栈已核对。该结果不替代本轮新提交 CI，未绕过授权 SDK/null 捕获门禁。
- 当前限定短故障矩阵不证明任意应用数量、多实例/长稳、极限在途请求和跨代迟到响应。
  Windows 授权 SDK/捕获 triplet、实际驱动/安装升级、正式签名最新版源、完整 SAT 厂商心跳、
  IPv6 分片与完整 ARXML/OEM 仍需继续；整体目标保持 active。

## 2026-10-01：IPC 实际选项对照与源服务名称约束（本轮）

- 分类：实质进展。按更新目标以 ARXML/指定 comm 配置为业务源，SAT 仅参考调用形式；
  用户明确排除 Bootes，不再将厂商安全心跳列为阻塞项。SAT 安全测试标题证明 heartbeat
  1200/2400 单位为毫秒，保留参数不等于发送该厂商报文；进程活性与故障恢复继续保留。
- 用已有 socket/Boost.Asio 标准 TCP_NODELAY 设置本地控制/成员连接，不修改 vsomeip
  线上 TCP，不引入新库、不更改 SAT 长度帧/JSON 流或旧二进制启动参数。缺省/1 开启、0
  关闭，非法值启动前拒绝；服务模式 ping 读取实际 accepted control/member 选项。
- 23 项真实 TCP/原生选项测试通过，覆盖缺省与开关、对端未改变、完整异常堆栈、控制/
  成员实际值与非法配置监听前拒绝。首次 macOS 单项测试失败：getsockopt 返回非零位 4，
  原断言误要求整数 1；产品已经按 bool 判断，仅修正测试口径，未掩盖首次失败。
- 使用同一 ipc-options-test 镜像（4170b023166f58a1515b8d9f7cfee2f0d014cbc6387f3b049f0dc4f117ddee92）
  先关后开，各完成 16 阶段、1600 RPC/3200 请求响应及 13201/13188 已产生事件完整核对。
  实际选项每阶段两端读取，所有采集内核丢包为 0、原生成员错误回调为空，各另有 12 权限/
  清理和 10 统计回归通过。测量时没有并行构建/本项目功能验收，完整数据在 docs/performance.md。
- 32 字节四并发 UDP/TCP 的 P99 从 47.492/41.895 降至 16.210/15.900 ms，但 TCP 1024
  字节四并发本次从 17.513 升至 39.847 ms；未做多轮随机交错，不能普遍归因或宣称全链路
  零 Python 开销。10 ms 开启原生 UDP/TCP 只产生并交付 295/299 条，严格周期仍未证明。
- 修正完整路径选择将路径当业务服务名的问题：生成成员 service 与缺省 name 采用 ARXML
  SHORT-NAME，source 记录名称/完整路径/部署；定义和 ID 不变。新增 5 项源名称/原生启动
  契约，并将现有 4 个 UDP/TCP × 大小端 veth 用例改为完整路径选择，方法/字段/事件均通过。
- 本地实际 V6.12.0 ARXML 131 个已部署服务与 H47A/V_6_12_0 两份 JSON 全部同 ID/同名；
  矩阵 137 个定义、3978 通信行不自动激活或挪用车网端点。其他版本存在缺失/改名，尚待用户
  确认激活版本。新增只读名称审计工具复用现有 ArxmlParser/JSON，8 项正反向及拒绝覆盖验证
  通过，已纳 CI。build/comm-name-evidence 绑定三份源 SHA、明确 runtime_verified=false。
- 真实 ARXML 存在 934 个参数/字段投影布局错误记录（重复引用重复计数），6290 行解析日志
  保留完整异常堆栈。该名称成功不代表 131 个车型服务全部能启动，未替它猜复合部署。
  补充只读 XML 根因核对：实际有 1 个描述/默认 transformation 与 2271 个 I-SIGNAL props，
  描述声明 ALIGNMENT=64/大端，样本 session handling active；AP 映射/props 均为 0。
  是当前 AP-only resolver 未覆盖该引用链，不把保守错误提示解释为源文件完全没有部署。
- 最终功能镜像 ipc-source-names-test 为
  48eadd9f292e977d31c3a40539f476e8d967aa678404480f66e32aefb18ed960；源码与两个镜像二进制
  均为 6d85f1744c73a58f486c62aa6684a54f3f85f3fde3f63b7759cbafe307c1a20b，两项 CTest 通过。
  功能 wheel SHA 为 319e14f246ceeb8dbc0477611dcee7671bfe9e3263a742199497733322450140，
  与性能对照 wheel 不同，不混用性能对象。固定 vsomeip/libtins/补丁输入均未变。
- 源码 build/ipc-source-evidence 完整通过 130+98=228 虚拟网、343 后端、22+7 抓包审计
  和 12 权限/清理；完成后补跑 8 名称审计，再纳入正式入口，不冒充首次运行已有该步骤。
  21039 帧/4367 消息，932 黄金向量、16 分片、40 选项、114 正常身份与 32 故障 RPC 对
  核对完整，采集内核丢包 0。安装包 build/ipc-installed-evidence 同样通过 228 虚拟网、
  343 后端、22+7+8=37 审计和 12 权限/清理；20495 帧/4378 消息，全部黄金向量与两类身份
  RPC 完整核对，采集内核丢包 0。逐份最终 JUnit 零失败/错误/跳过，IPC 等产品模块实际来自
  site-packages，不挂产品源码/无 PYTHONPATH；自有容器已退出。
- 新安装 Python 对真实旧二进制 d89a1abd3e8b2b800c6196196edbae059d4f3b9abd0a39963ce2d17fc838309e
  再次验收：默认字典可用，显式身份仍在成员变更前拒绝，外部 attach 进程与原成员未变，
  自有清理退出 0，证据在 build/ipc-legacy-evidence/check。
- Ruff/修改文件格式、版本、前端类型/构建、适配器、shell 与 CI YAML/diff 检查通过；前端
  既有大 chunk 警告和后端三项警告保留。本轮统计提取曾误读 interval 字段导致 KeyError，
  按实际嵌套结构重新核对原报告，未修改报告或统计器；初次本地历史模型无摘要，改用源 ARXML
  重新解析，不将旧投影当当前证据。新名称脚本两项 Ruff 类型异常规则已按 TypeError 修正。
- 前一提交 f972e86 的远程 run 36828881714 已终态：Linux 原生/安装包/负载和前后端成功，
  Windows 在 build-native.ps1 第 28 行因授权 SDK/triplet/Packet_ROOT 未配置失败，堆栈已核对。
  它不是本轮提交的 CI。正式签名最新版升级源/Windows 实机、真实 OEM 布局、IPv6 分片及
  规模长稳仍未完成；整体目标保持 active，不再要求实现 Bootes。

## 2026-10-01：Linux 独立发行包与真实签名升级（本轮）

- 分类：实质进展。上轮只读分析已核对原生数据面和 Python IPC 成本，本轮推进 Linux
  在线升级的实际缺口；Windows 打包/调试按更新目标暂缓，CI 改为显式手动选项。
  Bootes 不要求实现，未修改 SAT 调用形式、源服务名称或 SOME/IP/SD 协议栈。
- 先查现有依赖，复用已有 PyInstaller 可选依赖、zipfile、httpx 和 Ed25519；Linux 主程序
  为 onedir，独立升级器为 onefile。完整 ZIP 包含前端、原生二进制、依赖/插件、执行权限、
  原生许可/补丁和构建摘要；不要求目标机安装 Python。打包时将库别名转为实际文件，保持
  解压拒绝符号链接的既有门禁。当前仅验证 Debian 12/aarch64，不承诺全部 Linux/架构。
- 修正 POSIX 缺省升级器名、外置暂存和执行权限门禁；新旧应用独立启动时重置 PyInstaller
  运行环境。主进程退出前验证主程序、升级器及随包原生程序，缺少必需程序先拒绝；新程序
  启动/版本健康失败恢复旧目录并确认旧版健康，失败现场保留。状态记录实际重启/回滚 PID，
  测试只清理本次目录内对应进程，不把回滚失败报告为成功。
- 首次构建因缺 libpython3.11 失败，完整 CalledProcessError 堆栈保留；仅在构建镜像补齐
  共享库，宿主未安装软件。首次 19 项测试中 2 项失败：macOS Docker 共享目录权限位 0644
  但 os.access(X_OK) 为真；产品改为同时校验权限位和访问结果，不放宽原断言。
  build/linux-update-first-evidence 的失败 JUnit/应用/升级日志保留。
- build/linux-update-current-evidence 对当前夹具完整通过 19 项：临时受信 HTTPS、实际
  Ed25519/SHA、真实冻结主程序/升级器、新版健康及旧版备份；故意删除新包 Python 运行库
  后真实启动失败、旧程序恢复健康。发行包测试不指定安装根目录/升级器/静态目录或原生
  路径，确认缺省发现；另有 build/linux-default-discovery-evidence 两项独立正/反向通过。
  新版由临时源码副本四份一致版本真实编译，不改仓库版本，不发布测试版或测试密钥。
- 夹具改为由已验证仓库 SemVer 生成下一 patch，避免将未来 CI 固定为 0.1.0；新增 2 项
  正式/先行版本变化测试通过。原签名清单脚本、版本源、SAT 字典/长度帧契约保持一致。
- build/linux-clean-verified-evidence 在仅含 Debian slim + 发行目录的镜像验证：无已安装
  Python/SDK，网页、版本健康、随包原生发生器启停可用，二进制 SHA 与 build-info 一致；
  容器 network=none，自有进程/容器已退出。Docker Hub 构建和显式 pull 两次 TLS 超时，
  改用 ECR Debian 镜像，基础摘要为
  3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251。
- 打包镜像 someip-agent-linux:update-final 为
  8de860ccaa43ffc2945f999634286740d32f489702b4eb9a4fbd559aef4df0dd；干净运行镜像为
  a0d3c924f21d62372585eb80fba9692130de4dd8691933dca4f8180d1817463a。
  基线 ZIP SHA 为 8c5044e07e98e55e61d30540213f774b713577452f7f754ecb72b3bd182ef117，
  原生二进制仍为 6d85f1744c73a58f486c62aa6684a54f3f85f3fde3f63b7759cbafe307c1a20b；
  vsomeip 3.5.10/libtins 4.6 固定输入与局部补丁未改。不据此宣称性能改善或线速。
- build/linux-update-installed-evidence 完整通过 130+98=228 虚拟网、348 当时后端回归、
  22+7+8=37 审计和 12 权限/清理；20544 帧/4389 消息，932 黄金向量、16 分片、40 组选项、
  114 正常身份 RPC 对及 32 故障恢复对一致，采集内核丢包 0。模块来自 site-packages，
  无产品源码挂载/PYTHONPATH；升级 service/worker 另单独核对并加入正式模块来源入口。
- 新增版本夹具测试后补跑 350 项时，独立启动命令漏了既有 IPv6 sysctl，2 项 ::1 绑定失败，
  build/linux-backend-current-evidence 保留失败报告；诊断 all/default/lo disable_ipv6 均为 1。
  按正式入口开启容器内 IPv6 后，build/linux-backend-verified-evidence 的 350 项全通过，
  不跳过或改 IPv6 测试。上述所有最终 JUnit 均零失败/错误/跳过，既有三项后端警告保留。
- Ruff、版本一致性、前端类型/构建、服务适配器、shell 语法、CI YAML 与 diff 检查通过；
  前端既有大 chunk 警告未做无关改写。测试夹具之外新增文件仅为打包/验收与文档。
- 上一提交 7606fd6 的 run 36835256810 已终态：Linux 原生/安装包/短负载及前后端成功，
  Windows 失败；历史结果保留，不冒充本轮 CI。新 CI 复用同一原生镜像执行 Linux 升级与
  干净运行，不重复编译第二套底座；Windows 默认不运行。
- 正式 HTTPS 最新版源和信任公钥仍未配置，已向用户询问 GitHub Releases 或企业源；不
  公开发行临时测试包、不使用测试信任根。真实 OEM/Classic I-SIGNAL 序列化链仍需接通，
  AP-only resolver 的代码证据已重新核对。完整 ARXML/OEM、IPv6 分片、规模长稳和 Linux
  实际浏览器点击/生产发布验收继续推进；整体目标保持 active，Windows/Bootes 不再列为门禁。

## 2026-10-01：Classic 完整引用绑定与原生验收镜像补齐（本轮）

- 分类：实质进展。按继续完整目标的授权，将已验收的 Linux 发行包提交 e9c956c 推送到
  origin/main，核对远端完整 SHA 为 e9c956ca4bf789019710009bc198596cdd558695。随后补上
  真实 Classic ARXML 的引用图，不把前一轮只读分析当作代码修改授权。
- 复用已安装 lxml，新增 ClassicReferenceResolver，经 PDU trigger、I-PDU mapping、I-SIGNAL、
  SYSTEM-SIGNAL、sender/receiver 或 client/server 系统映射精确选择数据原型/操作，并保留
  transformation/transformer 完整链。操作不任取接口第一项，完整引用存在时不按 PDU 名称
  前缀匹配；多 ECU 对同一业务目标的引用去重，歧义目标和同 Header ID 不同操作明确拒绝。
- 方法/事件保存 typed classic_bindings 和完整路径，模型 JSON 往返不丢来源。缺失引用的旧
  名称投影仅可浏览，部署错误阻止原生初始化；完整链也不解除尚未实现的 Classic payload
  门禁。20 项 Classic 与 9 项源审计专项通过，覆盖错误引用/类型、方向、歧义隔离、缓存
  独立性、RPC 双向绑定、多个操作和完整链不能冒充 wire-ready；未新增生产依赖/自写协议栈。
- 首次真实文件复核发现重复完整路径 /Communication/PDUs/Notify_f_Feature_FL_SignalIPDU，
  解析器全局拒绝导入，终端保留 WireTypeError -> ArxmlParseError 堆栈。修正为按被引用成员
  隔离，不选第一个、不改 OEM 原文件，重复路径不再阻断其他可浏览服务。
- build/classic-source-final-evidence 使用真实安装包、只读输入与 network=none：131 个源服务
  与 H47A/V_6_12_0 的 137 定义/3978 通信行全部同名/同 ID，1067 成员保存 2228 绑定，方向
  为 input=491/output=491/data=1246。报告包含绑定、逐服务部署错误及三份源 SHA；34 个被
  引用歧义路径形成 51 条部署错误，另有 126 条未实现布局错误，共 177 条。934 条重复引用
  计数的信号布局错误仍存在；runtime_verified=false。没有激活车型或搬用车辆网络端点。
- 首个完整安装包验收 build/classic-installed-evidence 的 228 虚拟网、22+7+9=38 审计与
  12 权限/清理通过，但完整后端为 368 通过/2 失败：缺少 scripts/check_version.py 的升级
  夹具辅助资产，FileNotFoundError 堆栈保存在 JUnit。修改 native/Dockerfile 增加现有版本
  检查和升级夹具两文件，不修改测试断言或挂载产品源码；重新构建并启动完整独立复测。
- 上一提交 e9c956c 的远端 run 36842738059 已终态：前后端成功，原生安装包阶段同样因
  上述两项缺失失败（348 通过/2 失败），Windows 按配置跳过。后续负载/Linux 发行阶段未
  执行，缺失 artifacts 的次生失败保留；不能把本地发行包验收说成远端 CI 已绿。
- 核对官方 AUTOSAR CP 4.4.0 Transformer/System Template：固定长度成员不能统一自动加
  padding；变长非末尾元素的对齐规则与整条消息起点另行处理。MESSAGE-TYPE 的模型枚举
  不直接等于线上类型字节，session/default length/TLV 也未验证。直接 PDF 读取超时、Jina
  TLS 失败后使用官方搜索索引的精确规范条目；依据链接和边界保存在 docs/classic-arxml.md。
  不因此放开现有发送门禁，后续还需真实 Classic 编解码及黄金字节互操作。
- 补齐资产后的最终镜像 someip-agent-vsomeip:classic-verified 为
  1c6e4597c9a8120b12eb84b1663ce53245ec648242f40558095bb9f5e5339f15，固定 SDK/补丁核验与
  两项 CTest 均通过。wheel SHA 为 9c6487f176090d5d7caad02e7881cbacba8d46f36cc1660f258aac1fed28e42c；
  与先前来源审计镜像 9930fad77438b046df0fcf6c43082b584e792c005fd903c8ef28df5823d19fa9 的
  wheel 相同，新增的两文件仅为测试辅助资产。原生二进制保持
  6d85f1744c73a58f486c62aa6684a54f3f85f3fde3f63b7759cbafe307c1a20b，未宣称性能改善。
- build/classic-verified-evidence 完整复测通过 130+98=228 虚拟网、370 后端、22+7+9=38
  审计与 12 权限/清理；7 份最终 JUnit 均零失败/错误/跳过。模块实际来自 site-packages，
  无产品源码挂载/PYTHONPATH；classic/parser 的安装文件 SHA 与当前仓库分别完全一致。
  20602 帧/4412 消息，932 黄金向量、16 分片、40 组选项、114 正常双身份 RPC 对与 32
  故障恢复对经独立黄金审计和原生导入核对完整，8 故障 case 与 16 刻意不回答请求保留，
  采集内核丢包为 0。原 2 项失败报告仍在 classic-installed-evidence，不覆盖成通过。
- 20 项 Classic/9 项源审计专项、Ruff、四版一致性、前端类型/适配器、shell 语法及 diff
  检查通过；既有后端三项 warning 保留。本轮没有测线速、严格周期或规模长稳，既有性能
  数字不复制为新性能验收。正式信任升级源、真实 Classic/OEM payload、IPv6 分片和规模
  长稳仍需继续；Windows/Bootes 不要求实现，整体目标保持 active。

## 2026-10-01：显式 Classic payload 布局与原生变长对齐

- 上一轮只读分析核实当前默认仿真为原生数据面；本轮按持续目标恢复实现。工作树起始干净，
  Bootes/Windows 仍排除，未使用子智能体，未改车辆网卡/原 ARXML/comm 配置。
- 复用现有 lxml 与固定 vsomeip，不新增生产依赖。ClassicReferenceResolver 按 I-SIGNAL
  完整链读取 serializer、description 和 props；明确 SOMEIP 1.0.0/SERIALIZER/header64。
  显式结构/数组长度字段、字节序与对齐传播到嵌套 schema；请求/响应布局按绑定方向分别处理。
  缺失宽度、未知/TLV/细粒度覆盖、多个变体、非零起始位置、多 transformer 和冲突仍拒绝。
  完整 header/session/服务部署门禁不解除，不能把 payload 归一化叫做车型完整互操作。
- 官方 4.4.0 Transformer 的 00037/00218/00259/00263 要求绝对消息偏移与变长非末尾补齐，
  固定成员或全消息末尾不得统一补齐。本轮 Codec 同时考虑 16 字节 SOME/IP 头与嵌套前缀；
  CTest 增加空数组、末尾、固定成员和变长元素数组的独立黄金字节/截断拒绝。
  原生头、SD、订阅和通信继续由 vsomeip 处理，没有自写第二套协议状态机。
- 使用 agent-reach 的 gh 路线观察原 run 36846054968，前后端成功；原生构建与安装包/短负载
  阶段已实际继续到 Linux 打包环境步骤，不因观测超时重启。Jina 阅读超时、mcporter 不可用，
  采用官方 AUTOSAR 搜索索引核对规范，没有把全文下载失败称为完整阅读。
- 查阅固定 SDK 的 event::set_session 和 application_impl::get_session：通知有配置开关，
  启用后与应用请求共享计数器，不能直接宣称满足 Classic 每 transformer 的 active session。
  本机参考 autosarfactory 的 MESSAGE-TYPE 采用符号枚举，也未给 V6.12.0 的 0/1/2/3 数字
  提供可验证解释；已询问原生成工具/枚举定义，继续实现其他序列化工作，不按名称相关性猜测。
- 镜像 someip-agent-vsomeip:alignment-test 为
  587dcd51545c50ef196e7e75792efcbcbd7515cc1d35e1a4211684c471f8a854，固定源码/补丁验证和
  两项 CTest 通过。wheel SHA 为 3e72d019af851872a539002bf9d06e29e65cd381b31d8eb08667df30c2d84089；
  二进制 SHA 为 20ef8c071388299b875592135a7b1a60f810e3accf76d913e9bab6b24d4b2a38。
- build/alignment-source-evidence 用已安装 wheel、只读原文件、network=none 重新核对：
  131 个源服务与 H47A/V_6_12_0 同名/同 ID，1067 成员/2228 绑定不变，177 部署错误和934 条
  重复引用计数的布局错误仍保留。缺失复合 LENGTH-FIELD 的完整堆栈在 parse.log，报告仍
  runtime_verified=false；没有激活车型或把车辆端点用于虚拟网。
- 新增 16 项虚拟网对齐用例覆盖 UDP/TCP、双字节序、1/2 字节长度前缀，沿用 SAT 字典初始化
  验证 RPC、事件、字段 Getter/Setter/通知；它们验证 AP 显式 fixture 到共用 Codec 的链路，
  不冒充 Classic 车型运行。手工黄金审计新增 192 组精确向量，含缺失/错误 padding 拒绝。
- 本轮错误命令 check_services_adapter/check_capture_adapter 的 MODULE_NOT_FOUND 堆栈
  已保留，随后按 rg 定位正确的 scripts/check_service_adapters.mjs 并实际通过；不是增加空
  脚本绕过验收。Ruff、版本一致性、前端类型、适配器、shell/diff 检查通过。
- build/alignment-installed-evidence 首轮完整安装包验收通过 146+98=244 虚拟网、389 后端、
  25+7+9=41 审计与12 权限/清理；7 份 JUnit 均零失败/错误/跳过。22612 帧/4635 消息，
  1124 黄金向量、16 分片、40 组选项、114 正常双身份 RPC 对与32 恢复对均经独立审计及
  原生导入核验，8 故障 case/16 刻意不回答请求保留，采集内核丢包0。无产品源码挂载或
  PYTHONPATH，模块来自 site-packages；既有后端3 项 warning 保留，没有重测性能或长稳。
- 首轮后进一步拒绝 Classic 缺失 ALIGNMENT 套用 AP 默认值，增加对应回归；最终镜像
  someip-agent-vsomeip:alignment-final 为
  6ec9a17eae44912c79ff9fd1ab2268c7e30a7153a8f702e50c0c31e847479608，固定输入/CTest 通过；
  wheel 为 a81fd8983ec67bf0499205045972b0b10685220f76cd55434f1f4d1aa1e445c1。
  二进制、parser/transformation 与首轮相同，Classic 模块新 SHA 为
  676b74a014d6553539086c23b877cb1d268d4ffe389a87e4812346eaabe74f23；当前仓库与安装文件一致。
  最终 wheel 的390 项后端回归通过，零失败/错误/跳过，保存在
  build/alignment-final-backend-evidence/backend-regression.xml；最终原文件复核在
  build/alignment-final-source-evidence，131 服务同名/同 ID、934 布局错误与绑定/部署计数
  不变。未挂载产品源码。首轮389 项或虚拟网证据不是对最终新增拒绝检查的重复整网验收。
- 上一提交386428b 的远端 run 36846054968 已确认 completed/success：前端、后端、原生、
  虚拟网、安装包、短负载、Linux 发行包、真实签名升级/回滚及无 Python/SDK 运行全通过；
  Windows 按配置 skipped。本轮修改的远端状态需以新提交/run 为准，旧绿不代表新提交已绿。

## 2026-10-01：CP 4.4.0 可选前缀与 VSA_LINEAR 源字典

- 分类：本轮实质进展。上一轮仅完成只读分析，就持续重构目标没有实现或验收推进；本轮按
  明确 continuation 恢复实施。起始三个未提交 ARXML 文件为此前本任务的改动，已逐项核对，
  没有撤销用户文件；未使用子智能体，Bootes/Windows 排除，未改真实车辆网卡或源配置。
- 沿用固定 vsomeip/lxml，不新增生产依赖。Implementation VSA_LINEAR 从完整类型关系解析
  size indicator、payload 源名/类型及上界，归一成附源字典元数据的变长数组，不当作普通
  struct 写双前缀。CP 4.4.0 的长度字段类型来自 indicator；字典里是元素数、wire 上是字节数。
  仅明确 AUTOSAR_00046.xsd 的可选普通 struct/fixed-array 前缀缺省为零，未知版本不套默认。
- 原生 Codec 保留源字典形状，计数必须是非负整数并等于列表长度；数组字节前缀包含实际元素
  编码，不另写 count。空数组、非末尾绝对对齐、截断、非法计数、上界、错误元数据、未知
  profile 与 Application/Implementation profile 冲突均有拒绝路径。其他 VSA profile/多维
  profile、超大声明上界与未知布局仍未支持，不能用 AP fixture 代替车型 header/session 验收。
- 57 项类型/Classic/复合布局专项通过。初期新单元夹具把 SHORT-NAME 节点纳入类型索引导致
  覆盖实体路径，随后仅修正夹具索引与局部引用变更范围；错误堆栈保留在工具输出。
  原生构建核对固定源码/补丁后，两项 CTest 通过，覆盖双字节序与1/2/4字节 VSA 黄金前缀。
- 使用 agent-reach 的 gh 路线核对原 run 36849453663：已 completed/success，前后端、原生/
  虚拟网/安装包/短负载及 Linux 发行/签名升级回滚/干净运行全部通过，Windows skipped。
  mcporter 不可用、Jina 超时后查官方 AUTOSAR 索引，不把 PDF 下载失败称为全文阅读。
- 镜像 someip-agent-vsomeip:vsa-test 为
  4ea05f4950e4acf8f2352ef2c5ea49052b887012b0bcaabfbce5083688e12297；wheel SHA 为
  bcd654d96a78a1633c95d0047b47939825eb35a0ac3d4b73dda37f579fbc74f4；原生二进制 SHA 为
  c2d00cddd44e8fd90c596c14fe8639a017c27cc00e3492a8b30de5da327d2c44。
  三个解析模块的当前仓库/安装文件 SHA 完全一致，模块来自 site-packages，无产品源码挂载。
- build/vsa-installed-source-evidence 在 network=none、只读三份原文件和非 root UID 下验证：
  131 个服务与137定义/3978通信行同名/同 ID，1067成员/2228绑定及177部署错误不变。
  信号布局错误由此前934降至112（重复引用计数）：62缺失动态数组宽度、23超数组资源门禁、
  26缺少完整布局绑定、1空结构。新增按信号路径/type_ref 的错误清单和分组，完整异常栈在
  parse.log；runtime_verified=false，没有激活车型或复制车辆端点。
- 真实源73个 VSA 类型中63个 uint32 indicator 类型图已解析，10个因上界超过65536拒绝。
  源中实际存在1048576、150000、3000000上界；后续需分开声明上界与实际 wire/IPC 预算，
  不以提升一个常量冒充大数组/大包验收。62缺失宽度包含 Application CATEGORY=STRING，
  其 Implementation 为未标 VSA profile 的 count+ARRAY；需处理正式文本元数据/版本规则，
  不因字段名相似就当作 VSA_LINEAR。
- 新增12组 veth VSA 用例覆盖 UDP/TCP、双字节序、三种 indicator 位宽，沿用 SAT 字典启动
  二进制/socket 初始化，验证 RPC、事件、复合字段读写/通知、空和最大有效数量以及拒绝。
  黄金审计另加192组手工向量，含错误字节长度和 padding 的反向检查。
- build/vsa-installed-evidence 首轮实际256功能用例、1316黄金向量及原生导入通过，但审计
  反向测试4失败/21通过：仍写死旧1124组总数。原失败 JUnit/PCAP 完整保留；修正为
  原1124+新192=1316，另加4项 VSA 审计回归，没有删测试或放宽实际字节要求。
  使用同一产品镜像在新目录 build/vsa-verified-evidence 重跑完整安装包流水线。
- 最终复测通过158+98=256虚拟网、408后端、29+7+9=45审计及12权限/清理；7份 JUnit
  均零失败/错误/跳过，既有3项后端 warning 保留。24448帧/4914原生消息，1316黄金向量、
  16分片、40选项、114正常双身份 RPC 对与32恢复对经独立字节审计及原生导入核对，
  8故障case/16刻意不回答请求保留，采集内核丢包0。源解析与第一次失败证据均未覆盖。
- Ruff lint、变更文件格式检查、版本一致性、前端类型/服务适配器、shell/diff 检查通过。
  全后端 format-check 另发现3个既有未格式化测试文件，未做无关修改。本轮未测性能/长稳；
  原生迁移、Classic/OEM header/session/文本类型、大数组、IPv6分片、规模长稳以及正式信任
  升级源和真实升级按钮验收仍需继续，完整目标保持 active。
