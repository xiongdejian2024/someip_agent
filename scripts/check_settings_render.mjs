import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

// 复用既有 Vite/esbuild/React 工具链，只验收真实组件的首次渲染。
// SSR 不执行 useEffect；不能据此声称浏览器点击、HTTP 安装或重启升级通过。
const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { build } = require('esbuild')

try {
  const result = await build({
    stdin: {
      contents: `
        import { createElement } from 'react'
        import { renderToStaticMarkup } from 'react-dom/server'
        import { SettingsPage } from './src/pages/SettingsPage'
        export const html = renderToStaticMarkup(createElement(SettingsPage, { currentVersion: '0.1.0' }))
      `,
      resolveDir: fileURLToPath(new URL('../frontend/', import.meta.url)),
      loader: 'js',
    },
    bundle: true,
    write: false,
    platform: 'node',
    format: 'cjs',
    jsx: 'automatic',
    define: { 'process.env.NODE_ENV': '"production"' },
  })
  const rendered = { exports: {} }
  new Function('require', 'module', 'exports', result.outputFiles[0].text)(
    require, rendered, rendered.exports,
  )
  const { html } = rendered.exports
  assert.ok(html.includes('Linux（当前验收目标）'), '设置页必须明确当前 Linux 发行目标')
  assert.ok(!html.includes('Windows x64'), '暂缓 Windows 时不能把它写为当前发行目标')
  assert.ok(html.includes('v0.1.0'), '设置页应显示实际传入的应用版本')
  assert.ok(html.includes('检查更新'), '首次渲染应提供检查更新入口')
  assert.ok(!html.includes('升级到最新版本'), '尚未检查和验签时不能展示安装按钮')
  console.info('设置页首次渲染验证通过：Linux 目标、版本、检查入口与缺省升级门禁；不代表浏览器升级验收。')
} catch (error) {
  console.error('设置页首次渲染验证失败，完整异常堆栈：', error)
  process.exitCode = 1
}
