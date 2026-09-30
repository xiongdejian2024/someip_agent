import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { build } = require('esbuild')
const result = await build({
  entryPoints: [fileURLToPath(new URL('../frontend/src/api/agentStream.ts', import.meta.url))],
  bundle: true, write: false, platform: 'node', format: 'esm',
})
const { streamAgentChat, boundedAgentHistory } = await import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString('base64')}`)
const encode = (event, data) => `event: ${event}\r\ndata: ${JSON.stringify(data)}\r\n\r\n`
const chunks = (text) => {
  const bytes = new TextEncoder().encode(text)
  return new ReadableStream({ start(controller) {
    for (const value of bytes) controller.enqueue(Uint8Array.of(value))
    controller.close()
  } })
}
let captured
const respond = (body) => {
  globalThis.fetch = async (url, init) => {
    captured = { url, ...init }
    return new Response(chunks(body), { headers: { 'content-type': 'text/event-stream' } })
  }
}
const normal = encode('status', { phase: 'generating', message: '开始' })
  + encode('delta', { text: '# 中文🚘\n' })
  + ': 保活\r\n\r\n'
  + encode('delta', { text: '**逐字输出**' })
  + encode('done', { status: 'complete', model: '测试模型' })
respond(normal)
const events = []
await streamAgentChat('中文问题', { signal: new AbortController().signal, allowMutation: false, onEvent: (event) => events.push(event) })
assert.equal(events.filter((event) => event.event === 'delta').map((event) => event.data.text).join(''), '# 中文🚘\n**逐字输出**')
assert.equal(events.at(-1).event, 'done')
assert.equal(captured.url, '/api/v1/agent/chat/stream')
assert.deepEqual(JSON.parse(captured.body), { message: '中文问题', allow_mutation: false })

const context = { page: 'monitor', service_id: 0x5000, method_id: 0x8000, message_id: 'frame-1', source: 'pcap', frozen: true }
const history = Array.from({ length: 20 }, (_, index) => ({ role: index % 2 ? 'assistant' : 'user', content: `第${index}条` + '中文🚘'.repeat(2500) }))
const bounded = boundedAgentHistory(history)
assert.ok(bounded.length <= 12)
assert.ok(bounded.every((item) => item.content.length <= 6000 && !/[\uD800-\uDBFF]$/.test(item.content)))
assert.ok(bounded.reduce((sum, item) => sum + item.content.length, 0) <= 24_000)
assert.ok(bounded.at(-1).content.startsWith('第19条'))
assert.deepEqual(boundedAgentHistory([{ role: 'system', content: '不能提升角色' }]), [])
respond(normal)
await streamAgentChat('跟进', { signal: new AbortController().signal, allowMutation: false, context, history, onEvent() {} })
assert.deepEqual(JSON.parse(captured.body).context, context)
assert.deepEqual(JSON.parse(captured.body).history, bounded)
await assert.rejects(() => streamAgentChat('问题', { signal: new AbortController().signal, allowMutation: false, context: { ...context, service_id: -1 }, onEvent() {} }), /0–65535/)

respond(encode('delta', { text: '未完成回答' }))
await assert.rejects(() => streamAgentChat('问题', { signal: new AbortController().signal, allowMutation: false, onEvent() {} }), /连接提前中断/)
respond(encode('error', { message: '上游失败' }) + encode('done', { status: 'error' }))
await assert.rejects(() => streamAgentChat('问题', { signal: new AbortController().signal, allowMutation: false, onEvent() {} }), /上游失败/)
respond('event: delta\ndata: {坏JSON}\n\n')
await assert.rejects(() => streamAgentChat('问题', { signal: new AbortController().signal, allowMutation: false, onEvent() {} }), SyntaxError)

const abort = new AbortController()
let released = false
globalThis.fetch = async (_url, init) => new Response(new ReadableStream({
  start(controller) {
    controller.enqueue(new TextEncoder().encode(encode('delta', { text: '保留部分内容' })))
    init.signal.addEventListener('abort', () => controller.error(new DOMException('已停止', 'AbortError')))
  }, cancel() { released = true },
}), { headers: { 'content-type': 'text/event-stream' } })
let received = ''
await assert.rejects(() => streamAgentChat('问题', {
  signal: abort.signal, allowMutation: false,
  onEvent(event) { if (event.event === 'delta') { received += event.data.text; abort.abort() } },
}), { name: 'AbortError' })
assert.equal(received, '保留部分内容')
assert.equal(abort.signal.aborted, true)
console.info('智能体传输验证通过：上下文、历史上限、UTF-8 分片、CRLF、保活、完成、错误、异常断流和取消。', { 流已终止: abort.signal.aborted || released })
