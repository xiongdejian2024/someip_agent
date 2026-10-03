# 产品自动场景

场景不是 Python/Shell 脚本，也不能执行任意 URL。执行器复用已保存工程的模型和服务/监听
草案；输入被冻结为本次工程修订，运行不修改当前工程或模型，不绕过本机发送门禁。
只有明确的 POST 启动才运行；导入、恢复和查看历史均不会自动运行。

## API 与步骤

`POST /api/v1/scenarios/runs` 提交 `project_id` 和 `definition`，返回 202 与 UUID。
GET 同路径列出有界历史摘要，GET `/{id}` 读取当前步骤/最终结果；POST `/{id}/cancel`
取消并等待资源清理。GET `/{id}/junit` 和 `/{id}/report` 提供标准 XML/转义 HTML 报告。
每次运行关联实际 HTTP 请求 ID、程序版本、工程 ID/修订、ARXML 源 SHA-256、定义 SHA-256
和独立原始记录 ID。原始记录可通过既有 recordings API 回放和校验 ZIP 导出。

步骤包括：启动/停止工程服务或监听、等待成员就绪、call、notify、等待实际待响应请求后
respond_next、完整事件周期启动/停止、等待匹配报文、确定性断言、等待和有界并行组。
`start_service.profile` 引用工程 services 名称；其余服务步骤的 session 引用同一名称。
监听 profile 是工程 listeners 的非负下标字符串；并行组不能启动/停止资源或嵌套。
内部报文方向为 sim；消费者接收应匹配成员名，不能以 sim 冒充线上 rx。

assert.path 从已保存的步骤结果 save_as 或 parameters 开始，只按 JSON 字段/数组下标
访问，不使用 eval。eq 比较精确 JSON 值（大整数不会转换成浮点），approx 使用绝对误差，
exists 要求路径实际存在。通知 submitted 只证明原生接受命令；需要接收证明时必须再等待
消费者或监听报文并断言。参数替换只允许完整 `{"$param":"名称"}` 节点，不做字符串求值。

```json
{
  "project_id": "替换为已保存工程的 UUID",
  "definition": {
    "format": "someip-agent-scenario",
    "format_version": 1,
    "name": "内部周期接收",
    "cases": [{"speed": 42.5}],
    "steps": [
      {"kind": "start_service", "profile": "pair"},
      {"kind": "wait_ready", "session": "pair"},
      {"kind": "cycle_start", "session": "pair", "command": {
        "member": "Provider_server", "function": "UpdateSpeedChangedEvent",
        "args": {"$param": "speed"}, "interval_ms": 20
      }},
      {"kind": "wait_message", "session": "pair", "match": {
        "member": "Consumer_client", "payload_hex": "422a0000", "direction": "sim"
      }, "save_as": "event"},
      {"kind": "assert", "path": ["event", "payload_hex"], "expected": "422a0000"}
    ],
    "cleanup": [{"kind": "cycle_stop", "session": "pair", "command": {
      "member": "Provider_server"
    }}]
  }
}
```

上述 pair 需在工程中明确保存 VehicleStatus 的 Provider/server 与 Consumer/client 配置。
接口名字和载荷来自该工程 ARXML，不将示例名字套到未知车型上。

## 公共时钟场景步骤

同一场景自有服务会话支持以下步骤，复用真实原生同步 API，不增加 Python 周期循环：

- `sync_start`：`session` 引用本次 `start_service.profile` 名称，且必须二选一：
  `profile` 读取冻结工程的同名 `sync_groups`，或 `command` 给出完整原生同步配置。
- `sync_control`：`session` 加 `command`，action 为 pause/resume/step/speed/stop；
  speed 的倍率白名单与原生接口一致。暂停/恢复不重置源或逻辑时间。
- `sync_status`：只读查询实际原生状态，可通过 `save_as` 保存后断言。
- `sync_stop`：停止该自有会话的组，无 command；可放入业务 cleanup，重复停止安全。

所有步骤都不能引用用户已有会话 UUID。同步写动作禁止放入 parallel；只读状态可并行。
取消期间已发出的原生写命令先等待完成，再释放本次自有会话；迟到失败保留异常堆栈。
运行期间修改工程不会替换冻结的同步草案，CLI 与网页场景使用同一执行器和证据链。

```json
[
  {"kind":"start_service","profile":"pair"},
  {"kind":"wait_ready","session":"pair"},
  {"kind":"sync_start","session":"pair","profile":"pair","save_as":"prepared"},
  {"kind":"assert","path":["prepared","paused"],"expected":true},
  {"kind":"sync_control","session":"pair","command":{"action":"step"}},
  {"kind":"sync_status","session":"pair","save_as":"clock"},
  {"kind":"assert","path":["clock","frame_index"],"expected":1},
  {"kind":"sync_stop","session":"pair"}
]
```

原生倍率返回浮点（如 2.0），eq 保持精确 JSON 比较，整数 2 与浮点 2.0 不混同；
需要数值近似比较时明确使用 approx。调度回执不代替消费者报文接收，应另行 wait_message。

## 清理与完整性

每个参数用例执行后释放它拥有的全部资源，失败/超时/取消也执行清理；不调用 stop-all，
不关闭用户已有会话。原生资源创建被取消时先等待创建结束并登记归属，再释放。
业务 cleanup 总等待上限 30 秒，之后仍释放资源；不能用大量 delay 阻止取消无限返回。
最多 4 路运行、20 组参数、200 个动作（包含并行子步骤），单次最长 600 秒加清理。
步骤输出上限 512 KiB、运行输出预算 6 MiB、持久结果上限 8 MiB、历史最多 500 次。
满额明确拒绝，不悄悄删历史或截断后判通过。列表只返回摘要，不把全部大报告一次载入。

每运行独立记录只收集登记后的自有会话/监听来源，不混入用户其他资源。资源创建期间、
登记前的生命周期报文不属于业务步骤证据；业务步骤在登记后执行。记录额度 16 MiB，
队列丢弃、额度用尽、封存失败和清理错误都不能判通过。最终状态仅在封存/持久化后交付。
进程异常退出后的 running 历史标 interrupted，不自动重新执行、不声称资源已清理。
这是产品运行结果，不是开发 pytest 测试报告；AI 不参与最终判据。

## 无界面 CLI

先启动工作台后端（不需要打开网页），再调用安装后的 `someip-agent-scenario`；Linux 完整
发行包共用现有主程序入口 `./someip-agent scenario`，不要求另装系统 Python。
源码可用 `.venv/bin/python -m someip_agent.main scenario`。参数与 API 使用同一工程快照。

```bash
someip-agent-scenario --server http://127.0.0.1:8765 \
  --project 工程UUID --definition scenario.json \
  --result result.json --junit result.xml --html result.html
```

退出码：0=passed 且清理完成，1=业务失败/取消/中断/清理未完成，2=客户端或报告导出错误，
130=用户 Ctrl+C。客户端超时/读取失败/Ctrl+C 会取消已知的本次运行并等待清理；不重试
启动 POST，响应不确定不能重复启动。报告独占创建、权限 0600，不覆盖已有证据。
服务地址不接受内嵌凭据；默认只连接本机，真实网络权限仍由后端本机门禁决定。

## 网页入口

所有页面的工程栏下方可展开“自动场景与产品测试历史”。编辑/导入声明式 JSON、校验与
导出定义均不运行；“明确运行已保存工程”才启动。执行器使用页面显示的已保存修订，
不是工程未保存草案。默认示例只做参数断言，不能当作真实通信测试。
历史可选择查看，运行中按秒有界轮询，失败停止并提示手动刷新，不自动重新启动。
最终结果可下载 JUnit、查看 HTML、下载原始记录 ZIP。取消只作用于所选运行。
完整 JSON 保留大整数，步骤表显示参数组、动作、结果和耗时，清理错误单独呈现。

## 基线比较与完整证据

GET `/{id}/inputs` 读取封存工程/场景快照，GET `/{id}/compare/{baseline}` 比较终态运行。
网页选择历史与基线后明确比较，并可载入历史定义到编辑器；载入不运行。
比较程序版本、后端/原生执行代码 SHA、ARXML、完整工程配置、参数/种子和精确断言值。
条件不同时只展示差异，不冒充同条件回归通过；动作累计耗时不是硬实时指标，并行步骤可能
重叠。运行中不能成为基线。输入和结果分别校验 SHA；旧历史未封存输入仍可读报告，但
不能在升级后重新计算哈希而冒充可信输入。

GET `/{id}/evidence` 导出 ZIP：工程/场景输入、运行结果、模型投影、原始 ARXML（存在时）、
独立原始记录、JUnit、HTML 和本运行审计。审计按运行取全，不受全局最近 200 条影响。
清单逐附件记录大小和 SHA，运行/工程修订/请求 ID/源模型/执行代码交叉关联；生成后重新
校验实际写成的内容。离线校验还核对嵌套录制的分段顺序、大小、SHA 和累计帧数。
缺原 ARXML、记录丢弃/中断或清理未完成时明确 `complete=false`，不补造源文件。
完整性不代表业务通过：失败或明确取消的运行也可有完整失败证据。

CLI 增加 `--evidence evidence.zip`：下载并校验，不完整时退出 1，非法证据退出 2。
离线命令为 `someip-agent evidence-verify evidence.zip`；源码可使用
`.venv/bin/python -m someip_agent.main evidence-verify evidence.zip`（设置源码 PYTHONPATH）。
校验只读、不解压到宿主路径、不导入工程、更不执行其中内容；0=完整且有效，1=有效但
声明不完整，2=校验失败。文件/成员/展开大小有上限，重复名称和任意路径被拒绝。

不导出本机设置或应用密钥；工程与场景禁止携带凭据/发送授权字段，但原始业务载荷仍可能
包含业务敏感信息，分享前需按业务制度处理。SHA 是变更检测，不是数字签名，也不能防御
同时重写附件和全部摘要的对手；本功能不使用或导出正式发布私钥。
整轮 P0–P2、最新独立安装包与新增 CLI 冻结入口验收仍待完成。
