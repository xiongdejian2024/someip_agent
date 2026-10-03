# 原生激励源与数值边界

`native/src/signal_source.hpp` 是可复用的标量激励源，不实现协议或自己的调度线程。
复用原生 Codec、nlohmann JSON、标准库随机源；当前先接入旧 generator_start 与产品
simulation API。旧单信号、EventGroup=1 和 internal/UDP 边界仍在，不把它当完整 ARXML
动态事件。完整多信号事件、CSV/阶跃/状态机、同步时钟和网页配置由后续小块继续接入。

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
