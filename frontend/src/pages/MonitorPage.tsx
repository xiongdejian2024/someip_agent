import { useEffect, useMemo, useState } from 'react'
import { api, describeApiError } from '../api/client'
import { stringifyJson } from '../api/json'
import { logError, logInfo } from '../api/logger'
import { Icon } from '../components/Icon'
import { StatusBadge } from '../components/StatusBadge'
import { SignalScope } from '../components/SignalScope'
import { protocolId, useAgentScope } from '../agent/workspace'
import type { CaptureInterface, ConnectionState, MonitorMessage, NetworkListener, NetworkListenerConfig, WaveSample } from '../types'
import './MonitorPage.css'

interface MonitorPageProps {
  messages: MonitorMessage[]
  samples: WaveSample[]
  streamState: ConnectionState
  source: 'live' | 'demo'
  onClear: () => Promise<void>
}

function formatTime(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit', fractionalSecondDigits: 3 })
}

export function MonitorPage({ messages, samples, streamState, source, onClear }: MonitorPageProps) {
  const [paused, setPaused] = useState(false)
  const [snapshot, setSnapshot] = useState<MonitorMessage[]>([])
  const [waveSnapshot, setWaveSnapshot] = useState<WaveSample[]>([])
  const [search, setSearch] = useState('')
  const [protocol, setProtocol] = useState('ALL')
  const [direction, setDirection] = useState('ALL')
  const [errorOnly, setErrorOnly] = useState(false)
  const [view, setView] = useState<'trace' | 'graphics' | 'split'>('trace')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)
  const [detailTab, setDetailTab] = useState<'summary' | 'payload' | 'signals'>('summary')
  const [selected, setSelected] = useState<MonitorMessage | null>(null)
  const [selectedSignal, setSelectedSignal] = useState<string>()
  const openAgent = useAgentScope({ page: 'monitor', source: selected?.origin === 'pcap' ? 'pcap' : 'live', frozen: paused, message_id: selected?.id,
    service_id: selected?.protocol === 'SOME/IP-SD' ? undefined : protocolId(selected?.serviceId), method_id: selected?.protocol === 'SOME/IP-SD' ? undefined : protocolId(selected?.methodId), signal_name: selectedSignal })
  useEffect(() => { setSelectedSignal(undefined) }, [selected?.id])
  const [clearBusy, setClearBusy] = useState(false)
  const [clearNotice, setClearNotice] = useState<string | null>(null)
  const [listeners, setListeners] = useState<NetworkListener[]>([])
  const [listenerPanel, setListenerPanel] = useState(false)
  const [listenerBusy, setListenerBusy] = useState(false)
  const [listenerNotice, setListenerNotice] = useState<string | null>(null)
  const [captureInterfaces, setCaptureInterfaces] = useState<CaptureInterface[]>([])
  const [listenerConfig, setListenerConfig] = useState<NetworkListenerConfig>({
    name: 'SOME/IP UDP Listener',
    mode: 'socket',
    capture_interface: '',
    capture_filter: 'udp port 30490 or udp port 30500 or tcp port 30500',
    promiscuous: false,
    transport: 'udp',
    bind_host: '0.0.0.0',
    port: 30490,
    multicast_group: '239.192.255.251',
    interface_ip: '0.0.0.0',
  })

  const loadListeners = async () => {
    try {
      const payload = await api.networkListeners()
      setListeners(payload)
    } catch (error) {
      logError('读取网络监听器失败', error)
    }
  }

  useEffect(() => {
    void loadListeners()
  }, [])

  useEffect(() => {
    if (!listenerPanel) return
    const timer = window.setInterval(() => void loadListeners(), 2000)
    return () => window.clearInterval(timer)
  }, [listenerPanel])

  const loadCaptureInterfaces = async () => {
    try {
      setCaptureInterfaces(await api.networkInterfaces())
      logInfo('原生抓包网卡枚举完成')
    } catch (error) {
      logError('枚举原生抓包网卡失败', error)
      setListenerNotice(`网卡枚举失败：${describeApiError(error)}`)
    }
  }

  const startListener = async () => {
    setListenerBusy(true)
    setListenerNotice(null)
    try {
      await api.startNetworkListener(listenerConfig)
      await loadListeners()
      setListenerNotice(listenerConfig.mode === 'pcap' ? `已启动被动抓包：${listenerConfig.capture_interface}` : `已启动 ${listenerConfig.transport.toUpperCase()} 监听：${listenerConfig.bind_host}:${listenerConfig.port}`)
      logInfo('网络监听器已启动', { name: listenerConfig.name, transport: listenerConfig.transport, port: listenerConfig.port })
    } catch (error) {
      logError('启动网络监听器失败', error, { transport: listenerConfig.transport, port: listenerConfig.port })
      setListenerNotice(`启动失败：${describeApiError(error)}`)
    } finally {
      setListenerBusy(false)
    }
  }

  const stopListener = async (listenerId?: string) => {
    setListenerBusy(true)
    setListenerNotice(null)
    try {
      await api.stopNetworkListener(listenerId)
      await loadListeners()
      setListenerNotice('网络监听器已停止。')
      logInfo('网络监听器已停止', { listenerId: listenerId ?? 'all' })
    } catch (error) {
      logError('停止网络监听器失败', error, { listenerId: listenerId ?? 'all' })
      setListenerNotice(`停止失败：${describeApiError(error)}`)
    } finally {
      setListenerBusy(false)
    }
  }

  const baseMessages = paused ? snapshot : messages
  const baseSamples = paused ? waveSnapshot : samples
  const filtered = useMemo(() => {
    const keyword = search.trim().toLowerCase()
    return baseMessages.filter((item) => {
      const protocolMatch = protocol === 'ALL' || item.protocol === protocol
      const keywordMatch = !keyword || [item.serviceId, item.methodId, item.source, item.destination, item.messageType, item.payload ?? '', stringifyJson(item.signalValues ?? {})].some((value) => value.toLowerCase().includes(keyword))
      const directionMatch = direction === 'ALL' || item.direction === direction
      const errorMatch = !errorOnly || Boolean(item.status && item.status !== 'E_OK' && item.status !== '0x00')
      return protocolMatch && keywordMatch && directionMatch && errorMatch
    })
  }, [baseMessages, direction, errorOnly, protocol, search])
  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize))
  const currentPage = Math.min(page, pageCount)
  const visibleRows = filtered.slice((currentPage - 1) * pageSize, currentPage * pageSize)

  useEffect(() => { setPage(1) }, [direction, errorOnly, pageSize, protocol, search])

  const freeze = () => {
    setSnapshot([...messages])
    setWaveSnapshot([...samples])
    setPaused(true)
    logInfo('已冻结监控与波形显示，后台采集继续', { messageCount: messages.length, sampleCount: samples.length })
  }

  const togglePause = () => {
    if (!paused) freeze()
    else {
      setPaused(false)
      setPage(1)
      logInfo('监控与波形显示已恢复实时跟随')
    }
  }

  const changePage = (nextPage: number) => {
    if (!paused) freeze()
    setPage(nextPage)
  }

  const clearDisplay = async () => {
    if (clearBusy) return
    setClearBusy(true)
    setClearNotice(null)
    try {
      await onClear()
      setSnapshot([])
      setWaveSnapshot([])
      setSelected(null)
      setPage(1)
      logInfo('监控缓冲已清空，冻结快照与报文选择已同步清理')
    } catch (error) {
      logError('清空监控失败，已保留冻结快照与所选报文', error)
      setClearNotice(`清空失败：${describeApiError(error)}。已保留当前数据和所选报文，请重试。`)
    } finally {
      setClearBusy(false)
    }
  }

  const exportCsv = () => {
    const header = ['timestamp', 'direction', 'source', 'destination', 'protocol', 'service_id', 'method_id', 'message_type', 'length', 'status', 'payload']
    const quote = (value: unknown) => `"${String(value ?? '').replaceAll('"', '""')}"`
    const body = filtered.map((item) => [item.timestamp, item.direction, item.source, item.destination, item.protocol, item.serviceId, item.methodId, item.messageType, item.length, item.status, item.payload].map(quote).join(','))
    const blob = new Blob([[header.join(','), ...body].join('\n')], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `someip-monitor-${new Date().toISOString().replaceAll(':', '-')}.csv`
    anchor.click()
    URL.revokeObjectURL(url)
    logInfo('监控报文已导出', { count: filtered.length })
  }

  const rxCount = baseMessages.filter((item) => item.direction === 'RX').length
  const txCount = baseMessages.length - rxCount
  const bytes = baseMessages.reduce((sum, item) => sum + item.length, 0)
  const latencyValues = baseMessages.map((item) => item.latencyMs).filter((value): value is number => typeof value === 'number' && Number.isFinite(value))
  const averageLatency = latencyValues.length ? (latencyValues.reduce((sum, value) => sum + value, 0) / latencyValues.length).toFixed(2) : '—'

  return (
    <div className="page monitor-page monitor-workbench">
      <section className="monitor-toolbar panel">
        <div className="capture-state">
          <span className={`capture-button ${!paused ? 'recording' : ''}`}><i /></span>
          <div><strong>{paused ? '显示已冻结 · 后台继续采集' : streamState === 'online' ? '实时采集 · 最新报文优先' : '等待数据流连接'}</strong><small>{source === 'live' ? '实时测量数据' : '演示数据源'} · 有界缓冲 {baseMessages.length.toLocaleString()} 条</small></div>
          <StatusBadge state={streamState} onlineText="采集连接正常" offlineText="数据流离线" compact />
        </div>
        <div className="monitor-actions">
          <button className="button secondary" onClick={() => openAgent(selected ? '请解释当前选中报文，关联 ARXML 检查接口版本、返回码、Payload 解码与异常原因。引用报文 ID，区分事实和推断。' : '请从当前缓存查询异常报文及 SOME/IP-SD 证据，说明已观察到的事实、证据范围和下一步排查方法。')}><Icon name="sparkles" />{selected ? '诊断此报文' : '分析当前流量'}</button>
          <button className="button ghost" onClick={() => setListenerPanel((value) => !value)}><Icon name="network" />监听器 {listeners.filter((item) => item.running || item.status === 'running').length || ''}</button>
          <button className="button secondary" onClick={togglePause}><Icon name={paused ? 'play' : 'pause'} />{paused ? '恢复实时' : '冻结显示'}</button>
          <button className="button ghost" disabled={clearBusy} onClick={() => void clearDisplay()}>{clearBusy ? <span className="spinner" /> : <Icon name="x" />}{clearBusy ? '清空中…' : '清空'}</button>
          <button className="button ghost" onClick={exportCsv} disabled={!filtered.length}><Icon name="download" />导出 CSV</button>
        </div>
      </section>

      {clearNotice && <div className="inline-notice error" role="alert"><Icon name="info" size={15} /><span>{clearNotice}</span><button aria-label="关闭清空失败提示" onClick={() => setClearNotice(null)}><Icon name="x" size={14} /></button></div>}

      {listenerPanel && (
        <section className="panel listener-panel">
          <p className="muted">端口监听会绑定服务端口；网卡被动抓包不占用端口，由 libpcap 读取、libtins 重组 TCP、vsomeip 解码。抓包需要对应网卡权限，时间戳不是硬件时间。</p>
          <div className="listener-config">
            <label><span>名称</span><input value={listenerConfig.name} onChange={(event) => setListenerConfig((current) => ({ ...current, name: event.target.value }))} /></label>
            <label><span>观测模式</span><select value={listenerConfig.mode ?? 'socket'} onChange={(event) => { const mode = event.target.value as 'socket' | 'pcap'; setListenerConfig((current) => ({ ...current, mode })); if (mode === 'pcap') void loadCaptureInterfaces() }}><option value="socket">端口监听</option><option value="pcap">网卡被动抓包</option></select></label>
            {listenerConfig.mode === 'pcap' ? <>
              <label><span>捕获网卡</span><select value={listenerConfig.capture_interface ?? ''} onChange={(event) => setListenerConfig((current) => ({ ...current, capture_interface: event.target.value }))}><option value="">请选择网卡</option>{captureInterfaces.map((item) => <option key={item.name} value={item.name}>{item.name} · {item.addresses.join(', ') || item.description}</option>)}</select></label>
              <button className="button ghost" onClick={() => void loadCaptureInterfaces()}>刷新网卡</button>
              <label><span>BPF 过滤器</span><input value={listenerConfig.capture_filter ?? ''} onChange={(event) => setListenerConfig((current) => ({ ...current, capture_filter: event.target.value }))} /></label>
              <label><span>混杂模式</span><input type="checkbox" checked={Boolean(listenerConfig.promiscuous)} onChange={(event) => setListenerConfig((current) => ({ ...current, promiscuous: event.target.checked }))} /></label>
            </> : <>
            <label><span>传输协议</span><select value={listenerConfig.transport} onChange={(event) => setListenerConfig((current) => ({ ...current, transport: event.target.value as 'udp' | 'tcp' }))}><option value="udp">UDP</option><option value="tcp">TCP</option></select></label>
            <label><span>绑定地址</span><input value={listenerConfig.bind_host} onChange={(event) => setListenerConfig((current) => ({ ...current, bind_host: event.target.value }))} /></label>
            <label><span>端口</span><input type="number" min={1} max={65535} value={listenerConfig.port} onChange={(event) => setListenerConfig((current) => ({ ...current, port: Number(event.target.value) }))} /></label>
            {listenerConfig.transport === 'udp' && <label><span>组播地址</span><input value={listenerConfig.multicast_group ?? ''} onChange={(event) => setListenerConfig((current) => ({ ...current, multicast_group: event.target.value }))} placeholder="可选" /></label>}
            <label><span>接口 IP</span><input value={listenerConfig.interface_ip} onChange={(event) => setListenerConfig((current) => ({ ...current, interface_ip: event.target.value }))} /></label>
            </>}
            <button className="button primary" disabled={listenerBusy || !listenerConfig.name || (listenerConfig.mode === 'pcap' ? !listenerConfig.capture_interface || !listenerConfig.capture_filter : !listenerConfig.port)} onClick={() => void startListener()}>{listenerBusy ? <span className="spinner" /> : <Icon name="play" />}启动观测</button>
          </div>
          {listenerNotice && <div className={`listener-notice ${listenerNotice.includes('失败') ? 'error' : ''}`}><Icon name="info" size={14} />{listenerNotice}</div>}
          {listeners.length > 0 && <div className="active-listeners">{listeners.map((listener) => {
            const isRunning = listener.running || listener.status === 'running'
            return <div key={listener.id}><span className={`result-dot ${isRunning ? 'ok' : ''}`} /><strong>{listener.name}</strong><code>{listener.mode === 'pcap' ? `${listener.capture_interface} · ${listener.capture_filter}` : `${listener.transport.toUpperCase()} · ${listener.bind_host}:${listener.port}${listener.multicast_group ? ` · ${listener.multicast_group}` : ''}`}</code><em>{listener.message_count?.toLocaleString() ?? 0} 条</em>{listener.mode === 'pcap' && <span>捕获 {listener.captured_count ?? 0} 帧 · 内核丢包 {listener.kernel_dropped_count ?? '未知'} · 接口丢包 {listener.interface_dropped_count ?? '未知'} · 活跃流 {listener.active_streams ?? 0} · 分片上下文 {listener.active_fragment_datagrams ?? 0}（{listener.fragment_buffered_bytes ?? 0} 字节）· 已重组 {listener.reassembled_datagrams ?? 0} · 分片异常 {listener.fragment_error_count ?? 0}</span>}{listener.last_error && <span className="error" title={listener.last_error}>异常 {listener.parse_error_count ?? 0}：{listener.last_error}</span>}{isRunning ? <button className="button ghost" disabled={listenerBusy} onClick={() => void stopListener(listener.id)}><Icon name="stop" />停止</button> : <span className="muted">已停止</span>}</div>
          })}</div>}
        </section>
      )}

      <div className="monitor-measurement-bar">
        <div className="segmented monitor-view-switch" aria-label="监控视图"><button className={view === 'trace' ? 'active' : ''} onClick={() => setView('trace')}>Trace 报文</button><button className={view === 'graphics' ? 'active' : ''} onClick={() => setView('graphics')}>Graphics 波形</button><button className={view === 'split' ? 'active' : ''} onClick={() => setView('split')}>联合视图</button></div>
        <div className="monitor-counters"><span>RX <strong>{rxCount.toLocaleString()}</strong></span><span>TX <strong>{txCount.toLocaleString()}</strong></span><span>缓冲数据 <strong>{(bytes / 1024).toFixed(1)} KB</strong></span><span title={latencyValues.length ? `${latencyValues.length} 条携带延迟的样本` : '报文未携带延迟元数据'}>平均延迟 <strong>{averageLatency}{averageLatency === '—' ? '' : ' ms'}</strong></span></div>
      </div>

      <div className={`monitor-window-layout is-${view}`}>
        {view !== 'graphics' && <section className="panel trace-window" aria-label="Trace 报文窗口">
          <div className="trace-filterbar">
            <label className="trace-search"><Icon name="search" size={14} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索 ID、地址、类型、Payload 或信号" aria-label="搜索监控报文" />{search && <button aria-label="清除报文搜索" onClick={() => setSearch('')}><Icon name="x" size={13} /></button>}</label>
            <select aria-label="过滤报文协议" value={protocol} onChange={(event) => setProtocol(event.target.value)}><option value="ALL">全部协议</option><option value="SOME/IP">SOME/IP</option><option value="SOME/IP-SD">SOME/IP-SD</option></select>
            <select aria-label="过滤报文方向" value={direction} onChange={(event) => setDirection(event.target.value)}><option value="ALL">双向</option><option value="RX">接收 RX</option><option value="TX">发送 TX</option></select>
            <label className="trace-error-filter"><input type="checkbox" checked={errorOnly} onChange={(event) => setErrorOnly(event.target.checked)} />仅异常</label>
            <button className="button ghost" disabled={!search && protocol === 'ALL' && direction === 'ALL' && !errorOnly} onClick={() => { setSearch(''); setProtocol('ALL'); setDirection('ALL'); setErrorOnly(false) }}>重置过滤</button>
          </div>
          <div className="trace-content">
            <div className="trace-table-viewport">
              <table className="trace-table">
                <colgroup>{[48, 115, 64, 94, 88, 105, 134, 64, 94, 170, 170].map((width, index) => <col key={index} style={{ width }} />)}</colgroup>
                <thead><tr><th>#</th><th>时间</th><th>方向</th><th>协议</th><th>Service</th><th>Method / Event</th><th>消息类型</th><th>字节</th><th>返回码</th><th>源地址</th><th>目标地址</th></tr></thead>
                <tbody>{visibleRows.map((item, index) => <tr key={item.id} tabIndex={0} aria-selected={selected?.id === item.id} className={selected?.id === item.id ? 'selected' : ''} onClick={() => setSelected(item)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setSelected(item) } }}>
                  <td className="mono muted">{(currentPage - 1) * pageSize + index + 1}</td>
                  <td className="mono" title={item.timestamp}>{formatTime(item.timestamp)}</td>
                  <td><span className={`direction ${item.direction.toLowerCase()}`}>{item.direction === 'RX' ? '↓' : '↑'} {item.direction}</span></td>
                  <td title={item.protocol}><span className={`protocol-tag ${item.protocol === 'SOME/IP-SD' ? 'sd' : ''}`}>{item.protocol}</span></td>
                  <td className="mono cyan" title={item.serviceId}>{item.serviceId}</td><td className="mono" title={item.methodId}>{item.methodId}</td>
                  <td title={item.messageType}>{item.messageType}</td><td className="mono">{item.length}</td>
                  <td title={item.status ?? '未报告'}><span className={`result-dot ${item.status === 'E_OK' || item.status === '0x00' || !item.status ? 'ok' : 'error'}`} />{item.status ?? '—'}</td>
                  <td className="mono" title={item.source}>{item.source}</td><td className="mono" title={item.destination}>{item.destination}</td>
                </tr>)}</tbody>
              </table>
              {!filtered.length && <div className="empty-state small"><Icon name="activity" /><strong>暂无匹配报文</strong><span>{baseMessages.length ? '调整过滤条件以显示报文' : '启动监听器或运行仿真后查看测量数据'}</span></div>}
            </div>
            <aside className="trace-detail" aria-label="所选报文详情">
              <header><strong>报文详情</strong>{selected && <button className="icon-button" title="取消选择" aria-label="取消报文选择" onClick={() => setSelected(null)}><Icon name="x" size={13} /></button>}</header>
              <div className="trace-detail-tabs"><button className={detailTab === 'summary' ? 'active' : ''} onClick={() => setDetailTab('summary')}>概要</button><button className={detailTab === 'payload' ? 'active' : ''} onClick={() => setDetailTab('payload')}>Payload</button><button className={detailTab === 'signals' ? 'active' : ''} onClick={() => setDetailTab('signals')}>信号{selected?.signalValues ? ` (${Object.keys(selected.signalValues).length})` : ''}</button></div>
              <div className="trace-detail-body">
                {!selected ? <div className="trace-detail-empty"><Icon name="eye" size={24} /><strong>选择一条报文</strong><span>详情固定保留，不随实时刷新切换</span></div> : <>
                  {detailTab === 'summary' && <dl>{[
                    ['时间', selected.timestamp], ['方向', selected.direction], ['协议', selected.protocol], ['Service ID', selected.serviceId], ['Method / Event', selected.methodId], ['类型', selected.messageType], ['返回码', selected.status ?? '—'], ['信号解码', selected.signalDecodeError ? `失败：${selected.signalDecodeError}` : selected.signalDecoder ?? '未解码'], ['报文字节数', `${selected.length} B`], ['延迟', selected.latencyMs !== undefined ? `${selected.latencyMs.toFixed(2)} ms` : '—'], ['源地址', selected.source], ['目标地址', selected.destination],
                  ].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>}
                  {detailTab === 'payload' && <pre className="trace-payload">{selected.payload || '无负载数据'}</pre>}
                  {detailTab === 'signals' && (selected.signalDecodeError ? <p className="trace-detail-hint">原生信号解码失败：{selected.signalDecodeError}。原始 Payload 已保留。</p> : selected.signalValues && Object.keys(selected.signalValues).length ? <dl className="trace-signals">{Object.entries(selected.signalValues).map(([name, value]) => <div key={name}><dt title={name}>{name}<button className="text-button" aria-label={`分析信号 ${name}`} onClick={() => { setSelectedSignal(name); openAgent('请分析当前选中信号在缓存中的数值范围、采样间隔与异常证据，明确样本数量和限制。') }}>分析此信号</button></dt><dd>{typeof value === 'object' ? stringifyJson(value) : String(value)}</dd></div>)}</dl> : <p className="trace-detail-hint">当前报文未匹配到可解码的 ARXML 信号定义。</p>)}
                </>}
              </div>
            </aside>
          </div>
          <footer className="trace-pagination"><span>{filtered.length.toLocaleString()} / {baseMessages.length.toLocaleString()} 条 · 过滤仅影响显示</span><div><select aria-label="每页报文数量" value={pageSize} onChange={(event) => setPageSize(Number(event.target.value))}><option value={50}>50 条 / 页</option><option value={100}>100 条 / 页</option><option value={200}>200 条 / 页</option></select><button className="icon-button" disabled={currentPage <= 1} aria-label="上一页报文" onClick={() => changePage(currentPage - 1)}>‹</button><span>{currentPage} / {pageCount}</span><button className="icon-button" disabled={currentPage >= pageCount} aria-label="下一页报文" onClick={() => changePage(currentPage + 1)}>›</button></div></footer>
        </section>}
        {view !== 'trace' && <section className="panel monitor-graphics-window"><SignalScope samples={baseSamples} compact={view === 'split'} title="Graphics 信号波形" /></section>}
      </div>
    </div>
  )
}
