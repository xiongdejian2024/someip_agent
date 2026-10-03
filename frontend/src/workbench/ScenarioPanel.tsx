import { useEffect, useState } from 'react'
import { api, describeApiError } from '../api/client'
import { parseJson, stringifyJson } from '../api/json'
import { logError, logInfo } from '../api/logger'
import type { ProjectView } from './projects'

export interface ScenarioSummary {
  id: string; name: string; project_id: string; project_revision: number
  status: 'running' | 'passed' | 'failed' | 'cancelled' | 'interrupted'
  started_at: string; finished_at: string | null; recording_id: string | null
  request_id: string; cleanup_complete: boolean; step_count: number
}
export interface ScenarioRun extends Omit<ScenarioSummary, 'step_count'> {
  application_version: string; definition_sha256: string; model_source_sha256: string | null
  steps: Array<{ case: number; index: string; name: string; kind: string; status: 'passed' | 'failed'; duration_ms: number; result: unknown; error: string | null }>
  cleanup_errors: string[]; error: string | null
}

export function exampleScenario() {
  return { format: 'someip-agent-scenario', format_version: 1, name: '参数化精确断言示例',
    cases: [{ value: 42 }, { value: 24 }], steps: [
      { kind: 'assert', path: ['parameters', 'value'], expected: { $param: 'value' } },
    ], cleanup: [], timeout_seconds: 120 }
}

export function ScenarioPanel({ project }: { project: ProjectView | null }) {
  const [editor, setEditor] = useState(() => stringifyJson(exampleScenario(), 2))
  const [history, setHistory] = useState<ScenarioSummary[]>([])
  const [selected, setSelected] = useState('')
  const [run, setRun] = useState<ScenarioRun | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [pollRetry, setPollRetry] = useState(0)
  const refresh = async () => setHistory(await api.scenarioRuns())
  useEffect(() => { void refresh().catch(error => logError('读取产品测试历史失败', error)) }, [])
  useEffect(() => {
    if (!selected) { setRun(null); return }
    let active = true
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try {
        const result = await api.scenarioRun(selected)
        if (!active) return
        setRun(result)
        if (result.status === 'running') timer = setTimeout(() => { void poll() }, 1000)
        else await refresh()
      } catch (error) {
        if (active) { logError('读取场景结果失败，已停止轮询', error); setNotice(`读取失败：${describeApiError(error)}；可手动重试，不会重新启动场景。`) }
      }
    }
    setRun(null)
    void poll()
    return () => { active = false; clearTimeout(timer) }
  }, [selected, pollRetry])

  const action = async (name: string, work: () => Promise<void>) => {
    setBusy(true); setNotice('')
    try { await work(); logInfo(name); setNotice(`${name}完成。`) }
    catch (error) { logError(name, error); setNotice(`${name}失败：${describeApiError(error)}`) }
    finally { setBusy(false) }
  }
  const definition = () => {
    if (new TextEncoder().encode(editor).length > 2 * 1024 * 1024) throw new Error('场景定义超过 2 MiB')
    return parseJson(editor)
  }
  const downloadDefinition = () => {
    const url = URL.createObjectURL(new Blob([stringifyJson(definition(), 2)], { type: 'application/json' }))
    const link = document.createElement('a'); link.href = url; link.download = 'scenario.json'; link.click()
    URL.revokeObjectURL(url)
  }
  return <details className="panel" style={{ marginBottom: 12 }}>
    <summary>自动场景与产品测试历史（点击管理）</summary>
    <p className="muted">运行使用已保存工程快照：{project ? `${project.document.name} · 修订 ${project.revision}` : '请先保存并打开工程'}，不使用未保存草案。示例只验证参数，不发包；真实通信需配置动作、消费者等待和断言。</p>
    <textarea aria-label="场景定义 JSON" rows={12} style={{ width: '100%', fontFamily: 'monospace' }} value={editor} onChange={event => setEditor(event.target.value)} />
    <div className="context-actions">
      <button className="button secondary" disabled={busy} onClick={() => void action('场景校验（不运行）', async () => { setEditor(stringifyJson(await api.validateScenario(definition()), 2)) })}>校验（不运行）</button>
      <button className="button primary" disabled={busy || !project} onClick={() => void action('场景启动', async () => {
        if (!project) return
        const started = await api.startScenario(project.id, definition()); setSelected(started.id); await refresh()
      })}>明确运行已保存工程</button>
      <button className="button secondary" disabled={busy} onClick={() => void action('导出场景定义', async () => downloadDefinition())}>导出定义 JSON</button>
      <label>导入定义<input aria-label="导入场景定义 JSON" type="file" accept=".json,application/json" disabled={busy} onChange={event => {
        const file = event.target.files?.[0]; event.target.value = ''
        if (file) void action('导入场景定义（不运行）', async () => {
          if (file.size > 2 * 1024 * 1024) throw new Error('场景定义超过 2 MiB')
          setEditor(stringifyJson(await api.validateScenario(parseJson(await file.text())), 2))
        })
      }} /></label>
    </div>
    <div className="context-actions">
      <select aria-label="选择产品测试历史" value={selected} onChange={event => setSelected(event.target.value)}><option value="">选择历史运行</option>{history.map(item => <option key={item.id} value={item.id}>{item.name} · {item.status} · {item.started_at}</option>)}</select>
      <button className="button secondary" disabled={busy} onClick={() => void action('刷新测试历史', async () => { await refresh(); setPollRetry(value => value + 1) })}>刷新历史/结果</button>
      <button className="button secondary" disabled={busy || !run || run.status !== 'running'} onClick={() => void action('取消场景并清理', async () => { if (run) setRun(await api.cancelScenario(run.id)); await refresh(); setPollRetry(value => value + 1) })}>取消本次运行</button>
      {run && run.status !== 'running' && <>
        <a className="button secondary" href={api.scenarioArtifactUrl(run.id, 'junit')} download>下载 JUnit</a>
        <a className="button secondary" href={api.scenarioArtifactUrl(run.id, 'report')} target="_blank" rel="noreferrer">查看 HTML 报告</a>
        {run.recording_id && <a className="button secondary" href={api.recordingExportUrl(run.recording_id)} download>下载原始记录</a>}
      </>}
    </div>
    {notice && <p role="status">{notice}</p>}
    {run && <>
      <p>状态：{run.status} · 清理完成：{run.cleanup_complete ? '是' : '否'} · 请求：{run.request_id}</p>
      <p className="muted">运行 {run.id} · 程序 {run.application_version} · 工程修订 {run.project_revision} · 原始记录 {run.recording_id ?? '尚未建立'}</p>
      {run.error && <p role="alert">{run.error}</p>}
      {run.cleanup_errors.map((error, index) => <p role="alert" key={index}>{error}</p>)}
      <div style={{ maxHeight: 300, overflow: 'auto' }}><table><thead><tr><th>参数组/步骤</th><th>动作</th><th>结果</th><th>耗时</th></tr></thead><tbody>{run.steps.map(step => <tr key={`${step.case}:${step.index}`}><td>{step.case}/{step.index} {step.name}</td><td>{step.kind}</td><td>{step.status}{step.error && <small>{step.error}</small>}</td><td>{step.duration_ms.toFixed(1)} ms</td></tr>)}</tbody></table></div>
      <details><summary>完整结果 JSON（含精确整数）</summary><pre style={{ maxHeight: 400, overflow: 'auto' }}>{stringifyJson(run, 2)}</pre></details>
    </>}
  </details>
}
