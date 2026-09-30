import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { build } = require('esbuild')
const result = await build({
  entryPoints: [fileURLToPath(new URL('../frontend/src/data/monitorBuffer.ts', import.meta.url))],
  bundle: true,
  write: false,
  platform: 'node',
  format: 'esm',
})
const { BoundedBuffer, MonitorBuffer, TRACE_CAPACITY, SAMPLE_CAPACITY, messageSample } = await import(
  `data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString('base64')}`
)

const ring = new BoundedBuffer(3)
for (let index = 0; index < 10; index++) ring.push(index)
assert.deepEqual(ring.values(), [7, 8, 9])
ring.clear()
assert.deepEqual(ring.values(), [])

const makeMessage = (index) => ({
  id: String(index), timestamp: new Date(1_700_000_000_000 + index).toISOString(), direction: 'RX',
  source: '127.0.0.1:30501', destination: '127.0.0.1:30502', protocol: 'SOME/IP',
  serviceId: '0x1234', methodId: '0x8001', messageType: 'NOTIFICATION', length: 8,
  signalValues: { [`信号_${index % 200}`]: index },
})
const buffer = new MonitorBuffer()
for (let index = 0; index < 20_000; index++) buffer.append(makeMessage(index))
const view = buffer.snapshot()
assert.equal(view.messages.length, TRACE_CAPACITY)
assert.equal(view.samples.length, SAMPLE_CAPACITY)
assert.equal(view.messages[0].id, '19999')
assert.equal(view.messages.at(-1).id, String(20_000 - TRACE_CAPACITY))
assert.ok(view.samples.every((sample) => Object.keys(sample.values).length === 1), '不得向其他信号伪造采样')
assert.equal(buffer.dirty, false)

buffer.replace([makeMessage(2), makeMessage(1)])
assert.deepEqual(buffer.snapshot().samples.map((sample) => sample.time), [1_700_000_000_001, 1_700_000_000_002])
buffer.replace([])
assert.deepEqual(buffer.snapshot(), { messages: [], samples: [] })
assert.equal(messageSample({ ...makeMessage(0), timestamp: 'invalid' }), null)
assert.deepEqual(messageSample({ ...makeMessage(0), signalValues: { 布尔: true, 文本: '跳过', 无效: NaN } }).values,
  { '0x1234/0x8001/布尔': 1 })
buffer.append(makeMessage(3))
buffer.clear()
assert.deepEqual(buffer.snapshot(), { messages: [], samples: [] })
console.info('监控缓存验证通过：20,000 帧 / 200 路信号，容量、顺序、清空、快照替换和稀疏采样均正常。')
