import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { build } = require('esbuild')
const output = await build({
  stdin: { contents: `
    import { createElement } from 'react'
    import { renderToStaticMarkup } from 'react-dom/server'
    import { TimedStateInput } from './src/components/TimedStateInput'
    export * from './src/data/timedStates'
    export * from './src/api/json'
    export { api } from './src/api/client'
    export const input = renderToStaticMarkup(createElement(TimedStateInput, {
      initialState:'idle',states:[{name:'idle',value:18446744073709551615n}],dataType:'uint64',
      minimum:0,maximum:18446744073709551615n,disabled:false,onChange:()=>{throw new Error('只读渲染不得写配置')},
      onValidityChange:()=>{throw new Error('只读渲染不得运行副作用')}
    }))
  `, resolveDir: fileURLToPath(new URL('../frontend/', import.meta.url)), loader: 'js' },
  bundle:true,write:false,platform:'node',format:'cjs',jsx:'automatic',loader:{'.css':'empty'},
  define:{'process.env.NODE_ENV':'"production"'},
})
const module = {exports:{}}
new Function('require','module','exports',output.outputFiles[0].text)(require,module,module.exports)
const {parseTimedGraph,validateTimedGraph,parseJson,stringifyJson,api,input}=module.exports
const config={initial_state:'idle',states:[
  {name:'idle',value:18446744073709551615n,duration_ms:18446744073709551615n,next:'done'},
  {name:'done',value:9007199254740993n},
]}
const parsed=parseTimedGraph(stringifyJson(config),'uint64',0,18446744073709551615n)
assert.equal(parsed.states[0].duration_ms,18446744073709551615n)
assert.equal(parsed.states[1].value,9007199254740993n)
assert.equal(parseTimedGraph('{"initial_state":"x","states":[{"name":"x","value":true}]}','boolean',0,1).states[0].value,true)
for(const duration of [0,-1,true,1.5,18446744073709551616n]) {
  assert.throws(()=>validateTimedGraph({...config,states:[{...config.states[0],duration_ms:duration},config.states[1]]},'uint64',0,18446744073709551615n))
}
for(const graph of [
  {...config,initial_state:'missing'}, {...config,states:[]},
  {...config,states:[...config.states,{name:'unused',value:0}]},
  {...config,states:[config.states[0],{...config.states[1],duration_ms:1,next:'idle'}]},
  {...config,states:[config.states[0],{name:'done',value:0,duration_ms:1}]},
  {...config,states:[config.states[0],{name:'done',value:0,script:'exec'}]},
  {...config,states:[{name:'wrong name',value:0}]},
  {initial_state:'x',states:[{name:'x',value:1},{name:'x',value:2}]},
  {initial_state:'x',states:[{name:'x',value:true}]},
  {initial_state:'x',states:[{name:'x',value:'1'}]},
  {initial_state:'x',states:Array(129).fill({name:'x',value:1})},
]) assert.throws(()=>validateTimedGraph(graph,'uint64',0,18446744073709551615n))
assert.throws(()=>parseTimedGraph(' '.repeat(262145),'uint8',0,255),/256/)
assert.throws(()=>parseTimedGraph('{"initial_state":"x","states":[{"name":"x","value":1}]}','boolean',0,1),/布尔/)
assert.throws(()=>parseTimedGraph('{"initial_state":"x","states":[{"name":"x","value":101}]}','uint8',0,100),/范围/)
validateTimedGraph({initial_state:'prefix',states:[{name:'prefix',value:0,duration_ms:1,next:'x'},
  {name:'x',value:2,duration_ms:3,next:'x'}]},'uint8',0,255)
assert.match(input,/时间状态图（JSON）/)
assert.match(input,/18446744073709551615/)
assert.match(input,/不支持条件表达式/)
const originalFetch=globalThis.fetch
globalThis.window={setTimeout,clearTimeout}
try {
  let submitted
  globalThis.fetch=async (_url,options)=>{submitted=options.body;return new Response('{"running":true}',{headers:{'Content-Type':'application/json'}})}
  await api.startSimulation({generator:{kind:'state_machine',...parsed}})
  const restored=parseJson(submitted).generator
  assert.equal(restored.states[0].duration_ms,18446744073709551615n)
  assert.equal(restored.states[1].value,9007199254740993n)
  assert.deepEqual(parseJson(stringifyJson({simulations:[{generator:restored}]})).simulations[0].generator,restored)
} finally {globalThis.fetch=originalFetch;delete globalThis.window}
console.log('时间状态图无损整数、完整图校验、HTTP／工程往返与只读组件验收通过')
