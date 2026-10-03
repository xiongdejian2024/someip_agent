import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { build } = require('esbuild')
const output = await build({
  stdin: { contents: `
    import { createElement } from 'react'
    import { renderToStaticMarkup } from 'react-dom/server'
    import { GeneratorNumberInput } from './src/components/GeneratorNumberInput'
    import { SimulationPage } from './src/pages/SimulationPage'
    export * from './src/data/generatorValues'
    export * from './src/api/json'
    export { api } from './src/api/client'
    export const input = renderToStaticMarkup(createElement(GeneratorNumberInput, {
      label:'输出值', value:18446744073709551615n, dataType:'uint64', disabled:false,
      onChange:()=>{throw new Error('只读渲染不应修改配置')},
      onValidityChange:()=>{throw new Error('只读渲染不应运行副作用')}
    }))
    export const page = renderToStaticMarkup(createElement(SimulationPage, {
      services:[{id:'precision',name:'精确整数服务',serviceId:'0x1234',instanceId:'1',instanceIds:[1],methods:[],fields:[],
        events:[{id:'0x8001',name:'Counter',signals:[{name:'Counter',dataType:'uint64',minimum:9007199254740993n,maximum:18446744073709551615n}]}]}],
      samples:[], onDismissDraft:()=>{}
    }))
  `, resolveDir: fileURLToPath(new URL('../frontend/', import.meta.url)), loader: 'js' },
  bundle: true, write: false, platform: 'node', format: 'cjs', jsx: 'automatic', loader: { '.css':'empty' },
  define: { 'process.env.NODE_ENV': '"production"' },
})
const module = { exports: {} }
new Function('require', 'module', 'exports', output.outputFiles[0].text)(require, module, module.exports)
const { parseGeneratorValue, validateGeneratorValue, validateGeneratorRange, generatorMidpoint, generatorSliderSafe, parseJson, stringifyJson, api, input, page } = module.exports
for (const [literal, type, expected] of [
  ['9007199254740993', 'uint64', 9007199254740993n],
  ['18446744073709551615', 'uint64', 18446744073709551615n],
  ['-9223372036854775808', 'int64', -9223372036854775808n],
  ['255', 'uint8', 255], ['-128', 'int8', -128], ['1', 'boolean', 1],
  ['-1.25', 'float32', -1.25], ['1e-3', 'float64', 0.001],
]) assert.equal(parseGeneratorValue(literal, type), expected)
for (const [literal, type] of [
  ['', 'int64'], ['-', 'int64'], ['01', 'int64'], ['NaN', 'float64'], ['null', 'uint64'],
  ['"1"', 'int64'], ['true', 'boolean'], ['1.5', 'int64'], ['-1', 'uint64'],
  ['18446744073709551616', 'uint64'], ['-9223372036854775809', 'int64'],
  ['256', 'uint8'], ['128', 'int8'], ['2', 'boolean'], ['1e400', 'float64'],
  ['1e-400', 'float64'], ['1e30', 'uint64'], ['3.5e38', 'float32'],
]) assert.throws(() => parseGeneratorValue(literal, type), undefined, literal + ':' + type)
assert.throws(() => validateGeneratorValue(Number('9007199254740993'), 'uint64'), /大整数/)
assert.equal(generatorMidpoint(-9223372036854775808n, 9223372036854775807n, 'int64'), 0)
assert.equal(generatorMidpoint(9007199254740993n, 9007199254740994n, 'uint64'), 9007199254740994n)
assert.equal(generatorMidpoint(-10n, -9n, 'int64'), -9)
assert.equal(generatorMidpoint(9007199254740990, 9007199254740991, 'uint64'), 9007199254740991)
assert.equal(generatorSliderSafe(0, 100, 42), true)
assert.equal(generatorSliderSafe(0, 18446744073709551615n, 42), false)
assert.equal(generatorSliderSafe(0, 100, 9007199254740993n), false)
validateGeneratorRange('uint64', 'constant', 0, 18446744073709551615n, 9007199254740993n)
assert.throws(() => validateGeneratorRange('uint64', 'sine', 0, 18446744073709551615n, 42), /安全整数/)
assert.throws(() => validateGeneratorRange('uint8', 'constant', 10, 20, 9), /范围/)
assert.match(input, /type="text"/)
assert.match(input, /value="18446744073709551615"/)
assert.doesNotMatch(input, /type="number"|type="range"/)
assert.ok(page.includes('value="' + generatorMidpoint(9007199254740993n,18446744073709551615n,'uint64') + '"'))
assert.match(page, /9007199254740993/)
assert.match(page, /18446744073709551615/)

const originalFetch = globalThis.fetch
globalThis.window = { setTimeout, clearTimeout }
try {
  let submitted
  globalThis.fetch = async (_url, options) => {
    submitted = options.body
    return new Response('{"id":"precision-check","running":true}', { headers:{'Content-Type':'application/json'} })
  }
  const generator = {signal_name:'counter',kind:'constant',data_type:'uint64',minimum:0,
    maximum:18446744073709551615n,initial:parseGeneratorValue('9007199254740993','uint64'),period_seconds:5,sequence:[],seed:18446744073709551615n}
  const command = {name:'精确整数验收',service_id:0x1234,instance_id:1,method_id:0x8001,
    interface_version:1,interval_ms:20,transport:'internal',destination_host:'127.0.0.1',destination_port:30501,generator}
  await api.startSimulation(command)
  assert.equal(parseJson(submitted).generator.initial, 9007199254740993n)
  assert.equal(parseJson(submitted).generator.maximum, 18446744073709551615n)
  const restored = parseJson(stringifyJson({ simulations:[command] })).simulations[0]
  assert.deepEqual(restored.generator, generator)
  assert.equal(typeof restored.service_id, 'number')
} finally {
  globalThis.fetch = originalFetch
  delete globalThis.window
}
console.info('旧仿真整数验收通过：文本解析、类型边界、精确中值、滑块门禁、HTTP 与工程序列化；SSR 不冒充浏览器交互。')
