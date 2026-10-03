import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const {build} = require('esbuild')
const result = await build({stdin:{contents:`
  import {createElement} from 'react'
  import {renderToStaticMarkup} from 'react-dom/server'
  import {ServiceSyncPanel} from './src/components/ServiceSyncPanel'
  export {syncActions,syncEvents,syncSessionMatches} from './src/workbench/syncPresentation'
  export {api} from './src/api/client'
  export const html=renderToStaticMarkup(createElement(ServiceSyncPanel,{onChanged:()=>{throw new Error('首次渲染不得发送或保存')}}))
`,resolveDir:fileURLToPath(new URL('../frontend/',import.meta.url)),loader:'js'},bundle:true,write:false,platform:'node',format:'cjs',jsx:'automatic',loader:{'.css':'empty'},define:{'process.env.NODE_ENV':'"production"'}})
const module={exports:{}}
new Function('require','module','exports',result.outputFiles[0].text)(require,module,module.exports)
const {syncActions,syncEvents,syncSessionMatches,api,html}=module.exports
assert.match(html,/准备同步组（保持暂停）/)
assert.match(html,/保存／加载不运行/)
assert.match(html,/disabled="">单步公共帧/)
assert.match(html,/不是线上交付计数/)
assert.throws(()=>syncEvents('[]'),/1–16/)
assert.throws(()=>syncEvents('[null]'),/完整 JSON/)
assert.throws(()=>syncEvents(' '.repeat(2*1024*1024+1)),/2 MiB/)
const events=syncEvents('[{"member":"P_server","function":"UpdateEEvent","args":18446744073709551615,"interval_ms":20,"sources":[{"path":"","generator":{"kind":"sequence","seed":18446744073709551615,"sequence":[18446744073709551615]}}]}]')
assert.equal(events[0].args,18446744073709551615n)
assert.equal(events[0].sources[0].generator.seed,18446744073709551615n)
assert.equal(syncSessionMatches({application_name:'A',application_id:1},{application_name:'A',application_id:1}),true)
assert.equal(syncSessionMatches({application_name:'B',application_id:1},{application_name:'A',application_id:1}),false)
assert.equal(syncSessionMatches({application_name:'A',application_id:2},{application_name:'A',application_id:1}),false)
assert.equal(syncSessionMatches(undefined,undefined),false)
assert.equal(syncActions(null,true).prepare,false)
assert.equal(syncActions({active:false,paused:true},true).prepare,true)
assert.equal(syncActions({active:true,paused:true},true).step,true)
assert.equal(syncActions({active:true,paused:false},true).step,false)
assert.equal(syncActions({active:true,paused:false},true).pause,true)
assert.equal(syncActions({active:true,paused:true},false).resume,false)
const calls=[]
globalThis.window={setTimeout,clearTimeout}
globalThis.fetch=async (url,options)=>{
  calls.push({url,...options})
  return new Response('{"active":true,"frame_index":18446744073709551615,"logical_ms":18446744073709551615}',{status:200,headers:{'Content-Type':'application/json'}})
}
await api.serviceSync('id/one')
const status=await api.startServiceSync('id/one',{paused:true,speed:0.5,events})
await api.controlServiceSync('id/one',{action:'step'})
assert.equal(calls.length,3)
assert.equal(calls[0].url,'/api/v1/services/sessions/id%2Fone/sync')
assert.equal(calls[1].url,'/api/v1/services/sessions/id%2Fone/sync/start')
assert.equal(calls[2].url,'/api/v1/services/sessions/id%2Fone/sync/control')
assert.match(calls[1].body,/"seed":18446744073709551615/)
assert.match(calls[1].body,/"paused":true/)
assert.equal(status.frame_index,18446744073709551615n)
assert.equal(status.logical_ms,18446744073709551615n)
console.log('公共时钟草案／运行分离、精确整数、会话身份、状态按钮与原生 API 往返通过；SSR 不冒充浏览器交互')
