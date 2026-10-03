import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { build } = require('esbuild')
async function load(relative) {
  const result = await build({ entryPoints: [fileURLToPath(new URL(relative, import.meta.url))], bundle: true, write: false, platform: 'node', format: 'esm' })
  return import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString('base64')}`)
}
const { parseJson, stringifyJson } = await load('../frontend/src/api/json.ts')
const { api } = await load('../frontend/src/api/client.ts')
const { streamAgentChat } = await load('../frontend/src/api/agentStream.ts')
const { messageSample } = await load('../frontend/src/data/monitorBuffer.ts')
const source = '{"u64":18446744073709551615,"i64":-9223372036854775808,"nested":[9007199254740993],"id":4660,"float":1.25}'
const value = parseJson(source)
assert.equal(value.u64, 18446744073709551615n)
assert.equal(value.i64, -9223372036854775808n)
assert.equal(value.nested[0], 9007199254740993n)
assert.equal(typeof value.id, 'number')
assert.equal(value.float, 1.25)
assert.equal(stringifyJson(value), source)
assert.deepEqual(parseJson(stringifyJson(value, 2)), value)
assert.throws(() => parseJson('{"a":1,"a":2}'), /Duplicate/)
assert.throws(() => parseJson('{"u64":1e30}'), /安全范围/)
assert.throws(() => parseJson('1e400'), /安全范围/)
assert.throws(() => parseJson('1e-400'), /underflow/)
assert.equal(messageSample({ timestamp: new Date().toISOString(), serviceId: '1', methodId: '2', signalValues: { value: value.u64 } }), null, '不得静默把大整数转换为浮点波形')

globalThis.window = { setTimeout, clearTimeout }
const originalFetch = globalThis.fetch
let submitted
try {
  globalThis.fetch = async (_url, options) => {
    submitted = options.body
    return new Response(`{"status":"responded","result":${source}}`, { headers: { 'Content-Type': 'application/json' } })
  }
  const result = await api.serviceCommand('session', 'call', { member: 'member', function: 'function', args: value })
  assert.equal(parseJson(submitted).args.u64, value.u64)
  assert.equal(result.result.u64, value.u64)

  const events = []
  globalThis.fetch = async () => new Response(`event: tool\ndata: {"tool":"call","result":${source}}\n\nevent: done\ndata: {"status":"complete"}\n\n`, { headers: { 'Content-Type': 'text/event-stream' } })
  await streamAgentChat('检查整数', { signal: new AbortController().signal, allowMutation: false, onEvent: event => events.push(event) })
  assert.equal(events[0].data.result.u64, value.u64)
} finally {
  globalThis.fetch = originalFetch
  delete globalThis.window
}
console.info('整数精度验证通过：int64/uint64、嵌套参数、HTTP 往返、SSE、详情/导出与波形边界。')
