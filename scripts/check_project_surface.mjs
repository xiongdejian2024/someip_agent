import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { build } = require('esbuild')
const result = await build({
  stdin: { contents: `
    import { createElement } from 'react'
    import { renderToStaticMarkup } from 'react-dom/server'
    import { ProjectBar, ProjectContext, emptyProject } from './src/workbench/projects'
    import { ServiceRuntimePanel } from './src/components/ServiceRuntimePanel'
    import { SignalScope } from './src/components/SignalScope'
    const document = { ...emptyProject(), name: '复合事件工程',
      services: {'/Vehicle': {application_name:'saved_app', application_id:0x3402,
        members:{Vehicle:{service:'/Vehicle',deployment_path:'/Deployment',role:'server',transport:'internal',instance_id:7}}}},
      cycles:[{service_profile:'/Vehicle',command:{member:'Vehicle_server',function:'UpdateStatusEvent',args:{counter:18446744073709551615n},interval_ms:29}}],
      workspace:{page:'services',service_paths:['/Vehicle'],waves:[{service_id:0x1234,method_id:0x8001,signal_name:'value'}]}}
    const controls = {document, update: () => {throw new Error('首次渲染不应写工程或运行')}}
    const current = {id:'example',revision:7,updated_at:'2026-10-03',document}
    export const bar = renderToStaticMarkup(createElement(ProjectBar,{controls,current,apply:async()=>{},onSaved:()=>{}}))
    export const service = renderToStaticMarkup(createElement(ProjectContext.Provider,{value:controls},
      createElement(ServiceRuntimePanel,{demo:false,selected:{name:'Vehicle',id:'1',path:'/Vehicle',deploymentPath:'/Deployment',serviceId:'0x1234',instanceId:'7',instanceIds:[7],methods:[],events:[],fields:[]}})))
    export const wave = renderToStaticMarkup(createElement(ProjectContext.Provider,{value:controls},
      createElement(SignalScope,{samples:[{time:1,values:{'0x1234/0x8001/value':42,'0x1234/0x8001/other':3}}]})))
  `, resolveDir: fileURLToPath(new URL('../frontend/', import.meta.url)), loader: 'js' },
  bundle: true, write: false, platform: 'node', format: 'cjs', jsx: 'automatic', loader: { '.css': 'empty' },
  define: { 'process.env.NODE_ENV': '"production"' },
})
const module = { exports: {} }
new Function('require', 'module', 'exports', result.outputFiles[0].text)(require, module, module.exports)
assert.match(module.exports.bar, /复合事件工程/)
assert.match(module.exports.bar, /修订 7/)
assert.match(module.exports.bar, /另存新工程/)
assert.match(module.exports.bar, /打开（不运行）/)
assert.match(module.exports.bar, /导入工程 JSON/)
assert.match(module.exports.bar, /恢复为新修订（不运行）/)
assert.match(module.exports.service, /明确启动此配置/)
assert.match(module.exports.service, /value="13314"/)
assert.match(module.exports.service, /value="7"/)
assert.match(module.exports.wave, /1 \/ 6 路/)
console.info('工程管理、服务配置与波形选择 SSR 验证通过：恢复仅加载配置，不运行任务；不冒充浏览器交互。')
