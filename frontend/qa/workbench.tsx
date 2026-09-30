import React from 'react'
import { createRoot } from 'react-dom/client'
import App from '../src/App'
import '../src/index.css'

// 仅由 Vite 开发服务器提供的隔离验收页；所有 API 与 WebSocket 均为内存夹具。
const startTime = Date.now() - 20_000
let sequence = 0
let feedTimer: number | undefined
const markdown = '# 诊断结果\n\n已检查 **SOME/IP 服务模型**，以下为隔离测试证据。\n\n| 服务 | 状态 | 数量 |\n| --- | --- | --- |\n| Powertrain | 正常 | 2000 |\n| Body | 待检查 | 12 |\n\n## 建议步骤\n\n1. 检查 `Service ID = 0x5000`。\n2. 冻结报文，再比较时间戳。\n\n```json\n{ "service_id": "0x5000", "method_id": "0x8001", "description": "这是一行用于验证代码块独立横向滚动的很长很长很长很长很长很长很长很长的文本" }\n```\n\n- [x] 已检查服务\n- [ ] 继续验证订阅\n\n> 这里是引用段落。\n\n[Vector 文档](https://www.vector.com/)\n\n<script>window.__unsafeMarkdown=true</script>\n'
const services = Array.from({ length: 131 }, (_, index) => ({
  name: `ECU_${String(index).padStart(3, '0')}_Powertrain_Long_Service_Name_车载域控制器`,
  path: `/qa/${index}`, service_id: 0x5000 + index, instance_ids: [1], major_version: 1, minor_version: 0,
  methods: [], fields: [], events: Array.from({ length: 80 }, (_, eventIndex) => ({
    name: `状态事件_${eventIndex}`, event_id: 0x8000 + eventIndex, event_group_ids: [1],
    signals: [{ name: `Signal_${eventIndex}_Long_Physical_Signal_Name_温度转速状态`, data_type: 'uint16', minimum: 0, maximum: 1000, unit: 'rpm' }],
  })),
}))

function message(index: number) {
  const signal = index % 200
  return {
    id: `qa-${index}`, timestamp: new Date(startTime + index).toISOString(), direction: index % 3 ? 'rx' : 'tx',
    source: '192.168.112.123:30500', destination: '239.192.112.123:30501', service_id: 0x5000 + Math.floor(signal / 10),
    method_id: 0x8000 + signal % 10, message_type: 2, payload_size: 256, return_code: index % 17 === 0 ? 1 : 0,
    is_sd: index % 10 === 0, sd_summary: index % 10 === 0 ? 'OfferService 服务发现' : null,
    payload_hex: '00ab12cd'.repeat(64), signal_values: { [`Signal_${signal}_非常长的车载信号名称_${signal}`]: Math.sin(index / 31) * (signal % 2 ? 100 : 1e8) },
  }
}
let taskIndex = 0
const tasks: Array<{ id: string; running: boolean; config: object; emitted_count: number }> = []
const response = (value: unknown) => new Response(JSON.stringify(value), { headers: { 'content-type': 'application/json' } })
const diagnostics = { received: 20_000, streamChunks: 0, streamCancelled: false, chatRequests: [] as Record<string, unknown>[] }
Object.assign(window, { __qa: diagnostics })

window.fetch = async (input, init) => {
  const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
  if (url.endsWith('/health')) return response({ status: 'ok', version: '0.1.0', service_count: services.length, arxml_loaded: true, llm_configured: true })
  if (url.endsWith('/model/services')) return response(services)
  if (url.endsWith('/network/listeners') || url.endsWith('/monitor/messages')) return response([])
  if (url.endsWith('/simulation')) return response(tasks)
  if (url.endsWith('/simulation/start')) {
    const task = { id: `qa-task-${++taskIndex}`, running: true, config: JSON.parse(String(init?.body)), emitted_count: 15 }
    tasks.push(task)
    return response(task)
  }
  if (url.endsWith('/simulation/stop')) {
    const { simulation_id: id } = JSON.parse(String(init?.body))
    const selected = tasks.filter((task) => !id || task.id === id)
    selected.forEach((task) => { task.running = false })
    return response(selected)
  }
  if (url.endsWith('/agent/chat/stream')) {
    const request = JSON.parse(String(init?.body))
    diagnostics.chatRequests.push(request)
    if (diagnostics.chatRequests.length > 20) diagnostics.chatRequests.shift()
    const encoder = new TextEncoder()
    let timer: number | undefined
    let offset = 0
    diagnostics.streamChunks = 0
    diagnostics.streamCancelled = false
    const stream = new ReadableStream({
      start(controller) {
        const send = (type: string, value: unknown) => controller.enqueue(encoder.encode(`event: ${type}\ndata: ${JSON.stringify(value)}\n\n`))
        send('status', { phase: 'model', message: '正在生成隔离验收响应', model: 'qa-fixture' })
        if (String(request.message).includes('草案') || String(request.message).includes('方案')) {
          const serviceId = request.context?.service_id ?? 0x5000
          const methodId = request.context?.method_id ?? 0x8000
          const plan = {
            name: '隔离验收智能体草案', service_id: serviceId, instance_id: 1, method_id: methodId, interface_version: 1,
            transport: 'internal', destination_host: '127.0.0.1', destination_port: 30490, interval_ms: 100, enable_sd: false,
            generator: { signal_name: request.context?.signal_name ?? `Signal_${methodId - 0x8000}_Long_Physical_Signal_Name_温度转速状态`, kind: 'sine', data_type: 'uint16', minimum: 100, maximum: 900, initial: 500, period_seconds: 2, sequence: [] },
          }
          send('tool', { phase: 'result', id: 'qa-plan', name: 'prepare_simulation', result: { status: 'prepared', simulation_config: plan } })
        } else {
          send('tool', { phase: 'result', id: 'qa-evidence', name: request.context?.message_id ? 'analyze_message' : 'get_service_schema', result: { summary: '隔离夹具证据，不代表实际网络', message_id: request.context?.message_id, service_id: request.context?.service_id } })
        }
        timer = window.setInterval(() => {
          if (offset >= markdown.length) {
            window.clearInterval(timer)
            send('done', { status: 'complete', model: 'qa-fixture', degraded: false, traces: [] })
            controller.close()
            return
          }
          send('delta', { text: markdown.slice(offset, offset + 16) })
          offset += 16
          diagnostics.streamChunks += 1
        }, 100)
        init?.signal?.addEventListener('abort', () => {
          window.clearInterval(timer)
          diagnostics.streamCancelled = true
          controller.error(new DOMException('用户停止生成', 'AbortError'))
        }, { once: true })
      },
      cancel() { window.clearInterval(timer); diagnostics.streamCancelled = true },
    })
    return new Response(stream, { headers: { 'content-type': 'text/event-stream' } })
  }
  throw new Error(`隔离验收页禁止未声明的 API：${url}`)
}

class FixtureSocket {
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  private closed = false
  constructor() {
    window.setTimeout(() => {
      if (this.closed) return
      this.onopen?.()
      sequence = 20_000
      this.onmessage?.({ data: JSON.stringify({ type: 'snapshot', messages: Array.from({ length: 20_000 }, (_, index) => message(index)) }) })
      const control = document.getElementById('qa-feed')!
      control.onclick = () => {
        if (feedTimer) { window.clearInterval(feedTimer); feedTimer = undefined; control.textContent = '开始 1,000 帧/秒输入'; return }
        control.textContent = '停止压力输入'
        feedTimer = window.setInterval(() => {
          for (let index = 0; index < 100; index++) this.onmessage?.({ data: JSON.stringify({ type: 'message', message: message(sequence++) }) })
          diagnostics.received = sequence
        }, 100)
      }
    }, 30)
  }
  close() { this.closed = true; window.clearInterval(feedTimer); feedTimer = undefined; this.onclose?.() }
}
window.WebSocket = FixtureSocket as unknown as typeof WebSocket
createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>)
