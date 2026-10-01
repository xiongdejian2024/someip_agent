import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

// 复用已有 esbuild，验收页面实际使用的决策函数；不冒充浏览器点击或真实回滚。
const require = createRequire(new URL('../frontend/package.json', import.meta.url))
try {
  const { build } = require('esbuild')
  const bundle = await build({
    entryPoints: [fileURLToPath(new URL('../frontend/src/api/updateOutcome.ts', import.meta.url))],
    bundle: true, write: false, platform: 'node', format: 'cjs',
  })
  const module = { exports: {} }
  new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(require, module, module.exports)
  const { updateOutcome } = module.exports
  const id = 'a'.repeat(32)
  const prepared = { installation_id: id, version: '0.2.0', status: 'prepared', rollback_completed: false, rollback_failed: false, restored_version: null, error: null }
  const health = { status: 'ok', version: '0.2.0' }
  const decide = (record, info = health) => updateOutcome(record, id, '0.2.0', info)
  assert.equal(decide(prepared).status, 'pending', '新版本可达但升级器未确认，不能提前宣布成功')
  assert.equal(decide({ ...prepared, status: 'complete' }).status, 'complete')
  assert.equal(decide({ ...prepared, status: 'complete' }, { ...health, version: '0.1.0' }).status, 'pending')
  assert.equal(decide({ ...prepared, status: 'complete', installation_id: 'b'.repeat(32) }).status, 'pending')
  assert.equal(decide({ ...prepared, status: 'complete', version: '0.3.0' }).status, 'pending')
  assert.equal(decide({ ...prepared, status: 'failed', rollback_pid: 123 }).status, 'failed', 'PID 不是恢复健康证明')
  const restored = { ...prepared, status: 'failed', rollback_completed: true, restored_version: '0.1.0', error: 'RuntimeError: 新程序退出' }
  assert.equal(decide(restored).status, 'pending', '健康版本必须与已恢复版本一致')
  assert.equal(decide(restored, { status: 'error', version: '0.1.0' }).status, 'pending')
  const rollback = decide(restored, { status: 'ok', version: '0.1.0' })
  assert.equal(rollback.status, 'rolled_back')
  assert.ok(rollback.text.includes('已回滚至 v0.1.0'))
  assert.ok(rollback.text.includes('新程序退出'))
  assert.equal(decide({ ...restored, rollback_failed: true }, { status: 'ok', version: '0.1.0' }).status, 'failed')
  console.info('升级结果决策回归通过：本次安装关联、健康版本、完成及回滚失败门禁；不代表真实浏览器验收。')
} catch (error) {
  console.error('升级结果决策回归失败，完整异常堆栈：', error)
  process.exitCode = 1
}
