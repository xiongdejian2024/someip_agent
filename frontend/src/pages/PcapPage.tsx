import { useState } from 'react'
import { formatHex, normalizePcapResult } from '../api/adapters'
import { api, ApiError, describeApiError } from '../api/client'
import { stringifyJson } from '../api/json'
import { logError, logInfo } from '../api/logger'
import { FileDropzone } from '../components/FileDropzone'
import { Icon } from '../components/Icon'
import { useAgentScope } from '../agent/workspace'
import type { PcapImportResult } from '../types'

const MAX_UPLOAD_BYTES = 256 * 1024 * 1024

const demoResult: PcapImportResult = normalizePcapResult({
  source_name: 'offline-demo.pcap',
  packet_count: 200,
  captured_bytes: 156_420,
  someip_count: 128,
  someip_packet_count: 110,
  sd_count: 24,
  sd_packet_count: 20,
  skipped_count: 90,
  duration_seconds: 12.5,
  start_time: '2026-01-01T08:00:00.000Z',
  end_time: '2026-01-01T08:00:12.500Z',
  endpoint_count: 4,
  transport_counts: { udp: 170, tcp: 25, other: 5 },
  protocol_counts: { someip: 90, someip_sd: 18, someip_mixed: 2, other: 90 },
  sd_entry_counts: {
    FindService: 4,
    OfferService: 8,
    SubscribeEventgroup: 6,
    SubscribeEventgroupAck: 6,
  },
  top_endpoints: [
    {
      endpoint: '192.0.2.10', ip_version: 4, is_multicast: false,
      packet_count: 126, sent_count: 82, received_count: 44, someip_count: 96,
      transport_counts: { udp: 110, tcp: 16 }, offered_service_ids: [0x1234],
    },
    {
      endpoint: '192.0.2.20', ip_version: 4, is_multicast: false,
      packet_count: 94, sent_count: 43, received_count: 51, someip_count: 62,
      transport_counts: { udp: 70, tcp: 24 }, offered_service_ids: [0x1250],
    },
    {
      endpoint: '239.192.255.251', ip_version: 4, is_multicast: true,
      packet_count: 20, sent_count: 0, received_count: 20, someip_count: 24,
      transport_counts: { udp: 20 }, offered_service_ids: [],
    },
  ],
  errors: [],
})

const protocolMetadata = [
  { key: 'someip', label: 'SOME/IP 业务帧', className: 'green', color: 'var(--green)' },
  { key: 'someip_sd', label: 'SOME/IP-SD 帧', className: 'purple', color: 'var(--purple)' },
  { key: 'someip_mixed', label: 'SOME/IP 混合帧', className: 'blue', color: 'var(--blue)' },
  { key: 'other', label: '其他 / 未解码帧', className: '', color: '#30464f' },
]

const sdLabels: Record<string, string> = {
  FindService: 'FindService',
  OfferService: 'OfferService',
  StopOfferService: 'StopOfferService',
  SubscribeEventgroup: 'SubscribeEventgroup',
  StopSubscribeEventgroup: 'StopSubscribeEventgroup',
  SubscribeEventgroupAck: 'SubscribeEventgroupAck',
  SubscribeEventgroupNack: 'SubscribeEventgroupNack',
}

function percentage(value: number, total: number): string {
  return total > 0 ? `${((value / total) * 100).toFixed(1)}%` : '0.0%'
}

function formatBytes(value: number): string {
  if (value >= 1024 * 1024) return `${(value / 1024 / 1024).toFixed(2)} MB`
  if (value >= 1024) return `${(value / 1024).toFixed(2)} KB`
  return `${value} B`
}

function formatTimestamp(value: string | null): string {
  if (!value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN', { hour12: false })
}

function buildConicGradient(rows: Array<{ count: number; color: string }>): string {
  const total = rows.reduce((sum, row) => sum + row.count, 0)
  if (total <= 0) return '#30464f'
  let cursor = 0
  const stops = rows.filter((row) => row.count > 0).map((row) => {
    const start = cursor
    cursor += (row.count / total) * 100
    return `${row.color} ${start.toFixed(2)}% ${cursor.toFixed(2)}%`
  })
  return `conic-gradient(${stops.join(', ')})`
}

function transportSummary(counts: Record<string, number>): string {
  const entries = Object.entries(counts).filter(([, count]) => count > 0)
  return entries.length
    ? entries.map(([name, count]) => `${name.toUpperCase()} ${count.toLocaleString()}`).join(' · ')
    : '无可用传输层统计'
}

export function PcapPage() {
  const [file, setFile] = useState<File | null>(null)
  const [result, setResult] = useState<PcapImportResult | null>(null)
  const [processing, setProcessing] = useState(false)
  const [demo, setDemo] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const openAgent = useAgentScope({ page: 'pcap', source: 'pcap' })

  const process = async () => {
    if (!file) return
    if (file.size > MAX_UPLOAD_BYTES) {
      setNotice('文件超过默认 256 MB 上传上限，请裁剪抓包或调整服务端配置。')
      return
    }
    setProcessing(true)
    setNotice(null)
    try {
      const parsed = await api.importPcap(file)
      setResult({ ...parsed, file_name: parsed.file_name || file.name })
      setDemo(false)
      setNotice('PCAP 解析完成，下列统计均来自本次抓包的逐帧解析结果。')
      logInfo('PCAP 导入完成', { fileName: file.name, packets: parsed.packet_count })
    } catch (error) {
      logError('PCAP 导入失败', error, { fileName: file.name })
      if (error instanceof ApiError && error.status === 0) {
        setResult(demoResult)
        setDemo(true)
        setNotice('后端不可达：当前显示独立的离线演示样例，与所选文件无关，不代表真实解析结果。')
      } else {
        setNotice(`解析失败：${describeApiError(error)}`)
      }
    } finally {
      setProcessing(false)
    }
  }

  const exportReport = () => {
    if (!result) return
    try {
      const report = {
        report_type: 'someip-pcap-analysis',
        schema_version: 1,
        generated_at: new Date().toISOString(),
        demo,
        source: {
          file_name: result.file_name,
          uploaded_file_size: demo ? null : (file?.size ?? null),
        },
        statistics: result,
      }
      const blob = new Blob([stringifyJson(report, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      const baseName = result.file_name.replace(/\.[^.]+$/, '').replace(/[^a-zA-Z0-9._-]+/g, '_')
      anchor.href = url
      anchor.download = `${baseName || 'pcap'}-someip-report.json`
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 0)
      setNotice(demo ? '已导出演示 JSON 报告；报告内 demo=true。' : '已导出本次真实解析结果的 JSON 报告。')
      logInfo('PCAP JSON 报告导出完成', { fileName: result.file_name, demo })
    } catch (error) {
      logError('PCAP JSON 报告导出失败', error, { fileName: result.file_name, demo })
      setNotice('报告导出失败，请查看控制台异常堆栈。')
    }
  }

  const protocolRows = result
    ? protocolMetadata.map((metadata) => ({
      ...metadata,
      count: result.protocol_counts[metadata.key] ?? 0,
    }))
    : []
  const protocolTotal = protocolRows.reduce((sum, row) => sum + row.count, 0)
  const sdRows = result
    ? Object.entries(result.sd_entry_counts)
      .filter(([, count]) => count > 0)
      .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
    : []
  const sdEntryTotal = sdRows.reduce((sum, [, count]) => sum + count, 0)

  return (
    <div className="page pcap-page">
      {!result ? (
        <section className="pcap-upload-layout">
          <div className="pcap-intro">
            <span className="large-icon"><Icon name="file" size={34} /></span>
            <span className="eyebrow">OFFLINE PACKET ANALYSIS</span>
            <h2>从抓包文件还原通信现场</h2>
            <p>逐帧识别 SOME/IP 与 SOME/IP-SD 流量，输出捕获时间、传输层、SD Entry 和通信端点统计。</p>
            <ul>
              <li><Icon name="check" />PCAP / PCAPNG 文件解析</li>
              <li><Icon name="check" />IPv4 / IPv6、UDP / TCP 统计</li>
              <li><Icon name="check" />SOME/IP-SD Entry 与服务提供方归集</li>
            </ul>
          </div>
          <section className="panel pcap-drop-card">
            <FileDropzone
              accept=".pcap,.pcapng,.cap"
              title="拖放抓包文件到这里"
              hint="支持 .pcap、.pcapng，默认上传上限 256 MB"
              file={file}
              onFile={(nextFile) => { setFile(nextFile); setNotice(null) }}
              disabled={processing}
            />
            <div className="pcap-options">
              <label aria-disabled="true">
                <input type="checkbox" defaultChecked disabled />
                <span className="custom-check"><Icon name="check" size={12} /></span>
                <div><strong>关联当前 ARXML 模型（自动）</strong><small>后端会使用当前模型丰富导入到监控区的报文</small></div>
              </label>
              <label aria-disabled="true" style={{ opacity: 0.52, cursor: 'not-allowed' }}>
                <input type="checkbox" disabled />
                <span className="custom-check"><Icon name="check" size={12} /></span>
                <div><strong>智能异常检测（后续版本）</strong><small>当前版本仅输出可验证的协议与流量统计，不生成异常结论</small></div>
              </label>
            </div>
            <button className="button primary large full" disabled={!file || processing} onClick={() => void process()}>
              {processing ? <span className="spinner" /> : <Icon name="activity" />}
              {processing ? '正在构建统计…' : '开始协议解析'}
            </button>
            {notice && (
              <div className={`upload-notice ${notice.includes('失败') || notice.includes('超过') ? 'error' : ''}`}>
                <Icon name="info" size={15} />{notice}
              </div>
            )}
          </section>
        </section>
      ) : (
        <>
          <section className="pcap-result-header panel">
            <div className="pcap-file-icon"><Icon name="file" size={22} /></div>
            <div>
              <span>{demo ? '离线演示样例 · 非所选文件解析' : result.runtime === 'vsomeip' ? '原生 vsomeip 解析已完成' : '解析来源未标识'}</span>
              <h2>{result.file_name}</h2>
              <p>
                {formatBytes(result.captured_bytes)} 捕获数据 · {result.duration_seconds.toFixed(3)} 秒 ·
                {' '}{formatTimestamp(result.start_time)} → {formatTimestamp(result.end_time)}
              </p>
              {!demo && result.runtime === 'vsomeip' && <p>链路类型 {result.link_type ?? '未知'} · IPv4 已重组 {result.reassembled_datagrams} 份 · 分片异常 {result.fragment_error_count} 次 · 文件时间戳，非线上重放或硬件时延</p>}
            </div>
            <div className="pcap-result-actions">
              <button className="button secondary" disabled={demo} onClick={() => openAgent('请分析后端当前缓存中来源为 PCAP 的 SOME/IP 报文和 SD 证据。先给统计范围，再列出异常与下一步验证。注意缓存可能包含多次导入，不要冒充当前文件的完整报告。')}><Icon name="sparkles" />分析抓包证据</button>
              <button className="button ghost" onClick={exportReport}><Icon name="download" />导出 JSON 报告</button>
              <button className="button secondary" onClick={() => { setResult(null); setFile(null); setNotice(null); setDemo(false) }}>
                <Icon name="upload" />重新导入
              </button>
            </div>
          </section>
          {notice && (
            <div className={`inline-notice ${demo ? 'warning' : 'success'}`}>
              <Icon name="info" />{notice}
            </div>
          )}
          <section className="pcap-metrics metric-grid">
            <article className="metric-card green">
              <div className="metric-head"><span>全部捕获帧</span><Icon name="layers" /></div>
              <strong>{result.packet_count.toLocaleString()}</strong><small>{formatBytes(result.captured_bytes)} 链路数据</small>
            </article>
            <article className="metric-card blue">
              <div className="metric-head"><span>SOME/IP 消息</span><Icon name="activity" /></div>
              <strong>{result.someip_count.toLocaleString()}</strong><small>{result.someip_packet_count.toLocaleString()} 个承载帧</small>
            </article>
            <article className="metric-card purple">
              <div className="metric-head"><span>SOME/IP-SD 消息</span><Icon name="radio" /></div>
              <strong>{result.sd_count.toLocaleString()}</strong><small>{sdEntryTotal.toLocaleString()} 个可解析 Entry</small>
            </article>
            <article className="metric-card orange">
              <div className="metric-head"><span>通信 IP 端点</span><Icon name="network" /></div>
              <strong>{result.endpoint_count.toLocaleString()}</strong><small>{result.skipped_count.toLocaleString()} 帧未解码为 SOME/IP</small>
            </article>
          </section>
          <section className="pcap-analysis-grid">
            <article className="panel protocol-distribution">
              <div className="panel-header"><div><span className="panel-kicker">FRAME DISTRIBUTION</span><h3>帧协议分布</h3></div></div>
              <div className="donut-wrap">
                <div className="css-donut" style={{ background: buildConicGradient(protocolRows) }}>
                  <span><strong>{protocolTotal.toLocaleString()}</strong><small>Frames</small></span>
                </div>
                <ul>
                  {protocolRows.map((row) => (
                    <li key={row.key}><i className={row.className} /><span>{row.label}</span><b>{row.count.toLocaleString()} · {percentage(row.count, protocolTotal)}</b></li>
                  ))}
                </ul>
              </div>
              <div className="analysis-callout">
                <Icon name="network" />
                <span><strong>传输层帧统计</strong><small>{transportSummary(result.transport_counts)}</small></span>
              </div>
            </article>
            <article className="panel endpoint-table">
              <div className="panel-header">
                <div><span className="panel-kicker">SERVICE DISCOVERY</span><h3>SD Entry 摘要</h3></div>
                <span className="type-tag">{sdEntryTotal.toLocaleString()} Entries</span>
              </div>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Entry 类型</th><th>数量</th><th>占比</th></tr></thead>
                  <tbody>
                    {sdRows.length ? sdRows.map(([name, count]) => (
                      <tr key={name}>
                        <td className="mono cyan">{sdLabels[name] ?? name}</td>
                        <td>{count.toLocaleString()}</td>
                        <td><span className="load-bar"><i style={{ width: percentage(count, sdEntryTotal) }} /></span>{percentage(count, sdEntryTotal)}</td>
                      </tr>
                    )) : <tr><td colSpan={3}>未检测到可解析的 SOME/IP-SD Entry</td></tr>}
                  </tbody>
                </table>
              </div>
              <div className="analysis-callout">
                <Icon name="info" />
                <span><strong>统计口径</strong><small>来自 {result.sd_count.toLocaleString()} 条已解码 SD 消息；当前版本不输出智能异常结论。</small></span>
              </div>
            </article>
          </section>
          <section className="panel endpoint-table">
            <div className="panel-header">
              <div><span className="panel-kicker">TOP ENDPOINTS</span><h3>通信端点</h3></div>
              <span className="type-tag">显示 {result.top_endpoints.length} / {result.endpoint_count}</span>
            </div>
            <div className="table-wrap">
              <table>
                <thead><tr><th>端点</th><th>地址类型</th><th>参与帧数</th><th>捕获帧占比</th><th>传输层</th><th>提供服务</th><th>SOME/IP 消息</th></tr></thead>
                <tbody>
                  {result.top_endpoints.length ? result.top_endpoints.map((endpoint) => {
                    const frameShare = result.packet_count > 0 ? (endpoint.packet_count / result.packet_count) * 100 : 0
                    return (
                      <tr key={endpoint.endpoint}>
                        <td className="mono cyan">{endpoint.endpoint}</td>
                        <td>{endpoint.is_multicast ? `IPv${endpoint.ip_version} 多播` : `IPv${endpoint.ip_version} 单播`}</td>
                        <td>{endpoint.packet_count.toLocaleString()} <small>发 {endpoint.sent_count} / 收 {endpoint.received_count}</small></td>
                        <td><span className="load-bar"><i style={{ width: `${Math.min(100, frameShare).toFixed(1)}%` }} /></span>{frameShare.toFixed(1)}%</td>
                        <td>{transportSummary(endpoint.transport_counts)}</td>
                        <td className="mono">{endpoint.offered_service_ids.length ? endpoint.offered_service_ids.map((id) => formatHex(id)).join(', ') : '—'}</td>
                        <td>{endpoint.someip_count.toLocaleString()}</td>
                      </tr>
                    )
                  }) : <tr><td colSpan={7}>抓包中没有可统计的 IP 端点</td></tr>}
                </tbody>
              </table>
            </div>
          </section>
          {result.warnings.length > 0 && (
            <section className="panel endpoint-table" style={{ marginTop: 12 }}>
              <div className="panel-header"><div><span className="panel-kicker">PARSER WARNINGS</span><h3>解析告警</h3></div><span className="type-tag">{result.warnings.length}</span></div>
              <div className="table-wrap"><table><tbody>{result.warnings.map((warning, index) => <tr key={`${index}-${warning}`}><td className="mono">#{index + 1}</td><td>{warning}</td></tr>)}</tbody></table></div>
            </section>
          )}
        </>
      )}
    </div>
  )
}
