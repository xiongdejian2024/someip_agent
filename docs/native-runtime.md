# 原生 SOME/IP 运行时与验收

## 当前分工

- `native/src`：固定 vsomeip 3.5.10，C++ 处理在线服务发现、方法、事件、字段、序列化与发生器。
- `backend/src/someip_agent/soa`：SAT 风格的 SOAOperator / S2sBaseClass，启动二进制后用 socket 初始化与调用。
- 默认 `SimulationManager` 已改为原生进程控制，不保留 Python 发包回退。
- 实时 UDP/TCP 端口监听已移到 `native/src/network.cpp`，复用固定版本 vsomeip 导出的解码器；
  Python 网络模块只负责进程控制、监控对象化和 SD/信号展示分析，不绑定线上端口或解码 SOME/IP 头。
- `native/src/capture.cpp` 使用 libpcap 读取指定 Ethernet 网卡，`capture_processor.cpp` 复用
  libtins 的双向 TCP 与 IPv4 分片重组器，协议头复用相同 vsomeip 解码器。
  Python 不读取网卡或重组 TCP/IP。
- 离线 PCAP/PCAPNG 由 libpcap 读取，复用相同原生 TCP/IPv4 重组与 vsomeip 解码；
  Python 只负责上传文件、控制 socket、展示聚合及 SD/信号分析，不保留 Python 底层解析回退。
- 完整 ARXML 映射、SAT 辅助接口、IPv6 分片及完整 IPv4 选项仍有未完成项，
  不能宣称整个底层已替换。

vsomeip 上游固定提交：`c4e0db329da9b63f511f3c2456c040582daf9305`。
Dockerfile 检查源码提交，避免相同标签移动后悄悄更换核心。

## 构建与虚拟以太网

```bash
make native-image
make native-test
make native-regression
make native-installed-test
```

`native-regression` 先完成编译再运行后端，禁止在同一输出目录同时重链接二进制和启动测试。
已有固定提交的 SDK 镜像时可显式传 `NATIVE_SDK` 复用；Dockerfile 仍核验其 `/opt/vsomeip`
提交，并重新编译、执行 CTest。默认 `upstream` 从源码阶段构建，不隐式依赖本机 SDK。

`native-test` 只在 `--network none` 的临时 Linux 容器内创建 bridge/veth/netns，不改宿主机网卡。
`--privileged` 仅用于容器内 netns、veth 和 tcpdump；不要把它改成 host 网络运行在车辆网络上。
宿主机本地无可运行原生二进制时，测试会明确跳过原生集成；`native-regression`
设置 `SOMEIP_AGENT_REQUIRE_NATIVE_TESTS=1`，缺少二进制必须失败，不能靠跳过验收。
`native-installed-test` 使用先前构建的镜像，仅挂载测试与证据目录，不设置 PYTHONPATH；
先验证各产品模块均来自 site-packages，再执行相同虚拟网和全部后端回归。
安装包证据独立保存在 `build/installed-evidence/`，不能与源码挂载验收混为一谈。

输出证据在 `build/virtual-evidence/`：JUnit、原生日志、接口信息、PCAP 与黄金报文审计 JSON。
抓包审计核对 UDP/TCP 请求、响应、字段通知、周期浮点事件，以及 Offer/StopOffer/Subscribe/Ack。
它证明指定完整报文及确定 IPv4 分片向量存在，不承担通用 IP/TCP 重组或吞吐/丢包统计。

原生监听的 UDP、TCP 拆/粘包和组播还会在两个虚拟网卡节点之间验证，并独立核对 PCAP 黄金字节。
两个节点同时配置 IPv6，IPv6 UDP/TCP 监听也在虚拟以太网上实测和独立复核。
审计器只为该短 TCP 黄金流重组连续字节，不承担任意 TCP 流、序号回绕或长期会话重用的重组。

独立 CMake 构建需指定 `-DVSOMEIP_SOURCE_DIR=固定版本源码目录`，因为上游只安装接口头，不安装
已导出解码器的实现头。库要求精确 3.5.10；Linux 镜像和 Windows 脚本均从同一固定提交构建。

## 原生端口监听

`soa_partner run --network -p 0` 启动独立控制进程，不初始化服务路由，避免与服务端点抢占。
Python 通过 `network_start` / `network_stop` 和同一长度帧监控协议控制它；最后一个监听停止后退出进程。
UDP 支持 IPv4/IPv6 单播、IPv4 组播、一个数据报内多条 SOME/IP 消息；TCP 使用精确长度读处理拆包/粘包。
TCP 最多 128 个连接，从首字节开始每帧读取最多 10 秒，空闲连接不计该超时；单帧少于 2MiB，
为 payload_hex 和 4MiB IPC 限制预留空间。
非法 UDP 数据报记录原生堆栈后继续；非法 TCP 长度关闭对应连接，其他连接与监听器继续。
Python 的 `wire_verified=true` 表示实际 socket 收到字节，不表示网卡抓包、全网无丢包或时间戳来自网卡硬件。
destination 为本地绑定端点；绑定 0.0.0.0 时不能据此判断数据包的原始目的 IP。
原生进程或监控断开会标记相关监听失败，不回退到 Python socket；二进制缺失 API 返回 503。
监听端口冲突等配置失败返回 422，已有监听不受影响。
IPC 输出同时限制单帧 4MiB、连接队列 1000 帧和 16MiB；超限记录原生堆栈并断开慢消费者。
这不等于已完成吞吐、内存峰值或慢消费者长期压力验收。

## 原生被动网卡捕获

通过 `GET /network/interfaces` 枚举网卡；`POST /network/listeners/start` 使用以下配置：

```json
{
  "mode": "pcap",
  "capture_interface": "eth0",
  "capture_filter": "udp port 30490 or udp port 30500 or tcp port 30500",
  "promiscuous": false
}
```

以上路径位于 `/api/v1` 下。必须明确选择网卡并配置 BPF，不默认打开混杂模式。
此模式不绑定服务端口；真实源/目的端点来自 Ethernet/IP 报文，区别于通配端口绑定地址。
链路目前限 `DLT_EN10MB`，支持 Ethernet/VLAN、IPv4/IPv6、UDP 多报文和双向 TCP 拆/粘包；
TCP 序号、乱序、重传和序号回绕由 libtins 承担，不自写重组算法。
IPv4 分片由 libtins 重组后接入同一 UDP/TCP 路径；IPv6 分片仍明确拒绝并记录异常，
不能称为支持所有 IP/TCP 流量。中途捕获的流标记 `tcp_partial`；
如果首个捕获片段不是 SOME/IP 帧头，不猜测重同步，会隔离该方向并记录错误。

元数据使用 `observation=pcap_capture`、`capture_interface` 和 `timestamp_source=pcap_software`。
时间戳是使 SOME/IP 帧完整的捕获包的软件时间，不是第一片段或网卡硬件时间戳。
监控方向 `rx` 表示观察者收到捕获记录；通信双向性由实际源/目的端点判定，不据此推断某 ECU 的 TX/RX。

状态分别保留解码消息数、原始捕获帧数、解析错误、活跃 TCP 流、IPv4 分片统计及
libpcap 内核/接口丢包计数；
不可获取的丢包计数为 `null`，不伪装成零。最多 128 条 TCP 流、16MiB 重组总缓存；资源超限
停止本捕获并释放缓存，不关闭其他监听。畸形帧记录完整原生异常堆栈后继续观察其他有效流量。
libtins 的超时清理随新数据包处理触发，不承诺无流量时精确在第 60 秒清理。

虚拟网实际测试：独立服务已占用 30608 端口，捕获仍看到 IPv4/IPv6 UDP/TCP 两条正向及
一条反向黄金消息，PCAP 独立核对全部八组方向计数。另测错误 BPF、缺网卡、畸形 UDP 和过滤隔离。
原生 CTest 额外覆盖 VLAN、TCP 乱序、重复重传、IPv6 序号回绕、RST 关闭、同四元组复用和半帧截断。
这些是功能测试，不代表线速、长期内存峰值或无丢包承诺。

### IPv4 分片的隔离、来源与边界

`IPv4Fragments` 复用 `Tins::IPv4Reassembler`，只在外围限制资源、隔离完整单向键并拒绝歧义。
键包含源/目的地址、协议、IP 标识和完整 VLAN 标签序列，每个键独立使用库重组器。
同标识的反向地址、不同协议或 VLAN 不共用片段缓存。完全相同的重复片段不重复计数；
重叠、内容冲突、非法偏移/对齐、矛盾末片及重复首片头长度冲突会隔离整份数据报，
释放其数据缓存，并保留隔离键至超时，后续片段不能复活有歧义的数据。

最多 128 个上下文（含隔离键）、每份数据报 256 个唯一片段、累计 4MiB 跟踪 Payload；
资源超限停止本捕获并释放资源。4MiB 只约束跟踪的数据字节，外层和库内均有副本，
不等于 RSS 上限或已完成内存峰值验收。首次处理片段后固定 30 秒过期，不因重复片段续期。
捕获轮询主动执行过期清理，即使没有新流量也会释放残片；不是硬实时的 30 秒截止保证。

- `active_fragment_datagrams` 是分片上下文数，包含待重组和已隔离的键；
- `fragment_buffered_bytes` 是当前跟踪的 Payload 字节；
- `reassembled_datagrams` / `fragment_error_count` 是本次捕获的累计重组/分片错误数。

停止、故障和进程断开清零活跃上下文及字节，累计计数保留。原生分片错误同时计入解析错误，
日志保留异常栈。消息中的 `ip_reassembled` / `ip_fragment_count` 描述促成本次输出的 IP 包；
对于 TCP，不汇总更早 TCP 段的分片数量。完全重复片段不增加数量。软件时间戳仍来自完成消息
的捕获包，而非首片到达时间或硬件时间戳。

需要捕获分片时不能只使用端口 BPF，因为非首片不带传输层端口；可使用
`ip proto 17 and host 10.77.0.2` 或对应 TCP 协议过滤器，再在解码后筛服务。
虚拟网发送器仅在隔离 netns 内用于测试，不属于产品的 Python 发包回退。

完整 IPv4 选项仍有门禁：固定 libtins 4.0 要求 EOL 位于选项区最后一个字节，单 NOP 后补零
在本轮实际解析失败。[上游解析源码](https://raw.githubusercontent.com/mfontanini/libtins/v4.0/src/ip.cpp)
中的检查与此一致，不能把它称为所有选项都支持，也不能将原始报文静默改写成其他选项。
当前首片头长度冲突测试用四个 NOP 填满 24 字节头，分别验证合法选项重组和冲突隔离。
后续仍需解决 EOL 补零及完整选项矩阵；IPv6 分片也未完成，离线路径遵守相同边界。

监控页面可选择捕获模式、网卡、BPF 和混杂模式，并显示丢包统计与异常。Windows 构建要求
含真实捕获后端的 libpcap/Npcap SDK 配置；默认 null 后端不能冒充可用网卡抓包。详见 Windows 打包说明。

## 原生离线 PCAP/PCAPNG

`POST /api/v1/pcap/import` 将原始上传字节写入独立临时目录，用 `SOAOperator` 启动
`soa_partner run --network`，通过同一长度帧 socket 发送 `pcap_import` 文件路径。
原生进程不创建车辆网络端点，也不重放文件。读取、重组和 SOME/IP 头解码由 C++ 完成。
支持 libpcap 可读取的 PCAP/PCAPNG，以及 Ethernet、RAW IP、Linux SLL、明确 IPv4/IPv6
链路类型；不代表任意链路或 PCAPNG 混合链路均支持。IPv6 分片和完整 IPv4 选项仍受上述门禁限制。

每个原始帧返回连续 `pcap_frame`，结束返回 `pcap_done`；Python 校验帧号、消息序号、原始帧数和
捕获字节数，结束记录完整之前不提交到监控。文件读取中断、消息/Payload 配额超限整份拒绝。
二进制缺失或通道断开返回 503，不回退到 Python 解码；非法文件返回 422。
原生每进程只允许一份离线任务，按有限批量异步读取；最多一百万原始帧。
API 默认上传上限 256MiB、解析时限 120 秒、十万解码消息和 64MiB 累计 Payload。
这些是资源门禁，不是已验证的处理速率。缓冲对象及 JSON 副本不包含在 Payload 额度内。

分片过期用文件时间推进，而不是导入进程运行时长；乱序时间取已见最大文件时间，避免时钟倒退。
文件结束显式报告残留 TCP 半帧和 IPv4 残片，不冒充正常 FIN 或已经经过 30 秒。
`errors` 最多保留前 100 条展示警告，原生日志保留各异常堆栈；该数组长度不是错误总数。
畸形报文与未知非 SOME/IP 流量可以产生警告并继续，文件读取失败不被降格成警告。

消息标记 `observation=pcap_import`、`timestamp_source=pcap_file`、`runtime=vsomeip`、
`wire_verified=false`；保留原始纳秒整数，展示时间截到微秒。
消息时间对应完成报文的原始帧，不是第一段到达时间、导入时间或硬件网卡时延。
分片与 TCP 等待帧计入原始帧/传输统计，SOME/IP 承载帧计数以输出完整消息的帧为准。

固定 libtins 4.0 对 SYN 数据的序号处理存在首字节丢失问题。适配器只在库输入副本内
先交付无数据 SYN，再交付序号加一的数据；不修改原始抓包或增加原始帧计数。
TCP 重组仍由成熟库执行，不另写重组算法。原生及产品集成测试覆盖 IPv4/IPv6、拆段、
重复重传和序号回绕；真实 veth 另验证分片 SYN 数据。

虚拟网抓包经过独立黄金字节审计后，由 `native/tests/audit_offline.py` 调用产品原生导入器，
核对 SD、SAT、ARXML/WTI、恢复请求/响应、双向 IPv4/IPv6 以及分片用例。
各实时捕获用例拥有独立上下文，因此相同四元组/序号的分片 TCP 用例也独立导入核验；
整份文件不会把相同序号重传伪计成新消息。证据为 `offline-audit.json`，不替代性能/长稳验收。
分片黄金向量按源/目标、协议、IP ID 及实际 MF/片偏移选择，不能把同 ID 的完整包或其他流混入。
正式入口还执行六项 `test_audit_pcap.py` 回归：对真实 PCAP 的副本注入同 ID 完整包和其他方向片段，
同时验证多出首片或丢失尾片仍严格失败。副本是审计健壮性证据，不冒充线上注入或设备故障测试。
Make 在宿主提前创建编译/证据目录，并显式传入 `SOMEIP_AGENT_EVIDENCE_OWNER=UID:GID`。
虚拟网脚本成功或失败退出时只将本证据目录归还调用方，保留私有权限、不追随符号链接，
也不覆盖原始失败退出码；五项 Linux UID 回归验证非 root 读取和后续目录创建。
页面和导出报告保留真实解码来源、链路类型与分片统计；演示样例不推断成原生执行。

## ARXML 到原生目录与字典初始化

`POST /api/v1/model/native-catalog` 根据已经导入的模型生成 `catalog`、vsomeip `config`
和 SAT `members` 字典。接口不写文件、不启动二进制、不发包。示例请求：

```json
{
  "application_name": "vehicle_server",
  "application_id": 13073,
  "members": {
    "VehicleStatus": {"role": "server", "transport": "internal", "byte_order": "big"}
  }
}
```

部署按完整 `SERVICE-INTERFACE-REF`、成员引用和 `SERVICE-INTERFACE-DEPLOYMENT-REF` 绑定。
同名接口不会按最后一个名称匹配；一个接口的多个部署不会互相覆盖。
歧义时指定成员的 `service` 完整接口路径、`deployment_path` 和 `instance_id`。
页面的稳定身份优先使用完整部署路径，避免两个部署指向同一行。

基础类型从真实 `SW-BASE-TYPE` 的编码/位宽，以及 Implementation `VALUE` / `TYPE_REFERENCE`
引用解析；不根据类型名推断发送布局。浏览投影仍可显示旧类型提示，但缺少 `wire_schema`
的模型不能用于原生初始化。已保留模型 ID、源文件 SHA-256、接口和部署路径。

目前生成的发送布局是明确的 `scalar-big-endian` / `scalar-little-endian` 配置，参数使用原始值，
不自动执行物理量换算；它不是完整 AUTOSAR 序列化部署解释器。显式序列化属性尚未完整映射时
会拒绝生成，不默默套用此布局。应用类型映射、复杂 struct/union、动态数组、字符串/BOM、
TLV/E2E/对齐和端点拓扑的自动解析仍需实现。原生自定义 catalog 的复合类型能力不等于这里已支持。

方法参数保持声明顺序，INOUT 同时加入请求和响应。事件转换为 `Update{Name}Event`，字段生成
`Get{Name}`、`Set{Name}`、`Update{Name}Event`；字段的事件组由实际引用绑定。一个变量的事件及
字段通知使用该类型的值，Getter 返回该值；Setter 请求字典使用字段信号名。
名称/ID 冲突、未部署成员、未指定实例或缺少事件组均拒绝整份初始化，不静默跳过。

Python 可直接从模型初始化，无需手写整个 catalog：

```python
from pathlib import Path
from someip_agent.arxml.parser import ArxmlParser
from someip_agent.config import Settings
from soa_partner.src.base_partner import S2sBaseClass

model = ArxmlParser().parse(Path("vehicle_service.arxml").read_bytes(), "vehicle_service.arxml")
settings = Settings(_env_file=None, native_binary="soa_partner")
with S2sBaseClass.from_arxml(
    model,
    {"VehicleStatus": {"role": "server"}},
    directory=Path("data/native/new_vehicle_server"),
    settings=settings,
) as partner:
    partner.send_event_notify("VehicleStatus_server", "IgnitionState", 7)
```

输出目录必须不存在，避免覆盖运行中的配置；目录保留 `catalog.json`、`vsomeip.json`、
`model-binding.json` 和原生日志。缺少二进制不会回退到 Python 发包。
在线 UDP/TCP 必须明确 `peer_host`、服务 `port`、本机 `native_unicast`、发送开关和白名单，
并使用独立的应用身份；授权规则复用默认仿真的主机/SD 校验。页面服务生命周期/API
已接通下述真实原生链路；自动身份分配仍未交付，配置导出本身不代替运行验收。

`native/tests/test_arxml_virtual.py` 在两个 netns 生成不同原生配置，通过已有 SAT 字典接口初始化。
UDP/TCP、显式大端/小端均实际验证基础方法、事件和字段三种访问，并校验编号客户端的键名及
响应路由。PCAP 单独核对 32 个请求/响应/通知黄金向量。
此证明仅覆盖所列基础类型和测试模型，不证明任意 OEM ARXML 或性能目标。

## 服务页面与会话 API

服务模型页选择真实部署、实例、角色、明确标量字节序及独立应用 ID 后，可初始化原生会话。
会话复用 `S2sBaseClass` 的成员字典、`SOAOperator` 二进制与 socket，不使用 Python 发包替身。
初始化成功表示进程与成员通道可用；client 的 `OFFLINE` 不等于业务服务在线，操作仍需等服务可用。
当前页面会话不自动重启；原生进程或通道故障会报告，用户停止释放后再初始化。

API 前缀 `/api/v1/services/sessions`：

| 方法与相对路径 | 功能 |
|---|---|
| GET / | 活动会话和有限停止历史，包含 PID、成员状态、模型 ID 与源文件 SHA-256 |
| POST / | 接收与原生目录生成相同的 `NativeCatalogRequest` 成员字典并初始化 |
| POST /{id}/stop | 幂等停止自有进程与通道，释放应用身份 |
| POST /{id}/call | client 方法调用，等待真实响应；无响应方法只等待本地提交回执 |
| POST /{id}/notify | server 事件编码与提交 |
| GET /{id}/requests | 服务端收到的请求、原始 Payload 和 request_id，不自动回显业务响应 |
| POST /{id}/respond | 按成员、接口和真实待处理 request_id 人工响应，重复响应拒绝 |

动作请求使用 `member`、`function`、`args`、`timeout`（有限正数，最多 30 秒）；
响应另带 `request_id`、可选 `return_code` 和 `is_error`。页面可等待 client 调用期间切换
server 人工响应；最近八项结果单独保留，不因另一操作的提交提示覆盖真实方法结果。

内部模式的每个会话使用独立 routing/network，不同会话互不通信；页面可勾选在同一会话
加入相反角色的测试对端，依然需要真实方法请求与人工业务响应，不启用自动 echo。
内部观测标记 `transport=internal`、`direction=sim`。在线 UDP/TCP 使用实际对端与发送授权。
最多 16 个活动会话和 64 条停止历史；`active` 表示身份仍被占用，即使进程故障也可停止释放。
当前默认发生器的 `0x1101` 保留，活动应用名称和 ID 重复返回 409；不自动猜测空闲 ID。

普通方法结果为 `responded/vsomeip_response`。事件、人工响应和无响应方法返回
`submitted/native_submission`，仅证明原生完成编码并调用协议栈，不证明对端接收或物理线上存在。
这些 API 结果与 Trace 均保持 `wire_verified=false`；Trace 展示时间来自后端接收，另保留
原生单调时间。对端接收和线上黄金字节由独立 veth 测试及 PCAP 审计提供证据。
旧 SAT 发送接口不强制请求提交回执，保留既有调用语义。

`native/tests/test_services_virtual.py` 用页面相同 API 与另一虚拟网卡的 SAT 伙伴互通。
两角色、UDP/TCP、明确大小端共八项，覆盖方法、字段 getter/setter 和事件；独立审计新增
56 组端口/角色/方向黄金字节，再交原生离线导入器复核，不仅验证页面 HTTP 返回 200。
这不代表任意 OEM 序列化、线速或长稳已验收。

安装包证据可独立指定目录，避免覆盖前一阶段：

```bash
make native-installed-test NATIVE_IMAGE=someip-agent-vsomeip:services-test \
  NATIVE_INSTALLED_EVIDENCE=build/packaged-services-evidence
```

## Python 调用示例

以下沿用 SAT 的导入、字典初始化和断言形式；两个节点分别使用自己的配置文件，应用名需与
vsomeip JSON 中 `applications` 和 `routing` 一致。服务目录示例在 `native/tests/catalog.json`。

```python
import json
from soa_partner.src.Operator import SOAOperator
from soa_partner.src.base_partner import S2sBaseClass

class DoorServer(S2sBaseClass):
    def send_method_response_DoorService_SetPosition(self):
        request = self.ck_s2s_req("DoorService_server", "SetPosition")
        self.send_method_response(
            "DoorService_server", "SetPosition", json.loads(request["args"]),
            request_id=request["request_id"],
        )

operator = SOAOperator(
    "server", binary="/opt/someip/bin/soa_partner",
    catalog="/opt/someip/catalog.json", config="/opt/someip/server.json",
    log_path="/tmp/soa-server.log",
)
with DoorServer({"DoorService": {"role": "server", "name": "DoorService"}},
                operator=operator) as server:
    server.register_auto_response("DoorService_server", "SetPosition")
    # 测试前置之后执行业务测试；退出上下文时停止本实例拥有的二进制。
```

另一个节点的 client 可调用：

```python
client.wait_for_service_reconnect("DoorService_client", timeout=5)
client.send_request_and_ck_resp(
    "DoorService_client", "SetPosition", {"position": 240},
    {"out": {"position": 240}}, timeout=5,
)
```

控制通道保持 8 位十六进制长度头 + JSON，成员通道保持连续 JSON 文档流，不把 TCP 分包边界
当消息边界。`args` / `result` 为 JSON 字符串，不使用 `eval`。并发请求通过 correlation_id
关联独立等待者，避免互相取走响应。支持 tuple 列表及增量 client_1，传输配置在增量启停时保留。
更多真实用例见 `native/tests/test_virtual.py`。

### SAT 缓存、周期通知与错误语义

- 标准键为 `DoorService_client_1`，带名称的实例为 `DoorService_server_DoorService_BGM`；
  旧 `DoorService_1_client` 仅保留为查找别名，迭代和 socket 所有权不重复。
- `send_event_notify_thread_start/update/stop` 保留调用名称，但周期由原生定时器执行，不启动
  Python 发包线程。间隔 1 至 60000ms；更新不传间隔时继承，非法新值不会取消旧任务。
- 事件/请求/响应缓存最新优先。`ck_s2s_event` 返回实际 JSON，`return_latest_event` 支持查看或
  弹出，`chk_notify` 不消耗事件，`ck_coming_event` 排除调用前历史并用单调时间校验偏差。
  非匹配历史保留；与旧 SAT 某些辅助函数吞掉非匹配数据的行为不同。
- `ck_field`、事件/getter 组合断言、窗口内响应重试、v20 重复接口名逐项匹配已实现。
  模糊规则包括字典子集、无序列表子集、空列表、大小写不敏感字符串和最多四位小数。
- 默认字段订阅显式关闭 vsomeip 的相同值去重，以便 SAT 观察每次真实周期通知。仅将协议栈
  已交付的字段最新值保留到成员 socket 连接，不回放普通事件；离线清空旧字段。
  缓存交付标记 `initial=true` / `observation=vsomeip_field_cache`，不伪造新的线上抓包证据。
- 自动响应按 SAT 调用 `send_method_response_{去掉_server后的标准成员键}_{方法}`，包括实例后缀；
  缺少处理器记录日志但不编造成功回复。互通测试若明确需要 echo，可用扩展 `echo=True`。
- 错误响应保留 `return_code`、`message_type` 和原始 payload。空 ERROR 返回
  `FAILTYPE_OTHER_ERROR`，正常 RESPONSE 解码失败返回 `FAILTYPE_DESERIALIZATION_FAILURE`，
  均通过 correlation_id 交给原请求，不能吞掉后伪装为超时。未把 AUTOSAR 返回码猜测映射成厂商 SDK 错误码。

WTI 六个专用辅助 API、`ck_method_timeout`、原始 PartnerKeyInfo 构造及本实例拥有的进程异常恢复
已接通并在真实 UDP/TCP 上验证；厂商心跳/通道阻塞检测语义尚未完整移植，不能称为完全无差别替换。
当前编号客户端是共享 vsomeip application 的逻辑成员，
并不代表每个成员拥有独立的线上 Client ID；独立应用身份与多实例完整矩阵仍需补齐。
vsomeip 路由管理器按应用 ClientID 去重事件注册，适配器不能假定每次 request_event 都增加引用。
同一服务/实例/事件只由首个逻辑消费者请求，最后一个消费者停止才 release_event；取消订阅按
仍活动成员的订阅集合判断。停止一个编号成员不能破坏其他成员的事件接收，全部停止后允许重新注册。
事件未到达使用 TimeoutError，与部分旧 SAT 断言的 AssertionError 不同。
厂商 X86/idl 部署参数明确拒绝，服务 on-wire 类型必须由 catalog 提供，不猜测复杂布局。

### SAT WTI 和方法耗时审计

- 提供 `ck_wti_warning_and_resp`、`ck_wti_telltale_and_resp`、两个 `ck_wti_coming_*_and_resp`
  和两个 `ck_wti_no_*_and_ck_resp`，沿用 SAT 的参数顺序、`wti_auto` 服务选择、状态字符串转换及
  无特定提示之后的 0.2 秒 Getter 校验。仍需调用方明确提供真实 WTI catalog；测试用动态数组、
  UTF-8 和四字节长度前缀仅证明通用桥接，不宣称已经映射全部 OEM 的 WTI 序列化规则。
- `method_default_timeout` 默认 5.1 秒，作为同步调用的耗时审计阈值；显式 `timeout` 覆盖阈值。
  它不替代原生请求的协议超时配置。直接调用 `send_method_request` 也进入审计；异步调用及
  原生元数据标识的无响应方法不进入同步审计。原生 `ServiceStatus` 额外提供实例、服务 ID 和
  `no_return_methods`，不靠 Python 猜测方法名称。
- 耗时从 Python 发出控制请求前开始，到成员 socket 读到响应结束，使用单调时钟；包括 IPC、
  服务器业务和返回处理时间，不是网卡硬件时延。每个 correlation_id 独立关联，同名并发不互相覆盖。
  原生超时或超过审计阈值的正常响应都会记录；在途请求超过阈值也使 `ck_method_timeout()` 失败。
- `method_is_timeout` 保留 `(接口名, 响应记录时间, 耗时)` 列表形态，可赋值 `[]` 清理记录；清理
  不能让仍过期的在途请求变成通过。审计有界：4096 个在途请求，1000 条记录；容量满拒发，历史
  溢出计数仍导致检查失败，不静默把失败淘汰成成功。发送失败记录完整堆栈，断开清理在途关联。
- 无事件/无特定提示的断言保留无关历史，在断开时抛出错误；正常保持连接并覆盖完整窗口才通过。
  负向断言不是取消订阅，也不能将 socket 断开等同于未发生事件。
- `native/tests/test_sat_wti_virtual.py` 使用字典初始化两个真实服务，在两种传输上执行六个辅助接口。
  `native/tests/test_virtual.py` 覆盖默认阈值、异步、无响应、同名并发超时和 socket 断开。
  被动 PCAP 另核对两个服务/两种传输的 24 个手工推导黄金向量，保留既有 ARXML 和 SD 审计。

### SAT 成员状态与进程恢复

`PartnerKeyInfo(service, role, instance, heartbeat)` 保持原始四参数构造：默认 socket 为 None，
IP/port 为回环地址与 0，具有 start_config/start_args 和独立队列。构造不连接网络或启动进程。
原生适配器可附加 `socket=` / `ip_port=`；原有两个 socket/地址位置参数扩展仍可用。
未连接的对象发送时明确报错，不抛出 None.sendall 的属性错误；关闭无 socket 的对象安全且幂等。

S2sBaseClass 默认监督自己启动的进程，周期 5 秒，与 SAT 原始监督周期一致。退出码 0、1、-9
不自动重启；其他异常退出按当前活动字典重新初始化，保留回调、自动响应处理器和最新周期通知。
已经停止的增量成员不恢复，业务请求/响应及历史事件不重放。旧读线程必须结束后才重建同名成员，
防止旧线程清理新请求。关闭会先停监督，再关闭成员及本实例进程。

未退出的进程另用独立短连接调用原生控制循环 `ping`：不占用业务控制锁、不消费业务响应，
端点取已连接 socket 的数值地址，不在探测时重复 DNS 解析。连接、发送与长度帧接收共享绝对
时限，缓慢逐字节回包不能无限续期。回执必须符合运行模式和控制协议；不兼容回执停止监督并
记录异常，不能靠反复杀进程掩盖版本问题。这只证明控制循环能够响应，不证明所有 vsomeip
线程、成员通道或车辆服务健康。

动态控制端口的 native.ready JSON 与换行在同一次输出中写入，减少与协议栈日志交错的窗口。
Python 使用 JSON 文档边界读取完整就绪记录，兼容旧二进制在 JSON 后拼接日志的情况并记录警告；
未换行的记录等待后续输出，损坏 JSON 或非法端口明确失败并记录异常堆栈，不静默忽略。

扩展参数 `auto_restart=False` 可关闭监督，`monitor_interval` 可配置检查周期，`restart_limit`
默认 3 次/60 秒；超过窗口限制或恢复失败停止自动恢复，保存 last_error 并记录完整异常堆栈。
`liveness_timeout` 默认 1 秒，可设为大于 0 且不超过 3 的有限值；设为 None 仅检查退出码。
`liveness_failures` 默认连续 3 次，允许 1-100；短暂失败后成功会清零连续计数。不保证硬实时
故障检测时延，实际还受检查周期、线程调度及恢复初始化影响。持续无响应使用同一重启窗口预算，
只结束本实例仍持有的同一 Popen 对象，再恢复活动配置；预算用尽不再杀死最后一个进程，需调用
close 清理并检查故障。状态保留失败总数、连续失败数、最近异常/探测耗时及恢复原因。
`attach=True` 不监督、不杀死、不自动启动远端进程。这里只恢复 S2s 所拥有的二进制，不意味着
API 层所有任务、外部设备或远端节点均具有自动恢复能力。恢复状态表示控制配置就绪，远端
服务上线仍须 `wait_for_service_reconnect` 或实际方法/事件验证。

订阅操作使用 correlation_id 等待原生回执，同时保留原有响应缓存。接收线程的回调中调用订阅
接口时不等待自身线程，实际状态随后按原生回执更新。读线程按接收顺序维护 subscriptions，
等待者不覆盖更新的状态。恢复初始化时使用原生已确认的订阅集合，不重新订阅已取消的事件。
未知事件使整次变更失败，不先应用列表中的其他事件。周期启停/更新和成员生命周期串行。

`heartbeat` 保留在四参数配置中；原始参考只说明它用于厂商通道阻塞检测，没有足够证据确认
二进制内部语义或单位，因此目前不模拟厂商心跳消息，也不将进程 poll 检查冒充完整心跳支持。
连续进程退出与暂停但未退出分别验收：隔离 Linux veth 下真实 SIGSTOP client/server，覆盖
UDP/TCP、配置/订阅/周期恢复、在途请求失败且不重放及关闭不复活。独立抓包要求暂停中排队的
SetPosition(123) 不出现在网络上，恢复前后的 57/99 则必须有实际请求和响应；人为插入重放副本
必须使审计失败。此检测不等于 SAT 厂商 heartbeat 的全部语义。

WTI 虚拟网测试用空列表事件做有界的订阅准备探针：服务可用之后必须收到实际通知，才能开始
一次性的业务断言。不能以固定 sleep 代替 SD 订阅确认，也不把准备探针计入业务黄金向量。

## 默认后端仿真

配置 `SOMEIP_AGENT_NATIVE_BINARY` 指向本机可执行文件。macOS 宿主不能直接运行 Linux ELF；
本轮验证在 Linux 容器完成，不提供未验证的 macOS 原生产物。

- 内部模式：本地原生协议栈、无服务网络端口、SD 组播关闭；只输出协议栈 API 观测。
- UDP 模式：`NATIVE_UNICAST` 为本机测试网卡地址；原 `destination_host` 表示授权订阅主机，
  `destination_port` 表示本机服务端口；vsomeip 按客户端 SD 订阅端点发送，不再固定端口盲发。
- 网络发送开关和目标主机白名单同时有效；SD 组播目标也必须授权。
- `metadata.observation=vsomeip_api` / `wire_verified=false` 表示已提交协议栈，不保证报文实际上网。
  线上接收是否成功由对端和 PCAP 证明；发生器计数也不是对端接收计数。

目前白名单在原生路由消息接收层过滤 SD 来源。它不是车辆网络安全认证或完整防反射策略；
恶意端点选项、跨 VLAN、组播及端点级安全策略还需专门验收。真实车辆高风险使用不得越过这些门禁。

## 在线升级

设置页：检查更新 → 验证签名 → 升级到最新版本。后端先下载验哈希，再复制独立升级器到安装目录
之外。升级器校验发行包且返回 prepared 后，主进程才退出；替换后重启并核对健康接口版本。
失败版本与准备失败现场留存，上一版恢复后重启，连续升级会归档更早备份。

开发 `uvicorn ... --reload` 没有自动退出回调，安装接口明确返回 409。发行版入口提供该回调。
生产需配置 HTTPS 清单与受信 Ed25519 公钥；无发布源时明确显示未配置，不能假装已是最新版本。
签名私钥只能在发布环境。`backend/tests/test_update_e2e.py` 用本地受信 HTTPS 源与真实进程验证
整个升级链路，`test_update_worker.py` 验证健康失败回滚及连续升级。

Windows 原生构建、独立 updater 和 ZIP/EXE 打包已接入脚本；当前宿主无法证明 Windows 实机安装、
DLL 加载及 Inno Setup 回滚成功，必须由 Windows 验收补证后才可发布。
