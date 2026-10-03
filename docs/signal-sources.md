# 原生激励源与数值边界

`native/src/signal_source.hpp` 是可复用的标量激励源，不实现协议或自己的调度线程。
复用原生 Codec、nlohmann JSON、标准库随机源；当前先接入旧 generator_start 与产品
simulation API，并接入真实 ARXML 完整事件周期。旧单信号 API 的 EventGroup=1 和
internal/UDP 边界仍在；完整事件请使用服务会话入口。阶跃已接入两类入口，CSV 接入完整
事件周期；时间状态机、跨事件同步时钟及在线暂停/单步/倍率仍待后续小块。

## 整数与随机

- 常量、序列与整数随机值保留 JSON int64/uint64，不经过 double；监控 last_value 与
  signal_values 同样保留精确值，不能为了波形展示强转为浮点。
- float32/float64 的观测值经实际 Codec 编码再解码，显示实际类型的量化结果；例如
  float32 输入 0.1 显示 0.10000000149011612，对应黄金字节 3dcccccd。
- 配置 initial/minimum/maximum/sequence 保留数值类型；字符串数值、bool、非有限浮点
  与未知字段明确拒绝。旧整数配置中精确安全范围内的 1.0 可兼容，分数或超过 2^53−1 的
  浮点整数明确拒绝，不把已经失真的 JSON 输入当作精确整数。
- `seed` 是可选 uint64 整数，默认 0；每次启动使用独立 mt19937_64，不再共享静态随机流。
  相同类型、范围、种子和随机库实现按输出序号复现；不承诺跨标准库/系统生成相同随机流。
  这不是密码学随机源。
- 整数随机使用 uniform_int_distribution，可覆盖 int64/uint64 全范围。正弦/斜坡仍使用
  浮点计算，整数范围超过 ±(2^53−1) 时拒绝；不能默默降低精度或声称硬实时。
- 序列最多 8192 项，全部成员启动前经 Codec 校验；旧空/未提供序列保留 initial 语义。
  非法替换不会取消原先正常任务。停止释放激励源，异步调度使用 epoch 拒绝过期回调。

请求示例（仅内部 API 观测，不证明线上接收）：

```json
{
  "service_id": 4660, "method_id": 32769, "interval_ms": 10,
  "transport": "internal",
  "generator": {
    "kind": "random", "data_type": "uint64", "signal_name": "value",
    "minimum": 0, "maximum": 18446744073709551615, "seed": 123
  }
}
```

每次 start 都是明确写操作，真实网络仍受全局开关及白名单控制。原生配置拒绝返回 422，
运行时缺失/断开返回 503，不混淆输入错误与不可用。智能体的发生器字段白名单复用同一个
Pydantic extra=forbid 契约，新增 seed 不会绕过现有 mutation 门禁。

## 完整 ARXML 动态事件

服务页“完整事件周期”及 `/api/v1/services/sessions/{id}/cycles/start|update` 的完整参数
请求可增加 `sources`。空数组保持固定载荷；完整目录、实例、字节序和 EventGroup 取该
会话冻结的 ARXML，不读取当前全局模型重新猜布局。周期仍在 C++/Boost.Asio 运行，
Python 只提交配置、读取状态和监控，不逐周期编码/发送。

```json
{
  "member": "Provider_server", "function": "UpdateEnvelopeChangedEvent", "interval_ms": 20,
  "args": {"tag": 7, "samples": [4660, 43981], "bytes": [1, 2],
    "matrix": [[1, 2, 3], [4, 5, 6]], "nested": {"temperature": -2}},
  "sources": [
    {"path": "/tag", "generator": {"kind": "sequence", "sequence": [7, 8], "seed": 0}},
    {"path": "/samples/1", "generator": {"kind": "sequence", "sequence": [43981, 2], "seed": 0}},
    {"path": "/nested/temperature", "generator": {"kind": "sequence", "sequence": [-2, 3], "seed": 0}}
  ]
}
```

- 路径遵循 JSON Pointer（`~1` 转义 `/`，`~0` 转义 `~`）；空路径绑定根标量。
  类型由冻结 schema 推导，不接收另造 data_type、字段或偏移。只绑定现有标量/数组元素，
  不扩展数组或改变变长数组数量；VSA 仅允许已有 payload 元素，数量指示器保持真实值。
- 最多 128 个源，路径最多 512 字节，禁止重复路径。完整模板、每个源的全序列、组合初值
  与首样本在替换旧任务前验证；首样本预检使用副本，不消耗正式随机流。模板必须完整，
  未绑定字段保持原值；组合编码仍受 Codec 帧预算控制，运行失败会停止任务并记录原生堆栈。
- 同一个事件全部源共享样本序号与逻辑毫秒时间；从 t=0 开始，每次通知推进一个周期。
  过载不跳过逻辑样本，实际发送可以变慢，不声称硬实时。启动/更新重新初始化源与逻辑时钟；
  更新保留累计通知计数。每个 server 成员仍最多一个周期任务；不同事件、旧标量任务冲突
  必须先明确停止，不隐式覆盖或自动停掉用户任务。
- 固定参数载荷仅编码一次；动态载荷按完整事件编码。监控复用实际类型解码结果，按 JSON
  Pointer 展示最多 128 个数值/布尔叶节点；超出报告 `signal_values_truncated`，原始报文不裁剪。
  接收值来自该成员冻结目录，不能用当前新导入模型解释旧会话。产品监控仍明确
  `wire_verified=false`；实际 veth 接收另有黄金原始字节测试，不把 API trace 当抓包。
- 工程保存/恢复和场景的 `cycle_start` 复用该配置，包括序列与种子。恢复/载入不启动任务；
  必须明确启动。旧 SAT start/update 调用兼容，新增 sources 为 keyword-only 可选参数。

## CSV 完整事件激励

服务页支持导入 UTF-8 CSV 文件或编辑 CSV 时间轴。周期请求增加可选 `csv_text`，工程与
场景保存原始文本，不保存宿主文件路径；SAT start/update 同样接受 keyword-only 参数。
省略或 `null` 停用 CSV；网页清空文本后省略该字段。显式空文本拒绝，不冒充有效文件。

```csv
time_ms,/tag,/nested/temperature,/samples/1
0,9,-3,43981
29,10,4,2
```

首列必须为 `time_ms`，从 0 开始严格递增的 uint64 整型毫秒；不能重复、倒序或使用小数。
后续列为唯一 JSON Pointer，根标量使用空列名。每格使用 JSON 标量字面量：整数、有限
浮点、布尔或字符串；字符串按标准 CSV 引号规则再转义。字符串只作为数据，不执行公式。
拒绝空格子、null、结构/数组、NaN/Infinity、整数越界与浮点溢出/下溢。路径是否存在、
每格是否符合实际叶节点类型，由冻结 ARXML 的原生 Codec 在替换旧任务前校验全部行。
整数不接受浮点形式，布尔不接受数字代替；float32 显示实际编码量化值。

配置阶段复用 Python 标准库 csv/JSON 校验并预编译为原生时间轴；周期内不解析 CSV，
也不增加 Python 逐周期编码/发送。原生按同事件逻辑毫秒采样：行间保持前值，末行后保持
末值，不循环或插值；发送周期跨过多行时只发该时刻最近的值，不保证每行单独发出。
上述 29 ms 切换配合 20 ms 周期，在逻辑 40 ms 首次发送新值，不承诺硬实时。

## 时间状态机

公开 `state_machine` 源使用 `initial_state` 和最多 128 个 `states`。每状态有唯一 ASCII
`name` 和与冻结标量 schema 相符的 `value`。定时转换指定正整型 `duration_ms` 与存在的
`next`；终态不设置两者，进入后保持。允许初态前缀进入循环、自循环；拒绝零时长、重复名、
不可达状态和累计 uint64 毫秒溢出。示例：

```json
{"kind":"state_machine","initial_state":"idle","states":[
  {"name":"idle","value":9,"duration_ms":29,"next":"active"},
  {"name":"active","value":10}
]}
```

全部状态值在任务替换前用现有原生 Codec 校验，整数不转 double；float32 观测反映编码量化。
布尔状态必须是 JSON `true`/`false`，字符串和 bytes 仅在对应冻结叶类型允许，文本不执行。
编译后的状态图用标准库二分和整数取模采样，不建立 Python 定时/发送线程、不逐步追赶循环。
旧单信号发生器按原生经过时间，完整事件各路径按该事件共享逻辑毫秒采样。
采样晚到时选择当前时刻的状态，不保证每个短暂状态都会实际发出。

完整周期 API 的 `active_states` 与发送 trace 元数据记录最近实际样本的路径／状态名；
这些名称不在线上报文中，接收者不会从字节猜测状态名，也不证明远端交付。
网页、工程和自动场景复用相同配置；打开工程不启动，异常原生重建从逻辑零重新运行最新图。
本功能是**时间触发**状态机，不包含报文条件转换、脚本表达式或硬实时保证。

原文最多 1 MiB UTF-8、8192 行数据、128 个信号列，每格最多 65536 字符；编译产物
最多 2 MiB，为既有 4 MiB IPC 帧及完整参数留空间。与普通 sources 合计最多 128 个，
路径不能重叠；未绑定字段保持完整模板值。文件读取失败保留旧文本，配置/原生校验失败
保留正在运行的旧任务；异常记录堆栈，不打印完整 CSV。正常停止释放源，原生异常恢复
重新预编译最新已确认配置，并从逻辑 0 重新开始；不将恢复当作连续无中断时间轴。

网页恢复/工程导入/场景载入均不自动发送。UDP/TCP × 大/小端接收、最新配置异常恢复及
工程/真实场景的验收证据见 `p0-p2-delivery.md`，专项不替代整轮安装包与完整 CI。
