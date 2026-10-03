import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { readFile } from 'node:fs/promises'

const require=createRequire(new URL('../frontend/package.json',import.meta.url))
const {build}=require('esbuild')
const output=await build({stdin:{contents:`
  import {createElement} from 'react'
  import {renderToStaticMarkup} from 'react-dom/server'
  import {ServicesPage} from './src/pages/ServicesPage'
  import {SimulationPage} from './src/pages/SimulationPage'
  import {ServiceJsonInput} from './src/components/ServiceJsonInput'
  const service={id:'1',name:'复合服务',path:'/Composite',deploymentPath:'/Deployment',serviceId:'0x1234',instanceId:'1',instanceIds:[1],
    methods:[],fields:[],events:[{id:'0x8001',name:'Envelope',dataType:'struct',signals:[]}]}
  const noop=()=>{throw new Error('只读渲染不能触发运行或写入')}
  export const model=renderToStaticMarkup(createElement(ServicesPage,{services:[service],loading:false,source:'live',onServicesChange:noop}))
  export const runtime=renderToStaticMarkup(createElement(ServicesPage,{services:[service],loading:false,source:'live',initialView:'runtime',onServicesChange:noop}))
  export const simulation=renderToStaticMarkup(createElement(SimulationPage,{services:[service],samples:[],onDismissDraft:noop,onOpenService:noop}))
  export const input=renderToStaticMarkup(createElement(ServiceJsonInput,{label:'参数',ariaLabel:'参数',value:'{"counter":18446744073709551615}',disabled:false,onChange:noop,onValidityChange:noop}))
  export const invalid=renderToStaticMarkup(createElement(ServiceJsonInput,{label:'绑定',ariaLabel:'绑定',array:true,value:'{}',disabled:false,onChange:noop,onValidityChange:noop}))
`,resolveDir:fileURLToPath(new URL('../frontend/',import.meta.url)),loader:'js'},bundle:true,write:false,platform:'node',format:'cjs',jsx:'automatic',loader:{'.css':'empty'},define:{'process.env.NODE_ENV':'"production"'}})
const module={exports:{}}
new Function('require','module','exports',output.outputFiles[0].text)(require,module,module.exports)
const {model,runtime,simulation,input,invalid}=module.exports
assert.match(model,/服务工作区视图/)
assert.match(model,/<div hidden=""><section class="panel service-runtime-panel"/)
assert.doesNotMatch(runtime,/<div hidden=""><section class="panel service-runtime-panel"/)
assert.match(runtime,/class="model-workspace" hidden=""/)
assert.match(runtime,/初始化会话/)
assert.match(runtime,/选择运行服务/)
assert.match(simulation,/完整服务仿真入口/)
assert.match(simulation,/打开 复合服务 的完整服务入口/)
assert.match(simulation,/切换页面不会停止/)
assert.match(input,/18446744073709551615/)
assert.match(input,/aria-invalid="false"/)
assert.match(input,/格式化 JSON/)
assert.match(invalid,/aria-invalid="true"/)
assert.match(invalid,/role="alert"/)
assert.match(invalid,/disabled=""/)
assert.match(await readFile(new URL('../frontend/src/index.css',import.meta.url),'utf8'),/\[hidden\] \{ display: none !important; \}/)
console.log('工作台 UI 模型／运行分区、复合入口、无损 JSON 反馈与显式运行边界首次渲染通过；真实交互另行验收')
