import { useMemo, useState } from 'react'
import { api, describeApiError } from '../api/client'
import { logError, logInfo } from '../api/logger'
import { FileDropzone } from '../components/FileDropzone'
import { Icon } from '../components/Icon'
import { ServiceRuntimePanel } from '../components/ServiceRuntimePanel'
import { protocolId, useAgentScope } from '../agent/workspace'
import type { ServiceDefinition } from '../types'
import { useProject } from '../workbench/projects'

interface ServicesPageProps {
  services: ServiceDefinition[]
  loading: boolean
  source: 'live' | 'demo'
  onServicesChange: (services: ServiceDefinition[]) => void
}

type ChildType = 'methods' | 'events' | 'fields'

const childLabels: Record<ChildType, string> = { methods: 'Methods', events: 'Events', fields: 'Fields' }

export function ServicesPage({ services, loading, source, onServicesChange }: ServicesPageProps) {
  const project = useProject()
  const [selectedId, setSelectedId] = useState(() => services.find(service => project?.document.workspace.service_paths.includes(service.path ?? ''))?.id ?? services[0]?.id ?? '')
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set([services[0]?.id].filter(Boolean)))
  const [search, setSearch] = useState('')
  const [showImporter, setShowImporter] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [importing, setImporting] = useState(false)
  const [notice, setNotice] = useState<{ tone: 'success' | 'error'; text: string } | null>(null)

  const filtered = useMemo(() => {
    const keyword = search.trim().toLowerCase()
    if (!keyword) return services
    return services.filter((service) =>
      [service.name, service.serviceId, service.instanceId, ...service.methods.map((item) => item.name), ...service.events.map((item) => item.name)]
        .some((value) => value.toLowerCase().includes(keyword)),
    )
  }, [search, services])

  const selected = filtered.find((item) => item.id === selectedId) ?? filtered[0]
  const openAgent = useAgentScope({ page: 'services', source: 'live', service_id: selected?.deployed === false ? undefined : protocolId(selected?.serviceId) })

  const toggle = (id: string) => {
    setExpanded((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const importFile = async () => {
    if (!file) return
    setImporting(true)
    setNotice(null)
    try {
      const result = await api.importArxml(file)
      const count = result.imported ?? result.services?.length ?? 0
      if (!result.services?.length) {
        const reason = result.warnings?.[0] ?? '未识别到可展示的 SOME/IP 服务定义'
        setNotice({ tone: 'error', text: `文件已解析，但服务数为 0：${reason}` })
        logError('ARXML 未解析出服务', new Error(reason), { fileName: file.name, warnings: result.warnings ?? [] })
        return
      }
      onServicesChange(result.services)
      setSelectedId(result.services[0].id)
      setNotice({ tone: 'success', text: `ARXML 解析完成，已导入 ${count} 个服务定义${result.warnings?.length ? `，${result.warnings.length} 条警告` : ''}` })
      logInfo('ARXML 导入完成', { fileName: file.name, services: count })
      setShowImporter(false)
      setFile(null)
    } catch (error) {
      logError('ARXML 导入失败', error, { fileName: file.name })
      setNotice({ tone: 'error', text: `导入失败：${describeApiError(error)}` })
    } finally {
      setImporting(false)
    }
  }

  return (
    <div className="page services-page">
      <div className="page-toolbar">
        <div className="toolbar-summary">
          {source === 'demo' && <span className="demo-data-tag">演示模型</span>}
          <span><b>{loading ? '—' : services.length}</b> 个服务</span><i />
          <span><b>{services.reduce((sum, service) => sum + service.methods.length, 0)}</b> 个方法</span><i />
          <span><b>{services.reduce((sum, service) => sum + service.events.length, 0)}</b> 个事件</span>
        </div>
        <div className="context-actions"><button className="button secondary" disabled={!selected || source === 'demo'} onClick={() => openAgent('请检查当前选中的 ARXML 服务，说明方法、事件、字段及仿真支持范围，列出需要补充的模型信息。')}><Icon name="sparkles" />分析此服务</button><button className="button primary" onClick={() => setShowImporter((value) => !value)}><Icon name="upload" />导入 ARXML</button></div>
      </div>

      {notice && <div className={`inline-notice ${notice.tone}`}><Icon name={notice.tone === 'success' ? 'check' : 'info'} />{notice.text}<button onClick={() => setNotice(null)}><Icon name="x" size={14} /></button></div>}

      <ServiceRuntimePanel selected={selected} demo={source === 'demo'} />

      {showImporter && (
        <section className="panel import-panel">
          <div className="panel-header"><div><span className="panel-kicker">AUTOSAR MODEL</span><h3>导入服务定义</h3></div><button className="icon-button" onClick={() => setShowImporter(false)}><Icon name="x" /></button></div>
          <FileDropzone accept=".arxml,.xml" title="拖放 ARXML 文件到这里" hint="支持 AUTOSAR Classic / Adaptive ARXML，单文件最大 200 MB" file={file} onFile={setFile} disabled={importing} />
          <div className="import-actions"><span><Icon name="shield" size={14} /> 文件仅上传到本地后端解析</span><button className="button primary" disabled={!file || importing} onClick={() => void importFile()}>{importing ? <span className="spinner" /> : <Icon name="upload" />} {importing ? '正在解析…' : '开始导入'}</button></div>
        </section>
      )}

      <section className="model-workspace">
        <aside className="model-tree panel">
          <div className="tree-toolbar">
            <div className="search-input"><Icon name="search" size={15} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索服务或接口" />{search && <button onClick={() => setSearch('')}><Icon name="x" size={13} /></button>}</div>
          </div>
          <div className="tree-root-label"><Icon name="layers" size={15} /><span>Service Interfaces</span><em>{filtered.length}</em></div>
          <div className="service-tree-list">
            {filtered.map((service) => (
              <div key={service.id} className="tree-service">
                <button className={`tree-service-row${selected?.id === service.id ? ' selected' : ''}`} onClick={() => { setSelectedId(service.id); toggle(service.id); project?.update(doc => ({ ...doc, workspace: { ...doc.workspace, service_paths: service.path ? [service.path] : [] } })) }}>
                  <Icon name="chevron" size={13} className={expanded.has(service.id) ? 'expanded' : ''} />
                  <span className="tree-service-icon">S</span>
                  <span><strong>{service.name}</strong><small>{service.serviceId} · {service.instanceId}</small></span>
                </button>
                {expanded.has(service.id) && (
                  <div className="tree-children">
                    {(Object.keys(childLabels) as ChildType[]).map((type) => (
                      <div className="tree-group" key={type}>
                        <span><Icon name={type === 'methods' ? 'code' : type === 'events' ? 'radio' : 'database'} size={13} />{childLabels[type]}<em>{service[type].length}</em></span>
                        {service[type].slice(0, 4).map((item) => <button key={item.id} onClick={() => setSelectedId(service.id)}><i />{item.name}<small>{item.id}</small></button>)}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
            {!filtered.length && <div className="empty-state small"><Icon name={loading ? 'refresh' : 'search'} /><strong>{loading ? '正在加载服务模型' : '未找到服务'}</strong><span>{loading ? '正在从后端恢复 ARXML 数据…' : '尝试使用服务名或十六进制 ID'}</span></div>}
          </div>
        </aside>

        <div className="model-detail">
          {selected ? (
            <>
              <section className="panel service-hero">
                <div className="service-logo">SI</div>
                <div className="service-identity"><span>Service Interface</span><h2>{selected.name}</h2><p>{selected.deploymentPath ?? selected.path ?? selected.endpoint ?? '端点由运行时动态分配'}</p></div>
                <span className="protocol-pill">SOME/IP · {selected.transport ?? '传输未配置'}</span>
              </section>
              <section className="service-facts">
                <article><span>Service ID</span><strong className="mono">{selected.serviceId}</strong><small>{selected.deployed === false ? '未部署' : protocolId(selected.serviceId) ?? '—'}</small></article>
                <article><span>Instance ID</span><strong className="mono">{selected.instanceId}</strong><small>实例标识</small></article>
                <article><span>接口版本</span><strong>v{selected.majorVersion ?? 1}.{selected.minorVersion ?? 0}</strong><small>Major / Minor</small></article>
                <article><span>传输层</span><strong>{selected.transport ?? '未配置'}</strong><small>{selected.transport === 'TCP' ? '可靠传输' : selected.transport === 'UDP' ? 'UDP 传输' : '需导入部署配置'}</small></article>
              </section>
              <DefinitionSection title="Methods" subtitle="请求 / 响应接口" icon="code" rows={selected.methods.map((item) => [item.name, item.id, item.requestType ?? '—', item.responseType ?? '—', item.reliable == null ? '未配置' : item.reliable ? 'TCP' : 'UDP'])} headers={['名称', 'Method ID', '请求类型', '响应类型', '传输']} />
              <DefinitionSection title="Events" subtitle="事件与事件组" icon="radio" rows={selected.events.map((item) => [item.name, item.id, item.eventGroup ?? '—', item.dataType ?? '—', item.cycleMs ? `${item.cycleMs} ms` : '按变化'])} headers={['名称', 'Event ID', 'Event Group', '数据类型', '周期']} />
              <DefinitionSection title="Fields" subtitle="Getter / Setter / Notifier" icon="database" rows={selected.fields.map((item) => [item.name, item.id, item.dataType ?? '—', item.getter ?? '—', item.notifier ?? '—'])} headers={['名称', 'Field ID', '数据类型', 'Getter', 'Notifier']} />
            </>
          ) : <div className="panel empty-state"><Icon name="database" size={28} /><strong>{loading ? '正在加载服务模型' : '尚未加载服务模型'}</strong><span>{loading ? '正在从后端恢复 ARXML 数据…' : '导入 ARXML 文件以开始浏览'}</span></div>}
        </div>
      </section>
    </div>
  )
}

function DefinitionSection({ title, subtitle, icon, rows, headers }: { title: string; subtitle: string; icon: 'code' | 'radio' | 'database'; rows: string[][]; headers: string[] }) {
  return (
    <section className="panel definition-section">
      <div className="panel-header"><div className="definition-title"><span><Icon name={icon} /></span><div><h3>{title}</h3><small>{subtitle}</small></div></div><em>{rows.length}</em></div>
      {rows.length ? (
        <div className="table-wrap"><table><thead><tr>{headers.map((item) => <th key={item}>{item}</th>)}</tr></thead><tbody>{rows.map((row) => <tr key={`${title}-${row[1]}`}>{row.map((cell, index) => <td key={`${cell}-${index}`} className={index === 1 ? 'mono cyan' : ''}>{cell}</td>)}</tr>)}</tbody></table></div>
      ) : <div className="empty-row">此服务没有定义 {title}</div>}
    </section>
  )
}
