# Classic ARXML 完整引用绑定

当前支持从 Socket/SoAd 的明确引用重建业务投影，复用已有 lxml；不新增 SOME/IP 协议栈。
已接通完整引用图和显式 payload 布局归一化；整个 Classic transformer 的线上互操作仍未完成。

## 引用与来源

每条 `SOCKET-CONNECTION-IPDU-IDENTIFIER` 的 Header ID 提供 Service/Method ID；其
`PDU-TRIGGERING-REF` 必须通过完整路径依次解析：

1. `PDU-TRIGGERING` 的 `I-PDU-REF`；
2. `I-SIGNAL-I-PDU` 中的 `I-SIGNAL-TO-I-PDU-MAPPING`；
3. `I-SIGNAL-REF` 和 `SYSTEM-SIGNAL-REF`；
4. `SENDER-RECEIVER-TO-SIGNAL-MAPPING` 的目标数据原型，或
   `CLIENT-SERVER-TO-SIGNAL-MAPPING` 的目标操作及 call/return 方向；
5. `DATA-TRANSFORMATION-REF`、transformer chain 及 I-SIGNAL props 的 transformer 引用。

完整链存在时不根据 PDU 名称前缀或同名接口猜测。RPC 使用被引用的操作，不任取接口中的
第一个操作；请求和响应绑定保存在同一方法中。Sender/receiver 只保留明确映射的原型，
不自动把同接口的其他信号加入 payload。不同 ECU 上下文指向同一目标时可去重，不同目标
不合并。

方法/事件的 `classic_bindings` 保存 trigger、PDU、映射、I-SIGNAL、SYSTEM-SIGNAL、目标、
方向、起始位置及 transformation/transformer 完整路径，随模型 JSON 保留，源文件 SHA 仍
由模型记录。引用本身不宣称启动地址、位布局、消息类型或 session 语义已经验证。
无响应标记仍属于浏览投影，不能仅凭 sender/receiver 接口推导线上 RequestNoReturn；后续必须
核对 I-SIGNAL props 和实际请求/响应部署。
源文件中的数值 `MESSAGE-TYPE` 也不能直接作为线上报文类型字节。4.4.0 的模型枚举定义与
协议编码须分别校验，见 [System Template 表 7.13](https://www.autosar.org/fileadmin/standards/R18-10_R4.4.0_R1.5.0/CP/AUTOSAR_TPS_SystemTemplate.pdf)；本阶段不解释该未验证属性。

## 歧义和安全门禁

- 缺失引用、目标类型不符、多个业务目标、非法起始位置、chain 外的 props 引用明确报错；
- 重复完整路径按被引用的成员隔离，不选择第一个，也不阻断其他可浏览服务；
- 同一 Header ID 指向不同操作时不覆盖已有绑定；
- 缺失链的旧名称投影仅供浏览，服务附带 `deployment_errors`，不能生成原生配置；
- 完整引用链也不能解除 Classic payload 门禁；目前仍明确拒绝自动原生初始化。

真实 V6.12.0 文件包含 ALIGNMENT=64、HEADER-LENGTH=64 和 active session 属性，不能把
这些值直接当作原生 Codec 的成员 padding，或在 payload 中自行追加头。4.4.0 的
SWS_SomeIpXf_00259/00263 区分变长非末尾元素的对齐与固定长度元素：不能为所有成员统一
自动插入 padding；SWS_SomeIpXf_00037 要求以整条 SOME/IP 消息起点计算对齐。
依据为 [AUTOSAR CP 4.4.0 Transformer 规范](https://www.autosar.org/fileadmin/standards/R18-10_R4.4.0_R1.5.0/CP/AUTOSAR_SWS_SOMEIPTransformer.pdf)。下一步仍需接通
Classic 的完整头/session 部署与互操作验证，不能用 AP 对齐用例代替车型服务验收。

## 显式布局与原生对齐

Classic 解析器按绑定方向读取 I-SIGNAL props 和唯一 serializer 的 description；要求明确
SOMEIP 1.0.0 / SERIALIZER / 64-bit header，不把该 header 加入业务 payload。
请求与响应可以使用不同布局；字节序、显式结构/数组长度字段传播到嵌套类型，源模型不被修改。
缺失长度字段、多个变体、多 transformer、非零 START-POSITION、未知/TLV/细粒度属性仍拒绝。
这里没有按 MESSAGE-TYPE 数字或 SR 接口类型推导报文类型，也没有解除服务部署门禁。

共用原生 Codec 已支持 32/64-bit 对齐：以整条消息起点（含 16 字节 SOME/IP 头）计算，
包括外层结构/数组长度字段；仅变长非末尾元素补齐，不对固定成员或消息末尾统一补齐。
结构/数组长度不计算自己的前缀，包含其内部实际编码字节；外部 padding 不混入该长度。
这些规则依据上述 4.4.0 规范的 00037、00218、00259、00263；AP 显式属性也复用相同 Codec。
编码写零 padding，解码按布局跳过 padding，不把固定数据强制对齐。

原生 CTest 包含绝对偏移、空数组、末尾变长、固定成员不补齐及变长元素数组黄金字节。
`backend/tests/test_classic_layout.py` 验证真实类型/映射链上的嵌套布局、call/return 独立字节序
和不支持属性的拒绝。虚拟网另通过 AP fixture 验证相同 Codec 的 UDP/TCP、双字节序、
1/2 字节长度前缀、RPC/事件/字段读写；它不是 Classic 车型完整运行证据。

## 已有证据

`backend/tests/test_arxml_classic.py` 验证完整链、同名接口隔离、多操作精确选择、call/return、
数据方法的浏览投影、歧义路径隔离、错误引用/方向、模型 JSON 和原生配置门禁。

`native/tests/audit_service_names.py` 在核对源名称时同时导出完整 Classic 绑定和部署错误，
仍返回 `runtime_verified=false`。它只读，不激活车型/版本，不迁移车辆 IP/VLAN。
当前真实 V6.12.0 与 H47A/V_6_12_0 的 131 个服务名/ID 一致，1067 个成员保留 2228 条引用
绑定；本轮 `build/alignment-source-evidence/names.json` 重新核对同样的三份源文件，934 个
重复引用计数的信号布局错误仍未消除，缺失复合长度字段的完整堆栈在 parse.log。
源引用/显式布局解析成功不是车型全部服务可用。
