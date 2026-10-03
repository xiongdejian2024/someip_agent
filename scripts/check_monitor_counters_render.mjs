import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { build } = require('esbuild')
const result = await build({
  stdin: {
    contents: `
      import { createElement } from 'react'
      import { renderToStaticMarkup } from 'react-dom/server'
      import { MonitorPage } from './src/pages/MonitorPage'
      const props = {messages: [], samples: [], streamState: 'online', source: 'live', onClear: async () => {}}
      export const unknown = renderToStaticMarkup(createElement(MonitorPage, props))
      export const known = renderToStaticMarkup(createElement(MonitorPage, {...props,
        streamCounters: {backend_epoch: 'test', counter_scope: 'backend_process_lifetime', published_total: 100,
          retained_messages: 2, history_evicted_total: 98, cleared_total: 0, subscriber_discarded_total: 13,
          current_subscriber_discarded: 0, active_subscribers: 2},
        bufferCounters: {trace_evicted_total: 17, sample_evicted_total: 23}}))
    `,
    resolveDir: fileURLToPath(new URL('../frontend/', import.meta.url)), loader: 'js',
  }, bundle: true, write: false, platform: 'node', format: 'cjs', jsx: 'automatic', loader: { '.css': 'empty' },
  define: { 'process.env.NODE_ENV': '"production"' },
})
const rendered = { exports: {} }
new Function('require', 'module', 'exports', result.outputFiles[0].text)(require, rendered, rendered.exports)
const { known, unknown } = rendered.exports
assert.match(unknown, /订阅队列丢弃 未知/)
assert.match(known, /订阅队列丢弃 13（当前连接 0）/)
assert.match(known, /历史缓冲淘汰 98/)
assert.match(known, /Trace 淘汰 17/)
assert.match(known, /波形采样淘汰 23/)
assert.match(known, /不与应用缓冲淘汰相加/)
console.info('监控计数首次渲染通过：未知/零值、后端队列、历史及前端 Trace/采样分别显示；不冒充浏览器点击。')
