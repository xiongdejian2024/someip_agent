import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { build } = require('esbuild')
const output = await build({ stdin: { contents: `
  import {createElement} from 'react'
  import {renderToStaticMarkup} from 'react-dom/server'
  import {ProjectBar,emptyProject} from './src/workbench/projects'
  export {mergeSavedDraft,projectDraftChanged,projectContents,projectDate,projectEditorText} from './src/workbench/projectPresentation'
  export {projectResultUnknown} from './src/workbench/ProjectManager'
  export {ApiError} from './src/api/client'
  export const empty=emptyProject()
  export const saved={...empty,name:'已保存工程',cycles:[{service_profile:'/V',command:{member:'Provider_server',function:'UpdateValueEvent',args:{value:18446744073709551615n},interval_ms:29}}]}
  const current={id:'saved',revision:3,updated_at:'2026-10-03',document:saved}
  const noop=()=>{throw new Error('首次渲染不能保存、加载或运行')}
  export const clean=renderToStaticMarkup(createElement(ProjectBar,{current,controls:{document:saved,update:noop},apply:noop,onSaved:noop}))
  export const dirty=renderToStaticMarkup(createElement(ProjectBar,{current,controls:{document:{...saved,name:'未保存更改'},update:noop},apply:noop,onSaved:noop}))
  export const loading=renderToStaticMarkup(createElement(ProjectBar,{current:null,ready:false,controls:{document:empty,update:noop},apply:noop,onSaved:noop}))
`, resolveDir: fileURLToPath(new URL('../frontend/', import.meta.url)), loader: 'js' }, bundle: true, write: false,
  platform: 'node', format: 'cjs', jsx: 'automatic', loader: { '.css': 'empty' }, define: { 'process.env.NODE_ENV': '"production"' } })
const module = { exports: {} }
new Function('require','module','exports',output.outputFiles[0].text)(require,module,module.exports)
const {empty,saved,clean,dirty,loading,mergeSavedDraft,projectDraftChanged,projectContents,projectDate,projectResultUnknown,ApiError,projectEditorText}=module.exports
assert.match(clean,/草案与已保存修订一致/)
assert.match(dirty,/有未保存更改/)
assert.match(clean,/id="project-load" class="project-section" hidden=""/)
assert.match(clean,/id="project-management" class="project-section" hidden=""/)
assert.match(clean,/aria-label="搜索工程"/)
assert.match(clean,/aria-label="工程预览"/)
assert.match(clean,/不会代你停止任务/)
assert.match(loading,/disabled="">保存配置/)
assert.match(loading,/disabled="">加载工程/)
assert.equal(projectDraftChanged(saved,{...saved}),false)
assert.equal(projectDraftChanged(empty,saved),true)
assert.equal(projectDraftChanged(saved,{...saved,cycles:[]}),true)
const next={...saved,name:'保存返回的新修订'}
assert.equal(mergeSavedDraft(saved,saved,next),next)
const concurrent={...saved,name:'保存期间的新编辑'}
assert.equal(mergeSavedDraft(concurrent,saved,next),concurrent)
assert.equal(mergeSavedDraft(concurrent,saved,next).cycles[0].command.args.value,18446744073709551615n)
assert.equal(projectContents(saved).find(([label])=>label==='周期激励')[1],1)
assert.equal(projectDate('未知日期'),'未知日期')
const sharedModel={toJSON(){throw new Error('共享模型不得在每次输入时序列化')}}
const large={...saved,model:sharedModel}
assert.equal(projectDraftChanged(large,{...large}),false)
assert.equal(projectDraftChanged({...large,name:'修改名称'},large),true)
assert.equal(projectResultUnknown('保存工程',new ApiError('超时',0)),true)
assert.equal(projectResultUnknown('另存新工程',new ApiError('断网',0)),true)
assert.equal(projectResultUnknown('保存工程',new ApiError('修订冲突',409)),false)
assert.equal(projectResultUnknown('保存工程',new Error('本地输入错误')),false)
assert.equal(projectResultUnknown('读取工程备份',new ApiError('超时',0)),false)
assert.match(projectEditorText(saved),/18446744073709551615/)
assert.throws(()=>projectEditorText({...saved,model:{data:'中'.repeat(400000)}}),/1 MiB/)
assert.match(clean,/导出当前草案 JSON/)
console.log('工程管理分层入口、加载预览、无损草案变更与并发保存保护验证通过；SSR 不冒充浏览器交互')
