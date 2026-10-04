import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { build } = require('esbuild')
const result = await build({ stdin: { contents: `
  import {createElement} from 'react'
  import {renderToStaticMarkup} from 'react-dom/server'
  import {NetworkEnvironmentPage,NetworkHostPanel} from './src/pages/NetworkEnvironmentPage'
  import {emptyProject} from './src/workbench/projects'
  export {api,ApiError} from './src/api/client'
  export {emptyNetworkForm,formFromProfile,profileFromForm,networkDraftDocument,networkWriteUnknown,networkWriteAllowed} from './src/workbench/networkEnvironment'
  export const project=emptyProject()
  const noop=()=>{throw new Error('静态渲染不得写入工程或系统')}
  export const initial=renderToStaticMarkup(createElement(NetworkEnvironmentPage,{visible:true,ready:true,controls:{document:project,update:noop}}))
  export const hidden=renderToStaticMarkup(createElement(NetworkEnvironmentPage,{visible:false,ready:false,controls:{document:project,update:noop}}))
  export const profile={name:'隔离配置',parent:'test0',interface:'test0.100',vlan_id:100,ipv4:'10.88.0.2/24',mtu:1400,sd_multicast:'224.224.224.245'}
  export const status={available:true,reason:'',write_enabled:true,allowed_parents:['test0'],native_unicast:'127.0.0.1',active_tasks:false,activity:{configuring:false,lifecycle_operations:0},
    interfaces:[{ifname:'test0',ifindex:2,mtu:1500,flags:['UP'],link_type:'ether',addr_info:[{family:'inet',local:'192.168.1.2',prefixlen:24}]}],routes:[{dst:'default',dev:'test0',gateway:'192.168.1.1'}],managed:[{id:'uuid',profile,status:'applied',routes:[]}]}
  export const host=renderToStaticMarkup(createElement(NetworkHostPanel,{status,busy:false,blocked:false,refresh:noop,confirmRecord:noop,resetBinding:noop}))
  export const blocked=renderToStaticMarkup(createElement(NetworkHostPanel,{status,busy:false,blocked:true,refresh:noop,confirmRecord:noop,resetBinding:noop}))
`, resolveDir: fileURLToPath(new URL('../frontend/', import.meta.url)), loader: 'js' }, bundle: true, write: false, platform: 'node', format: 'cjs', jsx: 'automatic', loader: { '.css': 'empty' }, define: { 'process.env.NODE_ENV': '"production"' } })
const module = { exports: {} }
new Function('require', 'module', 'exports', result.outputFiles[0].text)(require, module, module.exports)
const { api, ApiError, emptyNetworkForm, formFromProfile, profileFromForm, networkDraftDocument, networkWriteUnknown, networkWriteAllowed, project, profile, status, initial, hidden, host, blocked } = module.exports
assert.deepEqual(project.network_profiles, [])
assert.throws(() => profileFromForm(emptyNetworkForm()), /环境名称/)
assert.deepEqual(profileFromForm(formFromProfile(profile)), profile)
const form = formFromProfile(profile)
for (const change of [{ vlan: '1.5' }, { vlan: '4095' }, { mtu: '10' }]) assert.throws(() => profileFromForm({ ...form, ...change }), /整数/)
assert.throws(() => profileFromForm({ ...form, interface: form.parent }), /独立/)
assert.throws(() => profileFromForm({ ...form, ipv4: '10.88.0.2' }), /前缀/)
assert.throws(() => profileFromForm({ ...form, parent: 'test0;reboot' }), /父网卡/)
assert.deepEqual(profileFromForm({ ...form, mode: 'untagged' }), { ...profile, interface: 'test0', vlan_id: null, mtu: null })
const added = networkDraftDocument(project, profile, null)
assert.equal(project.network_profiles.length, 0)
assert.equal(added.network_profiles.length, 1)
assert.throws(() => networkDraftDocument(added, profile, null), /已有工程草案/)
assert.equal(networkDraftDocument(added, { ...profile, name: '修改' }, profile.interface).network_profiles[0].name, '修改')
for (const code of [0, 503, 409, 422]) assert.equal(networkWriteUnknown(new ApiError('需核对结果', code)), true)
assert.equal(networkWriteUnknown(new ApiError('主机未授权', 403)), false)
assert.equal(networkWriteUnknown(new Error('本地校验失败')), false)
assert.equal(networkWriteAllowed(status, 'test0'), true)
assert.equal(networkWriteAllowed(status, 'foreign0'), false)
for (const changed of [null, { ...status, available: false }, { ...status, write_enabled: false }, { ...status, active_tasks: true }, { ...status, activity: { configuring: true, lifecycle_operations: 0 } }, { ...status, activity: { configuring: false, lifecycle_operations: 1 } }]) assert.equal(networkWriteAllowed(changed, 'test0'), false)
assert.match(initial, /校验并载入工程草案（不应用）/)
assert.match(initial, /预检系统应用（只读）/)
assert.match(initial, /不显示虚构网卡/)
assert.doesNotMatch(initial, /value="10\.88\.0\.2/)
assert.match(hidden, /network-environment-page" hidden=""/)
assert.match(host, /192\.168\.1\.2\/24/)
assert.match(host, /记录状态不代表即时所有权验证/)
assert.match(host, /绑定未来原生任务地址/)
for (const action of ['绑定未来原生任务地址', '清理自有配置', '解除地址绑定']) assert.match(blocked, new RegExp(`disabled="">${action}`))
assert.match(blocked, />刷新实际环境（只读）/)

// 真实客户端序列化，替身 HTTP 仅验证传输，不当系统写入验收。
globalThis.window = { setTimeout, clearTimeout }
const requests = []
globalThis.fetch = async (url, init) => { requests.push({ url, init }); return new Response('{}', { headers: { 'Content-Type': 'application/json' } }) }
await api.networkEnvironment()
await api.planNetworkEnvironment(profile)
await api.applyNetworkEnvironment(profile, 'a'.repeat(64))
await api.bindNetworkEnvironment('uuid')
await api.bindNetworkEnvironment(null)
await api.removeNetworkEnvironment('uuid')
assert.equal(requests.length, 6)
assert.equal(requests[0].init.method, undefined)
assert.equal(requests[1].url, '/api/v1/network/environment/plan')
assert.deepEqual(JSON.parse(requests[1].init.body), profile)
for (const item of requests.slice(2)) assert.equal(JSON.parse(item.init.body).confirm, true)
assert.equal(JSON.parse(requests[4].init.body).managed_id, null)
assert.equal(requests[5].url, '/api/v1/network/environment/uuid/remove')
console.log('网卡草案、双区渲染、授权／活动门禁、未知写入锁与明确确认传输验证通过；不冒充浏览器点击或系统配置')
