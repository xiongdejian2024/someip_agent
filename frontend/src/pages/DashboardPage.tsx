import { useEffect, useMemo, useState } from 'react'
import type { ConnectionState, MonitorMessage, PageId, ServiceDefinition, WaveSample } from '../types'
import { Icon } from '../components/Icon'
import { StatusBadge } from '../components/StatusBadge'
import { WaveformChart } from '../components/WaveformChart'

interface DashboardPageProps {
  services: ServiceDefinition[]
  modelLoading: boolean
  streamState: ConnectionState
  messages: MonitorMessage[]
  samples: WaveSample[]
  source: 'live' | 'demo'
  modelSource: 'live' | 'demo'
  onNavigate: (page: PageId) => void
  onOpenAgent: () => void
}

function timeText(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit', fractionalSecondDigits: 3 })
}

function signalLabel(key: string) {
  const parts = key.split('/')
  return parts.at(-1) || key
}

export function DashboardPage({ services, modelLoading, streamState, messages, samples, source, modelSource, onNavigate, onOpenAgent }: DashboardPageProps) {
  const [now, setNow] = useState(Date.now)
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(timer) }, [])
  const visibleSignals = useMemo(() => {
    const values = new Map<string, number>()
    for (const sample of samples) for (const [key, value] of Object.entries(sample.values)) {
      if (Number.isFinite(value)) values.set(key, value)
    }
    return [...values.entries()].sort(([left], [right]) => left.localeCompare(right)).slice(0, 3)
  }, [samples])
  const sdCount = messages.filter((item) => item.protocol === 'SOME/IP-SD').length
  const errorCount = messages.filter((item) => item.status && item.status !== 'E_OK').length
  const oneSecondAgo = now - 1_000
  const recentCount = messages.filter((item) => new Date(item.timestamp).getTime() >= oneSecondAgo).length
  const latencyValues = messages.map((item) => item.latencyMs).filter((value): value is number => typeof value === 'number' && Number.isFinite(value))
  const averageLatency = latencyValues.length ? `${(latencyValues.reduce((sum, value) => sum + value, 0) / latencyValues.length).toFixed(2)} ms` : '暂无数据'
  const hour = new Date().getHours()
  const greeting = hour < 6 ? '夜深了' : hour < 12 ? '早上好' : hour < 18 ? '下午好' : '晚上好'

  return (
    <div className="page dashboard-page">
      <section className="hero-strip">
        <div>
          <span className="eyebrow"><i /> {source === 'demo' ? 'DEMO DATA ACTIVE' : streamState === 'online' ? 'MONITOR STREAM CONNECTED' : streamState === 'connecting' ? '监控流连接中' : '监控流已断开 · 保留已采集数据'} {modelSource === 'demo' && <em>· 演示服务模型</em>}</span>
          <h2>{greeting}，工程师</h2>
          <p>{modelLoading ? '正在从后端恢复 ARXML 服务模型…' : services.length ? '当前工作区服务模型已加载，可以开始仿真、监听或智能诊断。' : '当前尚未导入 ARXML，请先加载服务模型。'}</p>
        </div>
        <div className="hero-actions">
          <button className="button secondary" onClick={() => onNavigate('services')}><Icon name="upload" /> 导入 ARXML</button>
          <button className="button primary" onClick={() => onNavigate('simulation')}><Icon name="play" /> 新建仿真</button>
        </div>
      </section>

      <section className="metric-grid">
        <article className="metric-card green">
          <div className="metric-head"><span>已加载服务</span><Icon name="database" /></div>
          <strong>{modelLoading ? '—' : services.length}</strong>
          <small>{modelLoading ? '正在同步模型' : <><b>+{services.reduce((sum, item) => sum + item.events.length, 0)}</b> 个事件信号</>}</small>
          <div className="metric-spark"><i /><i /><i /><i /><i /><i /><i /><i /></div>
        </article>
        <article className="metric-card blue">
          <div className="metric-head"><span>实时报文</span><Icon name="activity" /></div>
          <strong>{messages.length.toLocaleString()}</strong>
          <small><b>{recentCount} msg/s</b> 近 1 秒实际流量</small>
          <div className="metric-spark bars"><i /><i /><i /><i /><i /><i /><i /><i /></div>
        </article>
        <article className="metric-card purple">
          <div className="metric-head"><span>SD 报文</span><Icon name="radio" /></div>
          <strong>{sdCount}</strong>
          <small>当前缓冲区累计</small>
          <div className="metric-ring"><span>{sdCount}</span></div>
        </article>
        <article className={`metric-card ${errorCount ? 'orange' : 'green'}`}>
          <div className="metric-head"><span>诊断异常</span><Icon name="shield" /></div>
          <strong>{errorCount}</strong>
          <small>{errorCount ? '需要关注' : messages.length ? '缓冲区未见错误返回码' : '暂无报文，不能判定健康'}</small>
          <span className="health-label"><i /> {errorCount ? '待分析' : messages.length ? '仅当前缓存' : '等待证据'}</span>
        </article>
      </section>

      <section className="dashboard-grid">
        <article className="panel waveform-panel">
          <div className="panel-header">
            <div><span className="panel-kicker">SIGNAL SCOPE</span><h3>实时信号概览</h3></div>
            <div className="panel-actions">
              <StatusBadge state={streamState} onlineText="后端实时流" offlineText="已断开" compact />
              <button className="text-button" onClick={() => onNavigate('monitor')}>打开示波器 <Icon name="chevron" size={14} /></button>
            </div>
          </div>
          <div className="live-values">
            {visibleSignals.length ? visibleSignals.map(([key, value]) => <span key={key} title={key}><small>{signalLabel(key)}</small><strong>{Math.abs(value) >= 100_000 ? value.toExponential(2) : value.toFixed(2)}</strong></span>) : <span><small>等待 ARXML 解码信号</small><strong>—</strong></span>}
          </div>
          <WaveformChart samples={samples} signalKeys={visibleSignals.map(([key]) => key)} compact />
        </article>

        <article className="panel topology-panel">
          <div className="panel-header">
            <div><span className="panel-kicker">SERVICE MODEL</span><h3>ARXML 服务概览</h3></div>
            <button className="icon-button" aria-label="查看服务模型" onClick={() => onNavigate('services')}><Icon name="chevron" size={15} /></button>
          </div>
          <div className="topology-map">
            <div className="topology-lines"><i /><i /><i /><i /></div>
            <span className="ecu-node central"><Icon name="network" /><b>ARXML</b><small>{modelLoading ? 'loading…' : `${services.length} interfaces`}</small></span>
            {services.slice(0, 3).map((service, index) => <span key={service.id} className={`ecu-node ${['ecu-a', 'ecu-b', 'ecu-c'][index]}`}><Icon name="cpu" /><b>{service.name}</b><small>{service.serviceId}</small></span>)}
          </div>
          <div className="topology-legend"><span><i className="ok" />{modelLoading ? '正在加载服务模型' : `模型服务 ${services.length}`}</span><span><i />网络端点需实时监听确认</span></div>
        </article>
      </section>

      <section className="dashboard-grid lower">
        <article className="panel messages-panel">
          <div className="panel-header">
            <div><span className="panel-kicker">RECENT TRAFFIC</span><h3>最近报文</h3></div>
            <button className="text-button" onClick={() => onNavigate('monitor')}>查看全部 <Icon name="chevron" size={14} /></button>
          </div>
          <div className="table-wrap compact-table">
            <table>
              <thead><tr><th>时间</th><th>方向</th><th>服务 / 方法</th><th>类型</th><th>长度</th><th>状态</th></tr></thead>
              <tbody>
                {messages.slice(0, 5).map((item) => (
                  <tr key={item.id}>
                    <td className="mono muted">{timeText(item.timestamp)}</td>
                    <td><span className={`direction ${item.direction.toLowerCase()}`}>{item.direction === 'RX' ? '↓' : '↑'} {item.direction}</span></td>
                    <td><b className="mono">{item.serviceId}</b> <span className="muted">/ {item.methodId}</span></td>
                    <td><span className="type-tag">{item.messageType}</span></td>
                    <td className="mono">{item.length} B</td>
                    <td><span className={`result-dot ${item.status === 'E_OK' || !item.status ? 'ok' : 'error'}`} />{item.status ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </article>

        <article className="panel insight-panel">
          <div className="insight-glow" />
          <div className="panel-header"><div><span className="panel-kicker">AI INSIGHT</span><h3>智能诊断摘要</h3></div><span className="agent-mini"><Icon name="sparkles" /></span></div>
          <div className="insight-status"><Icon name="shield" /><div><strong>{errorCount ? `检测到 ${errorCount} 条异常返回码` : '当前缓冲区未见错误返回码'}</strong><span>基于 {messages.length.toLocaleString()} 条已解析报文{source === 'demo' ? '（演示数据）' : ''}</span></div></div>
          <ul>
            <li><span>SD 报文</span><b>{sdCount} 条</b></li>
            <li><span>可观测平均延迟</span><b>{averageLatency}</b></li>
            <li><span>异常返回码</span><b>{errorCount} 条</b></li>
          </ul>
          <button className="button agent-button" onClick={onOpenAgent}><Icon name="sparkles" /> 与智能体继续分析</button>
        </article>
      </section>
    </div>
  )
}
