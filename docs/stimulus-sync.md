# 完整事件公共时钟

同步组复用冻结 ARXML 的原生 Codec、EventStimulus 和 Boost.Asio；Python 只提交控制命令。
同一服务会话内最多一个组、1 至 16 个事件，可包含同一 server 的不同事件或多个 server
成员；同一成员／事件不能重复。成员的独立周期或旧标量发生器必须先明确停止，不能隐式覆盖。

## 配置与运行

在已初始化、就绪的服务会话中，通过 `POST /api/v1/services/sessions/{id}/sync/start`
一次提交全部事件。下面使用 VehicleStatus 的实际原生接口名；不是任意模型通用示例。

```json
{
  "paused": true,
  "speed": 1,
  "events": [
    {
      "member": "Provider_server",
      "function": "UpdateSpeedChangedEvent",
      "args": 42.5,
      "interval_ms": 20,
      "sources": [{"path": "", "generator": {"kind": "step", "initial": 42.5, "step_at_ms": 29, "step_value": 24.5}}]
    },
    {
      "member": "Provider_server",
      "function": "UpdateIgnitionStateEvent",
      "args": 9,
      "interval_ms": 30,
      "sources": [{"path": "", "generator": {"kind": "step", "initial": 9, "step_at_ms": 29, "step_value": 10}}]
    }
  ]
}
```

默认 `paused=true`，准备完成不产生业务样本。所有事件的完整模板、类型、源、CSV 与图
都先预编译；任何事件失败，组不会部分启动。公共时钟步长为事件周期的最大公约数；
示例为 10ms。第一帧逻辑 0 同时采样两事件，之后分别在 20ms 和 30ms 倍数采样。
单步推进一个公共帧，不保证每个帧每个事件都发送。源序列索引用本事件的实际样本序号，
时间函数／CSV／状态图用同一精确整型毫秒时钟；独立种子不因倍率或暂停而重置。

控制入口 `POST /api/v1/services/sessions/{id}/sync/control`：

- `{"action":"resume"}`：从下一个未采样帧继续，不重放上一帧。
- `{"action":"pause"}`：取消原生定时器，回执后公共时钟及提交计数不再增长。
- `{"action":"step"}`：只允许暂停组，采样一个公共帧，随后仍暂停。
- `{"action":"speed","speed":2}`：倍率仅支持 0.25、0.5、1、2、4；只改变墙钟调度。
- `{"action":"stop"}`：释放组激励及成员所有权，允许重新配置独立周期或新组。

查询 `GET /api/v1/services/sessions/{id}/sync` 返回组 ID、active/paused、下一帧逻辑时间、
帧序号、倍率，以及每事件实际提交计数、最近采样时刻和状态名。组活动时不能用独立周期
控制绕过组所有权；释放任一组 server 成员会停止整个组，其他成员通道不会被顺手释放。
活跃组不能就地覆盖；修改事件集合／源时先停止，再准备新组。

SAT 适配器新增 `start_event_sync(events, paused=True, speed=1)`、
`event_sync_status()` 和 `control_event_sync(action, speed=...)`；接口名称仍可使用 SAT
友好名称，由已有命名适配器归一化。旧周期 API 不变。

## 边界与证据

周期仍为 1 至 60000 整型毫秒；每事件最多 128 个路径，CSV／序列／状态图沿用原有预算。
配置受已有 4MiB ASCII 控制帧限制；CSV 预编译后的完整控制命令同样受限，不放宽 IPC。
迟到时减慢，不追赶或跳过业务样本。暂停无法撤回协议栈或网络中已提交的报文。

各事件先在同一原生帧采样／编码，然后按配置顺序提交 vsomeip；共享逻辑时间不代表
网络原子发送、同时到达或硬实时。发送 Trace 中的 `sync_group_id`、`sync_logical_ms`、
`sync_frame_index` 仅为调度元数据，接收端不猜测它们；`wire_verified` 仍为 false。
UDP/TCP 消费者的黄金原始字节是接收证据，不把控制回执当作线上交付证明。

脚本自有原生进程异常恢复会重新预编译最后确认配置，从逻辑 0 准备并保持暂停；组 ID 更新，
必须明确 resume，不冒充连续时间轴。网页会话维持既有故障策略，不自动重建原生进程。
这里不提供跨进程／跨会话的分布式时钟。工程 `sync_groups` 可保存可移植同步配置，
读取旧格式自动补空列表，恢复不运行。自动场景与 CLI 已通过 sync_start/control/status/stop
接入同一原生公共时钟，冻结输入、归属清理与记录证据沿用既有执行器，详见 `scenarios.md`。
网页服务运行面板提供独立“多事件公共时钟”：绑定工程服务配置、载入／复制周期草案、
编辑完整事件列表、校验加入工程草案与顶部保存。选择配置本身不会覆盖编辑框。
JSON 使用既有无损解析器，uint64 参数／种子精确保留，编辑预算 2 MiB，不执行表达式。
网页“准备同步组”固定 paused=true，即使加载的工程草案 paused=false 也保持安全暂停；
实际开始采样须明确恢复或单步。草案与当前会话 application 名称／ID 不匹配时禁止准备。
没有已确认的原生状态、会话已停止、写操作进行中或结果未知时禁用运行按钮。
失败命令不自动重发，自动轮询不解除写保护，须明确“核对原生状态”后再选择操作。
暂停状态才能单步，运行状态才能暂停；实际帧、逻辑时间、事件计数、状态名来自原生。
停止同步组保留服务会话；同步管理成员的独立周期按钮禁用，后端仍执行所有权校验。
旧单信号工作集的并发启动不能冒称公共时钟同步。
