import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { build } = require('esbuild')
const result = await build({
  entryPoints: [fileURLToPath(new URL('../frontend/src/api/adapters.ts', import.meta.url))],
  bundle: true, write: false, platform: 'node', format: 'esm',
})
const { normalizeServices, normalizeNetworkListener, normalizePcapResult } = await import(
  `data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString('base64')}`,
)
const services = normalizeServices([
  { name: '同名服务', path: '/Interfaces/Same', deployment_path: '/Deployments/One', service_id: 0x1234, instance_ids: [1] },
  { name: '同名服务', path: '/Interfaces/Same', deployment_path: '/Deployments/Two', service_id: 0x1235, instance_ids: [2] },
  { name: '同名服务', path: '/Other/Same' },
])
assert.equal(new Set(services.map(service => service.id)).size, 3, '同接口多部署必须具有不同页面身份')
assert.equal(services[0].path, '/Interfaces/Same')
assert.equal(services[1].deploymentPath, '/Deployments/Two')
assert.equal(services[2].deployed, false)
console.info('服务适配器验证通过：同名、同接口多部署和未部署接口均保留独立身份。')
const listener = normalizeNetworkListener({
  id: 'fragment-capture', config: { mode: 'pcap', capture_interface: 'eth0' }, running: true,
  active_fragment_datagrams: 2, fragment_buffered_bytes: 32, reassembled_datagrams: 3,
  fragment_error_count: 1,
})
assert.equal(listener.active_fragment_datagrams, 2)
assert.equal(listener.fragment_buffered_bytes, 32)
assert.equal(listener.reassembled_datagrams, 3)
assert.equal(listener.fragment_error_count, 1)
console.info('抓包适配器验证通过：IPv4 分片缓存、重组和错误计数完整传递。')
const offline = normalizePcapResult({ runtime: 'vsomeip', link_type: 113, reassembled_datagrams: 4, fragment_error_count: 2 })
assert.equal(offline.runtime, 'vsomeip')
assert.equal(offline.link_type, 113)
assert.equal(offline.reassembled_datagrams, 4)
assert.equal(offline.fragment_error_count, 2)
assert.equal(normalizePcapResult({ source_name: 'demo.pcap' }).runtime, null, '演示和旧接口不得推断为原生执行')
assert.equal(normalizePcapResult({ runtime: 'unknown' }).runtime, null)
console.info('离线适配器验证通过：真实原生来源与分片统计完整传递，演示不伪标。')
const configBundle = await build({
  entryPoints: [fileURLToPath(new URL('../frontend/src/api/serviceConfig.ts', import.meta.url))],
  bundle: true, write: false, platform: 'node', format: 'esm',
})
const { byteOrderOverride } = await import(
  `data:text/javascript;base64,${Buffer.from(configBundle.outputFiles[0].text).toString('base64')}`,
)
assert.deepEqual(byteOrderOverride('arxml'), {}, '遵循源布局时不得发送默认大端覆盖')
assert.deepEqual(byteOrderOverride('big'), { byte_order: 'big' })
assert.deepEqual(byteOrderOverride('little'), { byte_order: 'little' })
console.info('服务配置验证通过：源布局缺省不覆盖，明确大小端由后端检查部署冲突。')
