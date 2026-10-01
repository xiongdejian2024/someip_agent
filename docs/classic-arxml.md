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
协议编码须分别校验，见 [System Template 表 7.13](https://www.autosar.org/fileadmin/standards/R18-10_R4.4.0_R1.5.0/CP/AUTOSAR_TPS_SystemTemplate.pdf)；本阶段只保存原文，不解释该未验证属性。

每条 binding 的 `header_properties` 按完整 transformer 引用保存所有 I-SIGNAL props 变体：
protocol、transformer version、header length、message type、SR session 和 interface version。
原文 0 保持字符串 `"0"`，不转为线上 REQUEST。缺失 props 显式记录，不从 call/return 的
另一方向借值；多个 description 只记录数量，不任取首个 interface version。旧模型缺少此
列表时使用空列表，新增元数据不会解除部署门禁。

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
未识别版本的缺失长度字段、多个变体、多 transformer、非零 START-POSITION、未知/TLV/细粒度属性仍拒绝。
这里没有按 MESSAGE-TYPE 数字或 SR 接口类型推导报文类型，也没有解除服务部署门禁。

共用原生 Codec 已支持 32/64-bit 对齐：以整条消息起点（含 16 字节 SOME/IP 头）计算，
包括外层结构/数组长度字段；仅变长非末尾元素补齐，不对固定成员或消息末尾统一补齐。
结构/数组长度不计算自己的前缀，包含其内部实际编码字节；外部 padding 不混入该长度。
这些规则依据上述 4.4.0 规范的 00037、00218、00259、00263；AP 显式属性也复用相同 Codec。
编码写零 padding，解码按布局跳过 padding，不把固定数据强制对齐。

## CP 4.4.0 的可选前缀与 VSA_LINEAR

仅当源 schemaLocation 明确为 r4.0 / AUTOSAR_00046.xsd 时，未配置可选 struct/fixed-array
长度属性按无前缀处理；未知版本仍要求明确宽度。不是把所有动态数组默认成四字节。
依据为 00042 的深度优先顺序及 00216/00220 的可选前缀条件。

Implementation 的 VSA_LINEAR 必须为有序 size indicator + ARRAY payload 的 STRUCTURE。
类型解析保留二者源 SHORT-NAME、整数位宽和唯一变长维度上界，不按字段名字猜语义。
CP 4.4.0 的 SWS_SomeIpXf_00076/00234 区分字典中有效元素个数与线上有效数据字节长度；
长度字段类型来自 size indicator。字典例 `{"validElements":2,"words":[4660,43981]}` 的
uint16 payload 长度是 4 字节，不是 2，也不另写一个 count 字段。
依据为 [CP 4.4.0 Transformer](https://www.autosar.org/fileadmin/standards/R18-10_R4.4.0_R1.5.0/CP/AUTOSAR_SWS_SOMEIPTransformer.pdf)
及 [Software Component Template 的 TPS_SWCT_01647/01649](https://www.autosar.org/fileadmin/standards/R18-10_R4.4.0_R1.5.0/CP/AUTOSAR_TPS_SoftwareComponentTemplate.pdf)。

原生编码要求计数是非负整数且等于列表实际长度；解码从字节长度重建有效个数与源字典形状。
未知 profile、错误顺序/元数据、有符号 indicator、Application/Implementation profile 冲突、
多维 profile、计数不一致或超上界均拒绝。当前 ARRAY-SIZE 声明接受 uint32 正整数，
不按声明上界展开或分配元素；实际元素、payload 和 IPC 预算仍单独检查。
AP 显式属性继续使用其明确长度宽度，不套 CP 默认规则。

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
绑定。早期 alignment-source-evidence 的934条布局错误保留；本轮
`build/vsa-source-evidence/names.json` 的重复引用计数为112。早期73个 VSA 类型中63个的
uint32 indicator/类型图解析成功，10个因旧声明上界拒绝。最新
`build/large-array-dispatch-source-evidence/names.json` 中73个类型图全部解析，布局错误
103条、部署错误177条；其他缺失数组宽度、无绑定布局、空结构与重复引用仍有门禁，
完整堆栈保存在各自 parse.log，不覆盖旧失败记录。
源引用/显式布局解析成功不是车型全部服务可用。

`build/classic-header-source-evidence/names.json` 另保存 2223 条按信号/变体去重的 header
记录：原文 message type 0/1/2/3 分别为 953/491/491/288，SR session 均为 active。
它们来自已绑定成员，不冒充源文件全部 props，更不代表车型服务已启动；131 名称/ID、
103 布局错误、177 部署错误及 `runtime_verified=false` 均保留。
