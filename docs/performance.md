# 原生与 Python 链路的短负载证据

这些是 2026-10-01 单次 Linux Docker/veth 软件观测，不是硬实时、线速、最大吞吐或长稳承诺。
线上 SOME/IP/SD 和序列发生器由 vsomeip 原生进程执行，Python 保留 SAT 接口、控制与业务回调。
把底层换成 C++ 不代表整条 Python RPC 调用链路没有开销。

## 复现与口径

```bash
make native-image
make native-performance NATIVE_PERFORMANCE_EVIDENCE=build/performance-new-run
# 仅测原生序列发生器，适合相同机器上的修改前后对照：
make native-performance NATIVE_PERFORMANCE_EVIDENCE=build/performance-new-comparison \
  'PERFORMANCE_ARGS=--native-only --duration 3'
```

每次使用新证据目录；有旧 profile/report 时拒绝覆盖。只在 `--network none` 临时容器内建
bridge/veth/netns，不操作宿主或车辆网卡。仅挂载测试和证据，无 PYTHONPATH，并确认实际
S2sBaseClass 来自 site-packages。使用现有 SAT 适配器、原生 PCAP 导入器和 dpkt 独立核对，
没有新增库或自写协议栈。16 个阶段顺序运行，阶段间停止自己启动的原生进程。

- RPC：UDP/TCP × blob 32/1024 字节 × 1/4 并发，各 200 次。blob 外另有 4 字节序号、
  4 字节长度和 SOME/IP 头。延迟从 worker 开始调用至校验返回，包含 Python 请求编码、
  成员 socket、原生网络、Python 服务端 echo 回调及返回；不含任务在线程池中排队时间。
  RPC/s 则使用整个阶段的成功完成数/墙钟时间，不是纯 C++ 协议栈上限。
- Python 逐条事件：每条通过兼容 SAT 的 send_event_notify 调用，目标周期 10/1 ms，持续
  3 秒。该接口无提交回执，因此调用次数不是原生确认数，更不是远端收到次数。
- 原生 sequence：一次提交包含 1300/4000 个唯一值的配置，周期 10/1 ms，持续 3 秒。
  原生 emitted_count 只是尝试通知数，必须与 PCAP 和客户端逐序号核对；禁止序列循环。
  production_seconds 包括一次构造/控制启动停止/计数查询，首 tick 立即执行，不是纯稳态窗口。
- 三条链路采用相同四字节递增事件 payload。发生器超时后跳过过期 tick，不赶发；低于目标
  频率不能直接叫作网络丢包。Python 调度则可短时赶发，平均约 1000/s 不等于每条严格 1 ms。
- 每个 profile 保留 samples.json、server/client 日志、wire.pcap、tcpdump.log、wire-audit.json、
  result.json；总报告含二进制 SHA、平台、模块真实路径、负载参数。逐条核对黄金字节、
  请求/响应类型、方向、服务端口、RPC Client ID、缺失/重复/非预期序号；UDP 另做独立解码。
  TCP 使用产品重组器去除重传，不能把 TCP 段数当消息数。
- 分位数为 nearest-rank，不插值；线上间隔来自 PCAP 软件时间，TCP 是消息完成帧时间，
  同帧多消息可有零间隔。客户端回调另记录 monotonic 时间。不使用跨进程时钟相减算单向延迟。
- CPU 是自有 server/client PID 的 /proc CPU 增量/观测墙钟，100% 表示一核；核对 PID
  starttime 防复用。Python CPU 含编排、回调、接收与采样，不是纯发送 CPU；均不含离线审计，
  也不是整机/虚拟机/抓包总 CPU。native RSS 为窗口末值，harness peak RSS 是该 harness
  生命周期累计高水位，不能把它当每个 profile 独立峰值。
- verified 只表示本次已产生消息在各阶段完整核对、无 RPC 失败/原生 error/采集内核丢包；
  不表示达到目标频率或 P99 门限。并发 worker 的数字序号可以乱序提交，单独报告而非隐藏。
  CI 每次执行 16 阶段并保存独立 artifact，不给共享 runner 设置未经论证的 CPU/P99 门限。

## 实测环境与结果

Apple M1 宿主上的 Linux 5.10.76-linuxkit aarch64，Docker 提供 4 个 vCPU，Python 3.11.2，
vsomeip 固定 3.5.10。未并行运行其他本项目验收/构建，但宿主背景活动和虚拟机调度未锁定。
数据来自 `build/performance-installed-evidence/report.json`。16 阶段全部 verified：1600 次
RPC 返回成功、3200 条请求/响应和 13204 条事件均逐序号核对，采集内核丢包均为 0。
所有事件阶段线上/客户端缺失、重复、非预期序号和数字序号逆序均为 0。

### 完整 Python RPC 链路

各行均 200 次，不是固定三秒计数。单位：延迟 ms、完成数 RPC/s。
短测出现小 payload 比大 payload 慢，不能据此归纳稳定的大小与性能关系。

| 传输 | blob 字节 | 并发 | RPC/s | P50 | P95 | P99 |
|---|---:|---:|---:|---:|---:|---:|
| UDP | 32 | 1 | 56.2 | 17.193 | 23.658 | 28.461 |
| UDP | 32 | 4 | 143.5 | 29.178 | 37.745 | 51.383 |
| UDP | 1024 | 1 | 81.9 | 11.949 | 14.061 | 14.459 |
| UDP | 1024 | 4 | 276.8 | 14.188 | 16.647 | 17.414 |
| TCP | 32 | 1 | 55.5 | 17.202 | 23.212 | 27.453 |
| TCP | 32 | 4 | 147.9 | 26.773 | 30.738 | 32.748 |
| TCP | 1024 | 1 | 81.2 | 11.891 | 14.289 | 14.593 |
| TCP | 1024 | 4 | 298.1 | 12.774 | 16.830 | 18.146 |

### 逐条 Python 通知与原生发生器

“线上 P99”是相邻消息间隔，不是 RPC 或单向延迟。“产生数”分别为 Python 调用数、原生
尝试通知数；本次全部与线上/客户端收到数一致。CPU 仅服务端原生进程，单位为一核百分比。

| 路径 | 传输 | 目标周期 ms | 产生数 | 条/秒 | 线上 P99 ms | 原生服务 CPU % |
|---|---|---:|---:|---:|---:|---:|
| Python 逐条 | UDP | 10 | 300 | 99.9 | 18.320 | 12.0 |
| Python 逐条 | UDP | 1 | 3000 | 1000.0 | 1.875 | 44.2 |
| 原生 sequence | UDP | 10 | 301 | 100.2 | 15.493 | 12.3 |
| 原生 sequence | UDP | 1 | 3001 | 999.3 | 1.317 | 34.9 |
| Python 逐条 | TCP | 10 | 300 | 100.0 | 21.080 | 12.0 |
| Python 逐条 | TCP | 1 | 3000 | 1000.0 | 1.901 | 37.9 |
| 原生 sequence | TCP | 10 | 301 | 100.2 | 16.089 | 11.6 |
| 原生 sequence | TCP | 1 | 3001 | 999.5 | 1.396 | 34.9 |

1 ms 的 Python 总 CPU 约 UDP 13.4% / TCP 13.5%，原生 sequence 的 Python 总 CPU 约
7.5% / 8.0%；后者仍包含每条客户端通知的 Python 接收与回调。不同计数口径、独立进程的
短阶段和采样噪声都保留，不能把差值等同于 Python 解释器或 GIL 的纯成本。

## 序列配置复制热点的前后对照

旧 generator 在 tick、JSON value("sequence") 和异步 lambda 中深复制整份序列。
现在启动时只复制一次，tick 共享 shared_ptr<const Json> 快照，并引用 generator/sequence；
不改变公开字典/成员 socket、定时器跳过策略和停止 epoch。空/缺失序列保留 initial 语义，
新增 UDP/TCP 实际回归验证调用方修改字典不影响后续 tick、停止后计数不再增长且无新通知。

前后目录分别为 `build/performance-buffered-baseline-evidence` 和
`build/performance-optimized-comparison-evidence`；4 阶段使用相同序号、序列长度、采集配置。
均逐序号核对完整且采集内核丢包为 0。以下不是多轮统计或显著性结论。

| 传输 | 周期 ms | 原 CPU % | 新 CPU % | 原条/秒 | 新条/秒 | 原 P99 间隔 ms | 新 P99 间隔 ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| UDP | 10 | 13.3 | 13.9 | 99.6 | 98.2 | 18.653 | 24.108 |
| UDP | 1 | 51.5 | 34.2 | 999.0 | 999.3 | 1.850 | 1.550 |
| TCP | 10 | 13.6 | 11.3 | 99.6 | 100.2 | 18.912 | 17.527 |
| TCP | 1 | 53.1 | 35.2 | 999.3 | 998.6 | 1.866 | 1.453 |

本次高频 CPU 减少约三分之一，但低频 UDP 的 P99 变差，不能说所有抖动都改善。
第一轮 `build/performance-baseline-evidence/profile-04` 抓包报告 2362 个内核丢包，PCAP 只
解出 93/2869 条事件，而客户端收到全部 2869 条，整体标为 observation_failed、完整保留。
随后去除逐 packet 的 -U 输出强制刷新，增加采集缓冲为 16 MiB，仍使用 immediate-mode，
停止时 SIGINT 等待退出/flush，再重新测两份对照；不能把失败 PCAP 缺失当作协议栈丢包。

原基线二进制 SHA-256：`17aaecfd9d4286b5c25615f25ea3cd1ed2cddced015bba817941c27de0a15fc9`。
新二进制 SHA-256：`aff31f69de53d7409887e7ef71fe470739f285fc69afbcbaf6a28b17d234606d`。
完整流量/故障、长稳、多服务/多身份、重负载抓包以及 Windows/HIL/生产环境基准仍需继续。

## libtins 4.6 选项补丁后的独立复测

上面的表属于发生器配置复制优化时的二进制，不直接代表后续依赖更新。本次固定 libtins 4.6
和选项补丁后，单独重跑相同 16 阶段，证据为 `build/ip-options-performance-evidence/report.json`。
产品二进制 SHA-256：`d89a1abd3e8b2b800c6196196edbae059d4f3b9abd0a39963ce2d17fc838309e`；
镜像及依赖来源见 [执行记录](vsomeip-progress.md) 与 [依赖固定输入](native-dependencies.md)。
测试从 site-packages 加载 SAT 适配器，无产品源码挂载，运行前其他本项目功能验收容器均已退出。
宿主背景活动、Docker 调度和软件时间戳的限制保持不变。

16 阶段全部 verified：1600 次 RPC 成功返回，3200 条请求/响应、13201 条事件逐序号核对。
原生错误、RPC 失败、线上/客户端缺失、重复及非预期序号均为零，16 份采集内核丢包均为零；
另有 12 项权限/清理和 10 项统计口径回归通过。没有将旧库数字复制为新库验收结论。

以下为本次 1 ms 目标周期事件阶段，P99 是相邻线上消息的软件时间间隔，不是单向延迟：

| 路径 | 传输 | 客户端收到数 | 条/秒 | 线上间隔 P99 ms |
|---|---|---:|---:|---:|
| Python 逐条 | UDP | 3000 | 1000.0 | 1.871 |
| 原生 sequence | UDP | 3000 | 999.1 | 1.500 |
| Python 逐条 | TCP | 3000 | 1000.0 | 1.860 |
| 原生 sequence | TCP | 3001 | 999.5 | 1.542 |

10 ms 原生 TCP 本次产生/收到 299 条、约 99.5 条/秒，低于目标频率但已产生消息全部核对完整。
RPC P99 本次各配置约 14.65–44.50 ms，仍包含 Python 业务回调与 IPC，不是原生协议栈上限。
这是单次短测，无多轮显著性结论；不据此宣称库升级提升性能、最大吞吐、严格周期或长稳达标。

## 独立 application 改造后的默认链路复测

`build/applications-performance-evidence/report.json` 使用最终安装包和二进制
`6ac843c9ef6871576f2d40aab43df02d7792639425af3c608dc2dc1e2c3dbd92`，运行前本项目的
功能验收与构建均已结束。环境与上文一致，仍为每个事件阶段 3 秒、每个 RPC 阶段 200 次；
16 阶段全部 verified：1600 次 RPC 成功，3200 条请求/响应及 13193 条事件逐序号核对完整，
16 份采集内核丢包均为 0，无缺失/重复/非预期序号或运行中的原生成员错误回调。
另有 12 权限/清理与 10 统计口径回归通过，产品模块来源为 site-packages。

| 路径 | 传输 | 目标周期 ms | 客户端收到数 | 条/秒 | 线上间隔 P99 ms |
|---|---|---:|---:|---:|---:|
| Python 逐条 | UDP | 1 | 3000 | 1000.0 | 1.939 |
| 原生 sequence | UDP | 1 | 3001 | 999.4 | 1.282 |
| Python 逐条 | TCP | 1 | 3000 | 1000.0 | 1.937 |
| 原生 sequence | TCP | 1 | 3000 | 998.8 | 1.565 |

本次 10 ms 原生 UDP 只产生/收到 291 条，约 96.9 条/秒，线上间隔 P99 22.521 ms；
发生器按原策略跳过过期 tick，没有把少产生的 tick 算作网络丢包或隐藏低于目标的频率。
RPC P99 各配置约 14.21–51.98 ms，仍是完整 Python/IPC/业务回调链路，不是纯 C++ 上限。
不因部分 P99 更低就宣称性能改善，也不因一次更高就归因于身份隔离或 Python 解释器。

这些性能阶段沿用默认 application，证明旧调用链路在本轮改造后仍可完整核对；不是独立
application 的完整负载/长稳矩阵。双 Client ID 的并发和停止恢复由单独 10 项功能用例及
114 对请求响应黄金审计证明，两种证据不能互相替代。IPC 代码未显式设置 TCP_NODELAY，
可作为后续分层时延实验的假设，但尚未证明 Nagle/延迟 ACK 是上述 RPC 尾延迟的原因。
