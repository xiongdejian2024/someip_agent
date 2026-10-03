import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { api, describeApiError } from '../api/client'
import { parseJson, stringifyJson } from '../api/json'
import { logError, logInfo } from '../api/logger'
import type { NativeServiceCommand, NativeServiceRequest, NetworkListenerConfig, PageId, SimulationStartRequest } from '../types'

export interface ProjectDocument {
  format: 'someip-agent-project'
  format_version: 2
  name: string
  description: string
  model: unknown | null
  services: Record<string, NativeServiceRequest>
  listeners: NetworkListenerConfig[]
  simulations: SimulationStartRequest[]
  cycles: Array<{ service_profile: string; command: NativeServiceCommand & { interval_ms: number } }>
  workspace: { page: PageId; service_paths: string[]; waves: Array<{ service_id: number; method_id: number; signal_name: string }> }
}
export interface ProjectView { id: string; revision: number; updated_at: string; document: ProjectDocument }
export interface ProjectSummary { id: string; revision: number; updated_at: string; name: string }
export interface ProjectControls {
  document: ProjectDocument
  update: (change: (current: ProjectDocument) => ProjectDocument) => void
}

export function emptyProject(): ProjectDocument {
  return { format: 'someip-agent-project', format_version: 2, name: '未命名工程', description: '', model: null,
    services: {}, listeners: [], simulations: [], cycles: [], workspace: { page: 'dashboard', service_paths: [], waves: [] } }
}

export const ProjectContext = createContext<ProjectControls | null>(null)
export function useProject() { return useContext(ProjectContext) }

export function useProjectState(onRestore: (view: ProjectView) => Promise<void>) {
  const [view, setView] = useState<ProjectView | null>(null)
  const [document, setDocument] = useState<ProjectDocument>(emptyProject)
  const [epoch, setEpoch] = useState(0)
  const [ready, setReady] = useState(false)
  const update = useCallback((change: (current: ProjectDocument) => ProjectDocument) => {
    setDocument(current => {
      const next = change(current)
      return stringifyJson(next) === stringifyJson(current) ? current : next
    })
  }, [])
  const apply = useCallback(async (next: ProjectView) => {
    await onRestore(next)
    setView(next); setDocument(next.document); setEpoch(current => current + 1)
    logInfo('工程配置已载入，未启动运行任务', { projectId: next.id, revision: next.revision })
  }, [onRestore])
  useEffect(() => {
    let active = true
    void api.currentProject().then(async next => {
      if (active && next) await apply(next)
    }).catch(error => logError('恢复当前工程配置失败', error)).finally(() => { if (active) setReady(true) })
    return () => { active = false }
  }, [apply])
  return { view, document, update, apply, epoch, ready, setView }
}

export function ProjectBar({ controls, current, apply, onSaved }: {
  controls: ProjectControls; current: ProjectView | null
  apply: (view: ProjectView) => Promise<void>; onSaved: (view: ProjectView) => void
}) {
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [selected, setSelected] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [editor, setEditor] = useState('')
  const [backups, setBackups] = useState<Array<{ revision: number; updated_at: string }>>([])
  const [backup, setBackup] = useState('')
  const refresh = async () => setProjects(await api.projects())
  const run = async (action: string, work: () => Promise<void>) => {
    setBusy(true); setNotice('')
    try { await work(); await refresh(); logInfo(action); setNotice(`${action}完成；配置恢复不会启动任务。`) }
    catch (error) { logError(action, error); setNotice(`${action}失败：${describeApiError(error)}`) }
    finally { setBusy(false) }
  }
  useEffect(() => { void refresh().catch(error => logError('读取工程列表失败', error)) }, [])

  const save = (asNew: boolean) => run(asNew ? '新工程保存' : '工程保存', async () => {
    const model = await api.projectModel()
    const document = { ...controls.document, model }
    const saved = await api.saveProject(document, asNew ? undefined : current ?? undefined)
    if (current && !asNew) onSaved(saved)
    if (asNew || !current) {
      // 新工程保存成功后仍需明确打开；活动任务会使打开返回冲突，已保存的工程不丢失。
      setSelected(saved.id)
    }
  })
  const download = (config: ProjectDocument, id: string) => {
    const url = URL.createObjectURL(new Blob([stringifyJson(config, 2)], { type: 'application/json' }))
    const link = document.createElement('a')
    link.href = url; link.download = `project-${id}.json`; link.click(); URL.revokeObjectURL(url)
  }

  return <details className="panel" style={{ marginBottom: 12 }}>
    <summary>工程：{current?.document.name ?? '未打开'} · {current ? `修订 ${current.revision}` : '配置草案'}（点击管理）</summary>
    <p className="muted">保存后请打开工程以设为重启恢复目标。恢复只加载草案，不自动发包；切换前先停止并释放服务、仿真和监听。</p>
    <div className="context-actions">
      <input aria-label="工程名称" value={controls.document.name} maxLength={128} onChange={event => controls.update(doc => ({ ...doc, name: event.target.value }))} />
      <button className="button secondary" disabled={busy} onClick={() => void save(false)}>保存配置</button>
      <button className="button secondary" disabled={busy} onClick={() => void save(true)}>另存新工程</button>
      <select aria-label="选择工程" value={selected} onChange={event => setSelected(event.target.value)}><option value="">请选择工程</option>{projects.map(item => <option key={item.id} value={item.id}>{item.name} · 修订 {item.revision}</option>)}</select>
      <button className="button primary" disabled={busy || !selected} onClick={() => void run('打开工程', async () => apply(await api.openProject(selected)))}>打开（不运行）</button>
      <button className="button secondary" disabled={busy} onClick={() => void run('刷新工程列表', refresh)}>刷新</button>
      <button className="button secondary" disabled={busy || !current} onClick={() => void run('导出工程', async () => { if (current) download(await api.exportProject(current.id), current.id) })}>导出已保存工程</button>
      <label>导入工程 JSON<input aria-label="导入工程 JSON" type="file" accept=".json,application/json" disabled={busy} onChange={event => {
        const file = event.target.files?.[0]; event.target.value = ''
        if (file) void run('导入工程', async () => {
          if (file.size > 8 * 1024 * 1024) throw new Error('工程超过 8 MiB 上限')
          const saved = await api.importProject(parseJson(await file.text()))
          setSelected(saved.id)
        })
      }} /></label>
    </div>
    <div className="context-actions">
      <button className="button secondary" disabled={busy || !current} onClick={() => void run('读取工程备份', async () => { if (current) setBackups(await api.projectBackups(current.id)) })}>历史备份</button>
      <select aria-label="选择备份" value={backup} onChange={event => setBackup(event.target.value)}><option value="">选择旧修订</option>{backups.map(item => <option key={item.revision} value={item.revision}>{item.revision} · {item.updated_at}</option>)}</select>
      <button className="button secondary" disabled={busy || !backup || !current} onClick={() => void run('恢复工程备份', async () => {
        if (!current) return
        const saved = await api.restoreProject(current.id, Number(backup), current.revision)
        onSaved(saved); setSelected(saved.id); setBackups([]); setBackup('')
      })}>恢复为新修订（不运行）</button>
      <button className="button secondary" onClick={() => setEditor(stringifyJson(controls.document, 2))}>查看/编辑配置草案</button>
    </div>
    {editor && <><textarea aria-label="工程配置草案" value={editor} onChange={event => setEditor(event.target.value)} rows={12} style={{ width: '100%' }} /><button className="button secondary" disabled={busy} onClick={() => void run('校验配置草案', async () => {
      const value = parseJson(editor)
      const validated = await api.validateProject(value)
      controls.update(() => validated)
    })}>校验并载入草案（不启动）</button></>}
    {notice && <p role="status">{notice}</p>}
  </details>
}
