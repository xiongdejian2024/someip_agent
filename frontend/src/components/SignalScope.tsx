import { useEffect, useMemo, useState } from 'react'
import type { WaveSample } from '../types'
import { Icon } from './Icon'
import { formatSignalValue, SIGNAL_COLORS, signalLabel, WaveformChart } from './WaveformChart'
import './SignalScope.css'
import { useProject } from '../workbench/projects'
import { formatHex } from '../api/adapters'

interface SignalScopeProps {
  samples: WaveSample[]
  compact?: boolean
  title?: string
  initialSignalKeys?: string[]
  allowedSignalKeys?: string[]
}

const MAX_SIGNALS = 6

export function SignalScope({ samples, compact = false, title = '信号示波器', initialSignalKeys, allowedSignalKeys }: SignalScopeProps) {
  const project = useProject()
  const restoredKeys = project?.document.workspace.waves.map(wave => `${formatHex(wave.service_id)}/${formatHex(wave.method_id)}/${wave.signal_name}`)
  const [search, setSearch] = useState('')
  const [selectedKeys, setSelectedKeys] = useState<string[] | null>(restoredKeys?.length ? restoredKeys.slice(0, MAX_SIGNALS) : initialSignalKeys?.slice(0, MAX_SIGNALS) ?? null)
  const [layout, setLayout] = useState<'lanes' | 'overlay'>('lanes')
  const [windowSeconds, setWindowSeconds] = useState(30)
  const [resetZoom, setResetZoom] = useState(0)
  const [catalogPage, setCatalogPage] = useState(1)
  const keys = useMemo(() => {
    const allKeys = Array.from(new Set(samples.flatMap((sample) => Object.keys(sample.values))))
    return allowedSignalKeys ? allKeys.filter((key) => allowedSignalKeys.includes(key)) : allKeys
  }, [allowedSignalKeys, samples])
  const visibleKeys = selectedKeys === null ? keys.slice(0, 3) : selectedKeys.filter((key) => keys.includes(key))
  const filteredKeys = useMemo(() => keys.filter((key) => key.toLowerCase().includes(search.toLowerCase())), [keys, search])
  const pageCount = Math.max(1, Math.ceil(filteredKeys.length / 50))
  const safePage = Math.min(catalogPage, pageCount)
  const latestValues = useMemo(() => Object.assign({}, ...samples.map((sample) => sample.values)) as Record<string, number>, [samples])

  useEffect(() => { setCatalogPage(1) }, [search])
  useEffect(() => {
    if (selectedKeys === null && keys.length) setSelectedKeys(keys.slice(0, 3))
  }, [keys, selectedKeys])
  const projectUpdate = project?.update
  useEffect(() => {
    if (selectedKeys === null) return
    const waves = selectedKeys.flatMap(key => {
      const [service, method, ...name] = key.split('/')
      const service_id = Number(service), method_id = Number(method)
      return name.length && Number.isInteger(service_id) && Number.isInteger(method_id) ? [{ service_id, method_id, signal_name: name.join('/') }] : []
    })
    projectUpdate?.(doc => ({ ...doc, workspace: { ...doc.workspace, waves } }))
  }, [projectUpdate, selectedKeys])

  const toggleSignal = (key: string) => {
    setSelectedKeys((current) => {
      const selection = current ?? keys.slice(0, 3)
      return selection.includes(key) ? selection.filter((item) => item !== key) : [...selection.filter((item) => keys.includes(item)), key].slice(0, MAX_SIGNALS)
    })
  }

  return <section className={`signal-scope${compact ? ' is-compact' : ''}`} aria-label={title}>
    <header className="signal-scope-header">
      <div><strong>{title}</strong><span>{visibleKeys.length} / {MAX_SIGNALS} 路 · {layout === 'lanes' ? '独立纵轴' : '共用纵轴'}</span></div>
      <div className="signal-scope-controls">
        <div className="segmented" aria-label="波形布局"><button className={layout === 'lanes' ? 'active' : ''} onClick={() => setLayout('lanes')}>分轨</button><button className={layout === 'overlay' ? 'active' : ''} onClick={() => setLayout('overlay')}>叠加</button></div>
        <select aria-label="波形时间窗口" value={windowSeconds} onChange={(event) => setWindowSeconds(Number(event.target.value))}><option value={5}>最近 5 秒</option><option value={15}>最近 15 秒</option><option value={30}>最近 30 秒</option><option value={60}>最近 60 秒</option><option value={0}>全部缓冲</option></select>
        <button className="icon-button" title="重置缩放" aria-label="重置波形缩放" onClick={() => setResetZoom((value) => value + 1)}><Icon name="refresh" size={15} /></button>
      </div>
    </header>
    <div className="signal-scope-body">
      <aside className="scope-catalog">
        <label className="scope-search"><Icon name="search" size={14} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索信号 / Service ID" aria-label="搜索波形信号" /></label>
        <div className="scope-catalog-summary"><span>{keys.length.toLocaleString()} 个可用信号</span><button onClick={() => setSelectedKeys([])} disabled={!visibleKeys.length}>清除选择</button></div>
        <div className="scope-channel-list" style={{ height: compact ? 226 : 306 }}>
          {filteredKeys.slice((safePage - 1) * 50, safePage * 50).map((key) => {
            const index = visibleKeys.indexOf(key)
            return <label className={`scope-channel${index >= 0 ? ' is-selected' : ''}`} key={key} title={key}>
              <input type="checkbox" checked={index >= 0} disabled={index < 0 && visibleKeys.length >= MAX_SIGNALS} onChange={() => toggleSignal(key)} />
              <i style={{ background: index >= 0 ? SIGNAL_COLORS[index] : 'var(--text-tertiary)' }} />
              <span><strong>{signalLabel(key)}</strong><small>{key.includes('/') ? key.split('/').slice(0, 2).join(' / ') : '信号值'}</small></span>
              <code title={String(latestValues[key] ?? '—')}>{formatSignalValue(latestValues[key])}</code>
            </label>
          })}
          {!filteredKeys.length && <p className="scope-empty">{keys.length ? '没有匹配信号' : '等待报文解码后的数值信号'}</p>}
        </div>
        <footer className="scope-catalog-footer"><span>{visibleKeys.length >= MAX_SIGNALS ? '已选满 6 路，请先取消一项' : '勾选信号加入波形'}</span>{pageCount > 1 && <div><button aria-label="上一页信号" disabled={safePage <= 1} onClick={() => setCatalogPage(safePage - 1)}>‹</button><span>{safePage}/{pageCount}</span><button aria-label="下一页信号" disabled={safePage >= pageCount} onClick={() => setCatalogPage(safePage + 1)}>›</button></div>}</footer>
      </aside>
      <div className="scope-plot" style={{ height: compact ? 300 : 380 }}><WaveformChart samples={samples} compact={compact} signalKeys={visibleKeys} layout={layout} windowSeconds={windowSeconds} height={layout === 'lanes' ? Math.max(compact ? 300 : 380, visibleKeys.length * 90 + 86) : compact ? 300 : 380} showZoom resetZoom={resetZoom} /></div>
    </div>
    <footer className="scope-status"><span>{layout === 'lanes' ? '分轨对照不同量纲；各轨保持独立量程' : '叠加共用量程，适合比较同量纲信号'}</span><span>拖动底部范围缩放 · Ctrl + 滚轮缩放</span></footer>
  </section>
}
