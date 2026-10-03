import { useEffect, useRef, useState } from 'react'
import { api, describeApiError } from '../api/client'
import { normalizeMonitorMessage } from '../api/adapters'
import { stringifyJson } from '../api/json'
import { logError, logInfo } from '../api/logger'
import { SignalScope } from '../components/SignalScope'
import { MonitorBuffer } from '../data/monitorBuffer'
import { ReplayTimeline, type ReplayFrame } from './replay'

export interface RecordingView {
  id: string; format_version: 1; config: { name: string; segment_bytes: number; quota_bytes: number; queue_capacity: number }
  started_at: string; finished_at: string | null
  state: 'recording' | 'stopped' | 'quota' | 'failed' | 'interrupted'
  scope: 'monitor_publish'; frame_count: number; bytes: number; duration_ms: number; queue_discarded: number
  unsealed_frames: number; buffered_not_recorded: number; skipped_by_filter: number; last_error: string | null
  segments: Array<{ number: number; sha256: string; bytes: number; frame_count: number }>
}

export function RecordingPanel() {
  const [records, setRecords] = useState<RecordingView[]>([])
  const [name, setName] = useState('监控流记录')
  const [quota, setQuota] = useState('256')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [record, setRecord] = useState<RecordingView | null>(null)
  const [service, setService] = useState('')
  const [method, setMethod] = useState('')
  const [direction, setDirection] = useState('')
  const [view, setView] = useState(() => new MonitorBuffer().snapshot())
  const [clock, setClock] = useState({ position: 0, playing: false, ended: false })
  const [seek, setSeek] = useState('0')
  const [selectedFrame, setSelectedFrame] = useState<unknown>(null)
  const timeline = useRef(new ReplayTimeline())
  const buffer = useRef(new MonitorBuffer())
  const epoch = useRef(0)
  const loading = useRef(false)
  const playingAfterLoad = useRef(false)
  const appliedFilters = useRef<Record<string, string>>({})
  const fetchFailed = useRef(false)
  const refresh = async () => setRecords(await api.recordings())
  useEffect(() => {
    let active = true, polling = false
    const poll = async () => {
      if (polling) return
      polling = true
      try { const rows = await api.recordings(); if (active) setRecords(rows) }
      catch (error) { if (active) { logError('读取连续记录状态失败', error); setNotice(describeApiError(error)) } }
      finally { polling = false }
    }
    void poll(); const timer = window.setInterval(() => void poll(), 2000)
    return () => { active = false; window.clearInterval(timer); epoch.current += 1 }
  }, [])

  const publish = (frames: ReplayFrame[]) => {
    for (const frame of frames) buffer.current.append(normalizeMonitorMessage(frame.message), new Date(record?.started_at ?? 0).getTime() + frame.offset_ms)
    if (frames.length) setView(buffer.current.snapshot())
    if (frames.length || timeline.current.playing) setSeek(String(timeline.current.position))
    setClock({ position: timeline.current.position, playing: timeline.current.playing, ended: timeline.current.ended })
  }
  const filters = () => {
    const values: Record<string, string> = {}
    for (const [key, text] of [['service_id', service], ['method_id', method]]) {
      if (!text.trim()) continue
      const id = Number(text)
      if (!Number.isInteger(id) || id < 0 || id > 65535) throw new Error('Service/Method ID 无效')
      values[key] = String(id)
    }
    if (direction) values.direction = direction
    return values
  }
  const load = async (current: RecordingView, reset = false, position = 0) => {
    const generation = reset ? ++epoch.current : epoch.current
    if (loading.current && !reset) return
    loading.current = true
    fetchFailed.current = false
    try {
      if (reset) { appliedFilters.current = filters(); timeline.current.reset(position); buffer.current = new MonitorBuffer(); setView(buffer.current.snapshot()); setSelectedFrame(null) }
      const result = await api.recordingFrames(current.id, { ...appliedFilters.current, offset: String(timeline.current.nextOffset), start_ms: String(position) })
      if (generation !== epoch.current) return
      timeline.current.load(result)
      if (playingAfterLoad.current) timeline.current.play(performance.now())
      publish([])
    } catch (error) {
      if (generation === epoch.current) { fetchFailed.current = true; playingAfterLoad.current = false; logError('离线回放读取或校验失败', error); timeline.current.pause(); setNotice(describeApiError(error)); publish([]) }
    } finally { if (generation === epoch.current) loading.current = false }
  }
  useEffect(() => {
    if (!record) return
    const timer = window.setInterval(() => {
      try {
        publish(timeline.current.advance(performance.now()))
        if (!timeline.current.buffered && !timeline.current.complete && !loading.current && !fetchFailed.current) {
          playingAfterLoad.current = timeline.current.playing; void load(record)
        }
      } catch (error) { logError('离线回放调度失败', error); timeline.current.pause(); setNotice(describeApiError(error)) }
    }, 100)
    return () => window.clearInterval(timer)
  }, [record, service, method, direction])
  const run = async (action: string, work: () => Promise<void>) => {
    setBusy(true); setNotice('')
    try { await work(); await refresh(); logInfo(action) }
    catch (error) { logError(action, error); setNotice(`${action}失败：${describeApiError(error)}`) }
    finally { setBusy(false) }
  }
  return <details className="panel" style={{ marginBottom: 12 }}>
    <summary>连续记录与离线回放（只读，不向网络重放）</summary>
    <p className="muted">记录监控发布流，不是完整网卡抓包；单独报告队列丢弃、容量停止和中断。记录不启动网卡或服务。回放使用到达时间轴，保留原始报文时间。</p>
    <div className="context-actions">
      <input aria-label="记录名称" value={name} onChange={event => setName(event.target.value)} />
      <label>额度（MiB）<input aria-label="记录额度 MiB" type="number" value={quota} onChange={event => setQuota(event.target.value)} /></label>
      <button className="button primary" disabled={busy} onClick={() => void run('启动连续记录', async () => { await api.startRecording({ name, quota_bytes: Number(quota) * 1048576 }) })}>开始记录监控流</button>
    </div>
    {records.map(item => <div key={item.id} className="context-actions">
      <span>{item.config.name} · {item.state} · {item.frame_count} 帧 · {(item.bytes / 1048576).toFixed(2)} MiB · 队列丢弃 {item.queue_discarded} · 未封存 {item.unsealed_frames} · 额度未记录 {item.buffered_not_recorded}{item.last_error ? ` · ${item.last_error}` : ''}</span>
      {item.state === 'recording' ? <button className="button secondary" disabled={busy} onClick={() => void run('停止并封存记录', async () => { await api.stopRecording(item.id) })}>停止并封存</button> : <>
        <button className="button secondary" disabled={busy} onClick={() => { setRecord(item); setSeek('0'); playingAfterLoad.current = false; void load(item, true) }}>载入离线回放</button>
        <a className="button secondary" href={api.recordingExportUrl(item.id)} download>导出校验 ZIP</a>
      </>}
    </div>)}
    {notice && <p role="status">{notice}</p>}
    {record && <section aria-label="记录离线回放">
      <p>离线记录：{record.config.name} · {clock.position.toFixed(0)} / {record.duration_ms.toFixed(0)} ms · {clock.ended ? '已到结尾' : clock.playing ? '回放中' : '已暂停'} · 原始数据不进入实时监控流</p>
      <div className="context-actions">
        <input aria-label="回放 Service ID" value={service} placeholder="Service ID，可选" onChange={event => { playingAfterLoad.current = false; timeline.current.pause(); setService(event.target.value) }} />
        <input aria-label="回放 Method ID" value={method} placeholder="Event/Method ID，可选" onChange={event => { playingAfterLoad.current = false; timeline.current.pause(); setMethod(event.target.value) }} />
        <select aria-label="回放方向" value={direction} onChange={event => { playingAfterLoad.current = false; timeline.current.pause(); setDirection(event.target.value) }}><option value="">全部方向</option><option value="rx">rx</option><option value="tx">tx</option><option value="sim">sim</option><option value="pcap">pcap</option></select>
        <button className="button secondary" disabled={busy} onClick={() => { playingAfterLoad.current = false; void load(record, true, Number(seek)) }}>应用过滤/跳转</button>
        <button className="button primary" onClick={() => { playingAfterLoad.current = true; timeline.current.play(performance.now()); publish([]) }}>播放</button>
        <button className="button secondary" onClick={() => { playingAfterLoad.current = false; timeline.current.pause(); publish([]) }}>暂停</button>
        <button className="button secondary" disabled={busy} onClick={() => void run('离线单步', async () => { playingAfterLoad.current = false; if (!timeline.current.buffered && !timeline.current.complete) await load(record); publish(timeline.current.step()) })}>单步一帧</button>
        <select aria-label="离线回放倍率" defaultValue="1" onChange={event => publish(timeline.current.setRate(Number(event.target.value), performance.now()))}>{[0.1, 0.5, 1, 2, 5, 10].map(value => <option key={value} value={value}>{value}×</option>)}</select>
        <input aria-label="回放位置 ms" type="number" value={seek} onChange={event => setSeek(event.target.value)} min={0} max={record.duration_ms} />
      </div>
      <input aria-label="记录时间轴" type="range" min={0} max={Math.max(1, record.duration_ms)} value={Math.min(Number(seek), Math.max(1, record.duration_ms))} onChange={event => { setSeek(event.target.value); playingAfterLoad.current = false; timeline.current.pause() }} onPointerUp={event => void load(record, true, Number(event.currentTarget.value))} onKeyUp={event => void load(record, true, Number(event.currentTarget.value))} style={{ width: '100%' }} />
      <SignalScope samples={view.samples} title="离线记录波形 · 与到达时间轴联动" />
      <div style={{ maxHeight: 250, overflow: 'auto' }}><table><thead><tr><th>原始时间</th><th>方向</th><th>Service / Method</th><th>Payload</th></tr></thead><tbody>{view.messages.slice(0, 200).map(item => <tr key={item.id} onClick={() => setSelectedFrame(item)}><td>{item.timestamp}</td><td>{item.direction}</td><td>{item.serviceId} / {item.methodId}</td><td>{item.payload?.slice(0, 100)}</td></tr>)}</tbody></table></div>
      {selectedFrame !== null && <pre style={{ maxHeight: 200, overflow: 'auto' }}>{stringifyJson(selectedFrame, 2)}</pre>}
    </section>}
  </details>
}
