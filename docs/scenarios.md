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

网页入口与基线比较由后续小块接通；整轮 P1/P2/安装包验收仍未完成。
