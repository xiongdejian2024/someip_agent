import { useEffect, useMemo, useRef, useState } from 'react'
import { api, ApiError, describeApiError } from '../api/client'
import { parseJson, stringifyJson } from '../api/json'
import { logError, logInfo } from '../api/logger'
import { emptyProject, type ProjectControls, type ProjectDocument, type ProjectSummary, type ProjectView } from './projects'
import { projectContents, projectDate, projectDraftChanged, projectEditorText } from './projectPresentation'
import './ProjectManager.css'

type Section = 'load' | 'details' | 'history' | 'transfer' | 'advanced' | null
type DraftSnapshot = { document: ProjectDocument; editor: string }
type Confirmation = { title: string; action: string; work: () => Promise<string>; snapshot: DraftSnapshot }

export function projectResultUnknown(action: string, error: unknown) {
  return error instanceof ApiError && error.status === 0 && ['保存工程', '另存新工程', '导入工程', '恢复工程备份', '加载工程'].includes(action)
}

export function ProjectStatus({ document, current, onOpen }: {
  document: ProjectDocument; current: ProjectView | null; onOpen: () => void
}) {
  const dirty = projectDraftChanged(document, current?.document ?? emptyProject())
  return <section className="project-status" aria-label="当前工程状态">
    <span><strong>{document.name}</strong><small>{current ? `修订 ${current.revision}` : '未打开工程'} · {dirty ? '有未保存更改' : current ? '已保存' : '配置草案'}</small></span>
    <button className="button secondary" onClick={onOpen}>前往工程保存与加载</button>
  </section>
}

export function ProjectBar({ controls, current, apply, onSaved, ready = true, visible = true }: {
  controls: ProjectControls; current: ProjectView | null
  apply: (view: ProjectView) => Promise<void>
  onSaved: (view: ProjectView, requested: ProjectDocument) => void
  ready?: boolean
  visible?: boolean
}) {
  const [section, setSection] = useState<Section>('details')
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [listError, setListError] = useState('')
  const [selected, setSelected] = useState('')
  const [query, setQuery] = useState('')
  const [preview, setPreview] = useState<ProjectView | null>(null)
  const [previewError, setPreviewError] = useState('')
  const [previewRetry, setPreviewRetry] = useState(0)
  const [working, setBusy] = useState(false)
  const busy = working || !ready
  const lock = useRef(false)
  const [notice, setNotice] = useState<{ error: boolean; text: string } | null>(null)
  const [savedCopy, setSavedCopy] = useState<ProjectView | null>(null)
  const [uncertain, setUncertain] = useState(false)
  const [editor, setEditor] = useState('')
  const [editorBase, setEditorBase] = useState('')
  const [backups, setBackups] = useState<Array<{ revision: number; updated_at: string }>>([])
  const [backup, setBackup] = useState('')
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null)
  const editorDirty = editor !== editorBase
  const dirty = useMemo(() => projectDraftChanged(controls.document, current?.document ?? emptyProject()), [controls.document, current?.document])
  const unsaved = dirty || editorDirty
  const snapshot = useMemo(() => ({ document: controls.document, editor }), [controls.document, editor])
  const latestSnapshot = useRef(snapshot)
  latestSnapshot.current = snapshot
  // 历史恢复已持久化、尚未加载时，不能拿旧 revision 再保存当前工程。
  const needsReload = savedCopy?.id === current?.id && savedCopy?.revision !== current?.revision
  const validName = !!controls.document.name.trim()

  const refresh = async () => {
    try { setProjects(await api.projects()); setListError('') }
    catch (error) { logError('读取工程列表失败', error); setListError(describeApiError(error)) }
  }
  useEffect(() => { void refresh() }, [])
  useEffect(() => {
    let active = true
    setPreview(null); setPreviewError('')
    if (selected) void api.project(selected).then(value => { if (active) setPreview(value) })
      .catch(error => { logError('读取工程预览失败', error); if (active) setPreviewError(describeApiError(error)) })
    return () => { active = false }
  }, [selected, previewRetry])
  useEffect(() => { setBackups([]); setBackup('') }, [current?.id, current?.revision])
  useEffect(() => {
    if (!unsaved) return
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [unsaved])

  const run = async (action: string, work: () => Promise<string>) => {
    if (lock.current || !ready) return
    lock.current = true; setBusy(true); setNotice(null); setConfirmation(null)
    try {
      const text = await work()
      logInfo(action); setNotice({ error: false, text })
      // 写入成功和列表刷新失败分别报告，不能把已成功的保存误报为失败。
      await refresh()
    } catch (error) {
      logError(action, error)
      const unknown = projectResultUnknown(action, error)
      if (unknown) setUncertain(true)
      setNotice({ error: true, text: unknown
        ? `${action}结果未确认：${describeApiError(error)}。后端可能已完成；请刷新列表、核对修订并明确加载，不要重复提交。`
        : `${action}失败：${describeApiError(error)}` })
    } finally { lock.current = false; setBusy(false) }
  }
  const guard = (title: string, action: string, work: () => Promise<string>, always = false) => {
    if (always || unsaved) setConfirmation({ title, action, work, snapshot })
    else void run(action, work)
  }
  const load = (id: string) => guard('加载会替换工作区草案；未保存更改不会保留。', '加载工程', async () => {
    const view = await api.openProject(id)
    await apply(view)
    setSavedCopy(null); setUncertain(false); setEditor(''); setEditorBase(''); setSection('load')
    return `已加载「${view.document.name}」修订 ${view.revision}，已设为重启恢复目标；未启动任何任务。`
  })
  const save = (asNew: boolean) => void run(asNew ? '另存新工程' : '保存工程', async () => {
    const requested = controls.document
    // 模型在首次载入和 ARXML 导入时同步进草案；保存完整草案，不能悄悄丢弃 JSON 中的模型编辑。
    const saved = await api.saveProject(requested, asNew ? undefined : current ?? undefined)
    if (current && !asNew) {
      onSaved(saved, requested)
      if (selected === saved.id) setPreview(saved)
      return `已保存「${saved.document.name}」修订 ${saved.revision}；请求期间的新编辑会保留，未启动任务。`
    }
    setSavedCopy(saved); setSelected(saved.id); setPreview(saved); setQuery(''); setSection('load')
    return `已创建「${saved.document.name}」；尚未切换工程。点击“打开（不运行）”才设为重启恢复目标。`
  })
  const download = (config: ProjectDocument, id: string) => {
    const url = URL.createObjectURL(new Blob([stringifyJson(config, 2)], { type: 'application/json' }))
    const link = document.createElement('a')
    link.href = url; link.download = `project-${id}.json`; link.click(); URL.revokeObjectURL(url)
  }
  const toggle = (next: Exclude<Section, null>) => { setSection(next); setConfirmation(null) }
  const items = projects.filter(item => item.name.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))

  return <section className="panel project-manager" aria-label="工程保存与加载" aria-busy={busy} hidden={!visible}>
    <div className="project-toolbar">
      <div className="project-identity"><span className="project-eyebrow">当前工作区</span><strong>{current?.document.name ?? '未打开工程'}</strong>
        <span className="project-meta">{current ? `修订 ${current.revision}` : '配置草案'} · {needsReload ? '已保存版本已变化，等待重新加载' : unsaved ? '有未保存更改' : current ? '草案与已保存修订一致' : '尚未持久化'}</span>
      </div>
      <div className="context-actions">
        <button className="button primary" disabled={busy || !validName || editorDirty || needsReload || uncertain} onClick={() => save(false)}>保存配置</button>
        <button className="button secondary" aria-expanded={section === 'load'} aria-controls="project-load" disabled={busy} onClick={() => toggle('load')}>加载工程</button>
      </div>
    </div>
    <p className="project-safety">仅保存与加载配置，不自动发包。加载前须停止并释放服务、仿真和监听；不会代你停止任务。</p>
    <nav className="project-tabs project-page-tabs" aria-label="工程管理分区">{([['details', '保存与另存'], ['load', '加载工程'], ['history', '历史备份'], ['transfer', '导入 / 导出'], ['advanced', '高级配置']] as const).map(([key, label]) => <button key={key} type="button" aria-pressed={section === key} disabled={busy} onClick={() => toggle(key)}>{label}</button>)}</nav>
    {notice && <p className={`project-notice${notice.error ? ' error' : ''}`} role={notice.error ? 'alert' : 'status'}>{notice.text}</p>}
    {savedCopy && <div className="project-copy-notice"><span>已保存「{savedCopy.document.name}」修订 {savedCopy.revision}，等待明确加载。</span><button className="button secondary" disabled={busy} onClick={() => load(savedCopy.id)}>加载刚保存的工程（不运行）</button></div>}
    {confirmation && <div className="project-confirmation" role="group" aria-label="确认替换工作区草案">
      <strong>{confirmation.title}</strong><p>请先返回保存或另存需要保留的更改；确认后才会执行操作。{editorDirty && 'JSON 编辑器还有尚未校验载入的修改。'}</p>
      {confirmation.snapshot !== snapshot && <p role="alert">草案已再次变更，请返回后重新发起操作。</p>}
      <div className="context-actions"><button className="button secondary" disabled={busy} onClick={() => setConfirmation(null)}>返回，保留草案</button>
        <button className="button primary" disabled={busy || confirmation.snapshot !== snapshot} onClick={() => {
          if (confirmation.snapshot === latestSnapshot.current) void run(confirmation.action, confirmation.work)
        }}>确认继续（不运行）</button></div>
    </div>}
    <div id="project-load" className="project-section" hidden={section !== 'load'}>
      <div className="project-section-heading"><h3>加载已保存工程</h3><button className="button secondary" disabled={busy} onClick={() => void run('刷新工程列表', async () => { setPreviewRetry(value => value + 1); return '正在刷新列表与选中工程预览；读取失败会单独提示。' })}>刷新列表</button></div>
      <div className="project-load-grid">
        <div><label className="project-field">搜索工程<input aria-label="搜索工程" value={query} onChange={event => {
          const value = event.target.value
          setQuery(value)
          if (!projects.find(item => item.id === selected)?.name.toLocaleLowerCase().includes(value.trim().toLocaleLowerCase())) setSelected('')
        }} placeholder="按工程名称搜索" disabled={busy} /></label>
          {listError && <p role="alert">工程列表读取失败：{listError}；保留此前结果，请刷新重试。</p>}
          <div className="project-list" role="group" aria-label="选择工程">{items.map(item => <button key={item.id} type="button" className={`project-list-item${selected === item.id ? ' selected' : ''}`} aria-pressed={selected === item.id} disabled={busy} onClick={() => setSelected(item.id)}>
            <strong>{item.name}</strong><span>修订 {item.revision} · {projectDate(item.updated_at)} · {item.id.slice(0, 8)}{item.id === current?.id ? ' · 当前' : ''}</span>
          </button>)}{!items.length && !listError && <p className="muted">{query ? '没有匹配的工程。' : '还没有已保存工程。可先保存当前草案，或在“导入 / 导出”中导入。'}</p>}</div>
        </div>
        <div className="project-preview" aria-label="工程预览">
          {preview?.id === selected ? <><h4>{preview.document.name}</h4><p className="muted">修订 {preview.revision} · {projectDate(preview.updated_at)} · {preview.id.slice(0, 8)}</p><p>{preview.document.description || '未填写工程说明'}</p>
            <dl className="project-statistics">{projectContents(preview.document).map(([label, count]) => <div key={label}><dt>{label}</dt><dd>{count}</dd></div>)}</dl>
            <p className="muted">{preview.document.model ? '含模型投影' : '不含模型'} · 仅配置，不含运行进程、密钥或发送授权。</p>
          </> : <p role={previewError ? 'alert' : 'status'}>{previewError ? `工程预览失败：${previewError}。请重新选择或刷新。` : selected ? '正在读取工程预览…' : '选择一个工程，先检查内容再加载。'}</p>}
          <button className="button primary" disabled={busy || !selected || preview?.id !== selected} onClick={() => load(selected)}>打开（不运行）</button>
        </div>
      </div>
    </div>
    <div id="project-management" className="project-section" hidden={section === null || section === 'load'}>
      <div hidden={section !== 'details'}>
        <div className="project-details-grid"><label className="project-field">工程名称<input aria-label="工程名称" value={controls.document.name} maxLength={128} disabled={busy} onChange={event => controls.update(doc => ({ ...doc, name: event.target.value }))} /></label>
          <label className="project-field">工程说明<input aria-label="工程说明" value={controls.document.description} maxLength={2000} disabled={busy} onChange={event => controls.update(doc => ({ ...doc, description: event.target.value }))} /></label></div>
        {!validName && <p role="alert">请填写工程名称后再保存。</p>}
        <p className="muted">保存更新当前工程；另存创建独立工程，不覆盖原工程，也不自动切换工作区。</p>
        <button className="button secondary" disabled={busy || !validName || editorDirty || uncertain} onClick={() => save(true)}>另存新工程</button>
      </div>
      <div hidden={section !== 'history'}>
        <h3>历史备份</h3><p className="muted">恢复会生成新修订，保留原历史；不会立即替换工作区。随后明确加载才恢复页面和模型。</p>
        <div className="context-actions"><button className="button secondary" disabled={busy || !current} onClick={() => void run('读取工程备份', async () => { if (current) setBackups(await api.projectBackups(current.id)); return '历史备份已读取。' })}>读取历史备份</button>
          <select aria-label="选择备份" value={backup} disabled={busy || needsReload} onChange={event => setBackup(event.target.value)}><option value="">选择旧修订</option>{backups.map(item => <option key={item.revision} value={item.revision}>修订 {item.revision} · {projectDate(item.updated_at)}</option>)}</select>
          <button className="button secondary" disabled={busy || !backup || !current || needsReload || uncertain} onClick={() => guard('恢复旧修订将生成新的已保存版本；当前草案暂不替换。', '恢复工程备份', async () => {
            if (!current) throw new Error('没有已打开工程')
            const saved = await api.restoreProject(current.id, Number(backup), current.revision)
            setSavedCopy(saved); setSelected(saved.id); setPreview(saved); setQuery(''); setBackups([]); setBackup('')
            return `已恢复为修订 ${saved.revision}，当前草案保留；请明确加载新修订后再保存当前工程。`
          }, true)}>恢复为新修订（不运行）</button></div>
      </div>
      <div hidden={section !== 'transfer'}>
        <h3>导入 / 导出</h3><p className="muted">导出当前工程的已保存版本，不含未保存草案；导入创建新工程，不覆盖当前工程、不启动任务。</p>
        <button className="button secondary" disabled={busy || !current} onClick={() => void run('导出工程', async () => { if (current) download(await api.exportProject(current.id), current.id); return '已导出已保存修订；未保存草案不在文件中。' })}>导出已保存工程</button>
        <label className="project-field">导入工程 JSON<input aria-label="导入工程 JSON" type="file" accept=".json,application/json" disabled={busy || uncertain} onChange={event => {
          const file = event.target.files?.[0]; event.target.value = ''
          if (file) void run('导入工程', async () => {
            if (file.size > 8 * 1024 * 1024) throw new Error('工程超过 8 MiB 上限')
            const saved = await api.importProject(parseJson(await file.text()))
            setSavedCopy(saved); setSelected(saved.id); setPreview(saved); setQuery(''); setSection('load')
            return `已导入「${saved.document.name}」，等待明确加载；当前工程未被覆盖。`
          })
        }} /></label>
      </div>
      <div hidden={section !== 'advanced'}>
        <h3>高级配置草案</h3><p className="muted">JSON 修改须先校验载入草案，再单独保存；模型编辑也只在明确加载工程后激活。不会执行脚本或启动任务。</p>
        <p className="muted">内联编辑上限 1 MiB；大型工程请导出草案后外部编辑，再从“导入 / 导出”导入新工程。工程文件上限仍为 8 MiB。</p>
        <div className="context-actions"><button className="button secondary" disabled={busy || editorDirty} onClick={() => {
          try { const text = projectEditorText(controls.document); setEditor(text); setEditorBase(text) }
          catch (error) { logError('打开工程 JSON 编辑器失败', error); setNotice({ error: true, text: describeApiError(error) }) }
        }}>查看/编辑配置草案</button>
          <button className="button secondary" disabled={busy} onClick={() => void run('导出当前草案', async () => { download(controls.document, 'draft'); return '已导出当前草案，包含未保存的配置；未持久化工程、未启动任务。' })}>导出当前草案 JSON</button></div>
        {editor && <><textarea aria-label="工程配置草案" value={editor} disabled={busy} onChange={event => setEditor(event.target.value)} rows={12} spellCheck={false} />
          <div className="context-actions"><button className="button secondary" disabled={busy} onClick={() => guard('校验通过后将替换当前草案；尚未保存。', '校验配置草案', async () => {
            const validated = await api.validateProject(parseJson(editor))
            controls.update(() => validated); const text = stringifyJson(validated, 2); setEditor(text); setEditorBase(text)
            return '已校验并载入配置草案；尚未保存、未启动任务。'
          })}>校验并载入草案（不启动）</button><button className="button secondary" disabled={busy} onClick={() => guard('放弃 JSON 编辑器中的修改；已载入工作区的草案不变。', '放弃 JSON 编辑', async () => { setEditor(''); setEditorBase(''); return 'JSON 编辑已放弃，工作区草案保留。' })}>关闭 JSON 编辑器</button></div></>}
      </div>
    </div>
  </section>
}
