# 原生激励源与数值边界

`native/src/signal_source.hpp` 是可复用的标量激励源，不实现协议或自己的调度线程。
复用原生 Codec、nlohmann JSON、标准库随机源；当前先接入旧 generator_start 与产品
simulation API，并接入真实 ARXML 完整事件周期。旧单信号 API 的 EventGroup=1 和
internal/UDP 边界仍在；完整事件请使用服务会话入口。CSV/阶跃/状态机、跨事件同步时钟、
暂停/单步/倍率由后续小块继续接入。

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
