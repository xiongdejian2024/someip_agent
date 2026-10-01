# 原生依赖来源与 IPv4 选项补丁

在线协议栈固定 vsomeip 3.5.10，提交 `c4e0db329da9b63f511f3c2456c040582daf9305`。
抓包仍使用 libpcap、libtins 成熟解析/重组能力，不新增 Python 或自写 IP 重组回退。

## 固定 SDK 的 SD 观测解码

在线 SD 状态机仍由 vsomeip 动态库负责。监听、被动捕获与离线导入的 SD 展示解码复用
同一 SDK 的 `implementation/service_discovery` 模型源码。上游动态库未导出这些模型符号，
因此 CMake 直接静态编译 14 个上游源文件，不修改其协议实现或导出表。Docker 构建验证固定
源码提交，并检查 `interface`/`implementation` 没有相对 HEAD 的修改。

适配层检查完整帧/数组长度、Option 解码是否完整、引用索引及 4096 个 Entry/Option 配额；
异常原生日志有堆栈，输出保留 `payload_hex` 和 `sd_error`。Python 仅校验/展示版本化元数据，
不以旧 `SdPayload.decode` 回退。库将未知 Option type 归一为 255，不能用该值冒充原始类型；
原始字节保留。固定库将线上的 Eventgroup Reserved/Counter 解码到 `get_reserved()`，
适配层仅从该已解码字段取低四位，不使用未填充的 `get_counter()`。

Linux 发行包在 `third-party/vsomeip-source.zip` 保存该固定 SDK 源码（不含 .git），同时保存许可与
`native-CMakeLists.txt`。这项迁移不代表所有未知扩展可解码、硬实时达标或 Python 信号分析
已经迁出；未支持的报文必须显式失败，不改变在线服务发现行为。

## libtins 固定输入

- 上游标签：`v4.6`；提交：`2d2f7012d9f3a16d684a55ba39f1215b6aef5429`。
- 来源：[上游提交](https://github.com/mfontanini/libtins/tree/2d2f7012d9f3a16d684a55ba39f1215b6aef5429)。
- 本地补丁：`native/patches/libtins-ipv4-options.patch`。
- 补丁 SHA-256：`34099f36c40249770f3c7485f135b45d5053fd6bcdc93a86793174553a3e9c3f`。
- 补丁后 `src/ip.cpp` SHA-256：`fe3b9717eb8b53e5fef9736854cf5fb18f055139f2a812b4e7e24ffd0e26f486`。
- CMake 使用 `find_package(libtins 4.6 EXACT CONFIG REQUIRED)`，禁止混用旧头文件与新库。
- Linux 与 Windows 均核验源码提交、补丁可应用性及整个修改后文件哈希；缓存含额外修改时拒绝。
- TCPIP/PCAP 开启，Dot11/examples/上游测试关闭；本项目原生 CTest 和虚拟网矩阵执行验收。

## 局部修复与边界

旧 4.0 及固定 4.6 的原始选项循环要求 EOL 位于 IHL 最后一个字节，合法的 EOL 提前结束
加零补齐会被拒绝。按 number 低五位分类还会把完整 type 为 0x80/0x81 的 TLV 当作单字节
选项；序列化尺寸有相同分类问题。补丁仅修改该源文件三个位置：

1. 解析时按完整 type 识别 EOL/NOP，读取 TLV 长度前检查头边界。
2. EOL 后检查所有剩余字节为零，而不是要求立刻到头末尾。
3. 计算序列化选项尺寸时采用相同完整 type 分类。

依据是 [RFC 791 第 3.1 节](https://www.rfc-editor.org/rfc/rfc791.html#section-3.1) 的 EOL、
选项长度和零补齐定义。补丁不是完整选项语义实现，不执行路由与时间戳更新；IPv6 分片和
复制位跨分片一致性仍未验收。原始 PCAP 不改写。SYN payload 的成熟库适配保持独立。

## 制品与许可证

libtins 使用 BSD-2-Clause，许可证取自上述固定源码的 `LICENSE`。
Linux 镜像将 LICENSE 和补丁保存在 `/opt/third-party/libtins/`；Windows 构建器收集为
`libtins-copyright` 和 `libtins-ipv4-options.patch`。仓库 `.gitattributes` 固定补丁为 LF；
Windows clone 禁止自动 CRLF 转换，以便源码哈希一致。
源码提交和哈希锁定不替代全部供应链门禁：仍需最终制品 SBOM、漏洞/许可扫描和发布签名。
Windows 仍要求真实 Npcap SDK/抓包 triplet；此脚本变更不等于已在 Windows 编译或网卡验收。
