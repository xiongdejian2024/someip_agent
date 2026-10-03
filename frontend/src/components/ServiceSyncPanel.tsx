import { useEffect, useRef, useState } from 'react'
import { api, ApiError, describeApiError } from '../api/client'
import { stringifyJson } from '../api/json'
import { logError, logInfo } from '../api/logger'
import type { NativeServiceSession, NativeSyncCommand, NativeSyncControl, NativeSyncStatus } from '../types'
import { useProject } from '../workbench/projects'
import { syncActions, syncEvents, syncSessionMatches } from '../workbench/syncPresentation'
import { ServiceJsonInput } from './ServiceJsonInput'

/** 配置草案与真实原生公共时钟分开；编辑、保存和读取状态均不发送业务样本。 */
export function ServiceSyncPanel({ session, onChanged }: { session?: NativeServiceSession; onChanged: () => void }) {
  const project = useProject()
  const projectRef = useRef(project)
  projectRef.current = project
  const [profile, setProfile] = useState('')
  const [events, setEvents] = useState('[]')
  const [valid, setValid] = useState(true)
  const [speed, setSpeed] = useState('1')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [observation, setObservation] = useState<{ id: string; status: NativeSyncStatus } | null>(null)
  const [readError, setReadError] = useState('')
  const [uncertain, setUncertain] = useState<ReadonlySet<string>>(new Set())
  const serial = useRef(0)
  const operating = useRef(false)
  const reading = useRef(false)
  const sessionRef = useRef(session)
  sessionRef.current = session
  const status = observation && observation.id === session?.id ? observation.status : null
  const unknown = !!session && uncertain.has(session.id)
  const matched = syncSessionMatches(project?.document.services[profile], session)
  const actions = syncActions(status, !!session?.running && !busy && !unknown && !readError)

  useEffect(() => {
    serial.current++
    setObservation(null); setReadError(''); setError(''); setNotice('')
    if (!session?.running) return
    const id = session.id
    let active = true
    const refresh = async () => {
      if (operating.current || reading.current) return
      reading.current = true
      const ticket = serial.current
      try {
        const next = await api.serviceSync(id)
        if (active && ticket === serial.current) { setObservation({ id, status: next }); setReadError('') }
      } catch (cause) {
        if (active && ticket === serial.current) { logError('读取原生公共时钟失败', cause); setReadError(describeApiError(cause)) }
      } finally { reading.current = false }
    }
    void refresh()
    const timer = window.setInterval(() => void refresh(), 1500)
    return () => { active = false; window.clearInterval(timer) }
  }, [session?.id, session?.running])

  const work = async (label: string, operation: () => Promise<void>, mutation = false) => {
    if (operating.current) return
    operating.current = true; serial.current++; setBusy(true); setError(''); setNotice('')
    const id = session?.id
    try { await operation(); logInfo(label, { sessionId: id }) }
    catch (cause) {
      logError(label, cause, { sessionId: id }); setError(describeApiError(cause))
      if (mutation && id) {
        // 非零错误也可能在回执编码后发生：全部写错误都先只读核对，不自动重发。
        setUncertain(current => new Set([...current, id]))
        if (cause instanceof ApiError && cause.status === 0) setError('写入结果未知，请明确核对原生状态；不会自动重复命令。')
      }
    } finally { serial.current++; operating.current = false; setBusy(false) }
  }
  const command = (): NativeSyncCommand => ({ events: syncEvents(events), paused: true, speed: Number(speed) })
  const observe = (id: string, next: NativeSyncStatus) => {
    if (sessionRef.current?.id === id) { setObservation({ id, status: next }); setReadError('') }
    onChanged()
  }
  const control = (command: NativeSyncControl) => work('控制原生公共时钟', async () => {
    if (!session?.running || unknown || readError) throw new Error('请先核对活动会话的实际原生状态')
    observe(session.id, await api.controlServiceSync(session.id, command))
    setNotice('原生公共时钟已回执；调度计数不是远端交付证明。')
  }, true)

  return <section className="service-sync-panel" aria-label="公共时钟同步组">
    <div className="service-runtime-step"><span>04</span><h4>多事件公共时钟</h4><small>草案不运行 · 原生统一逻辑时间</small></div>
    <p className="muted">同一会话的 1–16 个完整事件共享原生时钟，支持 CSV 与状态机。准备总是暂停且零业务样本；恢复、暂停、单步与倍率由明确按钮控制，不承诺网络同时到达或硬实时。原生提交次数不是线上交付计数。</p>
    <details><summary>同步配置草案（保存／加载不运行）</summary>
      <label>绑定工程服务配置<select aria-label="同步服务配置" value={profile} disabled={busy} onChange={event => setProfile(event.target.value)}><option value="">请选择配置</option>{Object.keys(project?.document.services ?? {}).map(key => <option key={key} value={key}>{key}</option>)}</select></label>
      <p className="muted">选择配置不会覆盖编辑框。明确载入下列草案后再编辑；完整 member/function 名称取该会话 ARXML，不猜测业务值。</p>
      <div className="context-actions"><button className="button secondary" disabled={busy || !profile || !project} onClick={() => {
        const draft = project?.document.sync_groups?.find(item => item.service_profile === profile)
        setEvents(stringifyJson(draft?.command.events ?? [], 2)); setSpeed(String(draft?.command.speed ?? 1)); setError('')
        setNotice(draft ? '同步草案已载入，未运行；网页准备保持暂停。' : '该配置尚无同步草案。'); logInfo('载入同步草案，未运行', { profile })
      }}>载入同步草案（不运行）</button>
      <button className="button secondary" disabled={busy || !profile || !project} onClick={() => {
        const drafts = project?.document.cycles.filter(item => item.service_profile === profile).map(item => item.command) ?? []
        setEvents(stringifyJson(drafts, 2)); setNotice('周期草案已复制，未保存也未运行。'); logInfo('复制完整周期草案到同步编辑器', { profile })
      }}>复制该配置周期草案</button></div>
      <ServiceJsonInput label="同步完整事件列表（JSON 数组，上限 2 MiB）" ariaLabel="同步事件列表" value={events} rows={10} array disabled={busy} onChange={setEvents} onValidityChange={setValid} />
      <div className="context-actions"><button className="button secondary" disabled={busy || !valid || !profile || !project} onClick={() => void work('将同步配置加入工程草案', async () => {
        if (!project || !project.document.services[profile]) throw new Error('请选择当前工程内的服务配置')
        const previous = project.document.sync_groups?.find(item => item.service_profile === profile)
        const prepared = command()
        const candidate = { ...project.document, sync_groups: [...(project.document.sync_groups ?? []).filter(item => item.service_profile !== profile), { service_profile: profile, command: prepared }] }
        const checked = await api.validateProject(candidate)
        const normalized = checked.sync_groups.find(item => item.service_profile === profile)!
        if (projectRef.current?.document.services[profile] !== candidate.services[profile]) throw new Error('校验期间工程配置已切换，请重新校验；没有覆盖当前草案')
        if (projectRef.current?.document.sync_groups?.find(item => item.service_profile === profile) !== previous) throw new Error('校验期间同步草案已修改，请重新校验；没有覆盖新编辑')
        project.update(current => ({ ...current, sync_groups: [...(current.sync_groups ?? []).filter(item => item.service_profile !== profile), normalized] }))
        setNotice('同步配置已加入工程草案；请点击顶部保存配置。未准备、未发包。')
      })}>加入工程草案（不运行）</button></div>
    </details>
    <label>时间倍率<select aria-label="公共时钟时间倍率" disabled={busy} value={speed} onChange={event => setSpeed(event.target.value)}>{[0.25, 0.5, 1, 2, 4].map(value => <option key={value} value={value}>{value}×</option>)}</select></label>
    <p className="muted">{session ? `当前会话：${session.application_name}` : '选择或初始化服务会话后才能控制原生时钟。'}{profile && !matched && ' 绑定草案与当前会话身份不同，禁止准备。'}</p>
    <div className="context-actions">
      <button className="button primary" disabled={!actions.prepare || !matched || !valid} onClick={() => void work('准备暂停的原生公共时钟', async () => {
        if (!session || !matched) throw new Error('同步草案必须绑定当前会话的服务配置')
        observe(session.id, await api.startServiceSync(session.id, command())); setNotice('原生组已准备并暂停，业务样本为零；单步或恢复才采样。')
      }, true)}>准备同步组（保持暂停）</button>
      <button className="button secondary" disabled={!actions.resume} onClick={() => void control({ action: 'resume' })}>恢复公共时钟</button>
      <button className="button secondary" disabled={!actions.pause} onClick={() => void control({ action: 'pause' })}>暂停公共时钟</button>
      <button className="button secondary" disabled={!actions.step} onClick={() => void control({ action: 'step' })}>单步公共帧</button>
      <button className="button secondary" disabled={!actions.speed} onClick={() => void control({ action: 'speed', speed: Number(speed) })}>应用时间倍率</button>
      <button className="button danger" disabled={!actions.stop} onClick={() => void control({ action: 'stop' })}>停止并释放同步组</button>
      <button className="button secondary" disabled={busy || !session?.running} onClick={() => void work('明确核对原生公共时钟状态', async () => {
        if (!session) return
        observe(session.id, await api.serviceSync(session.id))
        setUncertain(current => { const next = new Set(current); next.delete(session.id); return next })
        setNotice('已只读核对原生状态，未重复写命令。')
      })}>核对原生状态</button>
    </div>
    {unknown && <p role="alert">上次写命令失败或结果未知；自动轮询不会解除写保护，请明确核对状态。</p>}
    {(error || readError) && <p className="service-json-error" role="alert">{error || readError}</p>}
    {notice && <p role="status">{notice}</p>}
    {status ? <div aria-label="实际公共时钟状态"><p>{status.active ? status.paused ? '原生组已暂停' : '原生组运行中' : '无活动同步组'} · 下一帧 {String(status.frame_index)} · 逻辑时间 {String(status.logical_ms)} ms · 步长 {status.interval_ms ?? '—'} ms · 实际倍率 {status.speed}×</p>
      {status.last_error && <p role="alert">{status.last_error}</p>}
      {status.events.map((event, index) => <p key={index}>{event.member} · {event.function} · {event.interval_ms} ms · 提交通知 {String(event.emitted_count)} 次 · 最近采样 {event.last_logical_ms == null ? '未采样' : `${event.last_logical_ms} ms`} · 状态 {stringifyJson(event.active_states)}</p>)}
      <small>组 {status.group_id ?? '无'}；原生调度观测，不是线上交付计数。停止组不释放整个服务会话。</small>
    </div> : <p className="muted">尚未确认当前会话的原生公共时钟状态，运行按钮保持禁用。</p>}
  </section>
}
