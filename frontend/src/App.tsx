import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from './api/client'
import { logError, logInfo } from './api/logger'
import { AgentPanel } from './components/AgentPanel'
import { Layout } from './components/Layout'
import './components/Workspace.css'
import { AgentWorkspace } from './agent/workspace'
import { useBackendStatus } from './hooks/useBackendStatus'
import { useMonitorStream } from './hooks/useMonitorStream'
import { DashboardPage } from './pages/DashboardPage'
import { MonitorPage } from './pages/MonitorPage'
import { PcapPage } from './pages/PcapPage'
import { ServicesPage } from './pages/ServicesPage'
import { SettingsPage } from './pages/SettingsPage'
import { SimulationPage } from './pages/SimulationPage'
import type { AgentWorkspaceContext, PageId, ServiceDefinition, SimulationStartRequest } from './types'

const pages: PageId[] = ['dashboard', 'services', 'simulation', 'monitor', 'pcap', 'settings']

function pageFromHash(): PageId {
  const value = window.location.hash.replace('#/', '') as PageId
  return pages.includes(value) ? value : 'dashboard'
}

export default function App() {
  const [page, setPage] = useState<PageId>(pageFromHash)
  const [services, setServices] = useState<ServiceDefinition[]>([])
  const [servicesLoading, setServicesLoading] = useState(true)
  const [serviceSource, setServiceSource] = useState<'live' | 'demo'>('live')
  const [agentOpen, setAgentOpen] = useState(false)
  const [agentIntent, setAgentIntent] = useState<{ id: string; prompt: string }>()
  const [agentScope, setAgentScope] = useState<AgentWorkspaceContext>({ page: 'dashboard', source: 'live' })
  const [simulationDraft, setSimulationDraft] = useState<{ id: string; config: SimulationStartRequest }>()
  const [modelError, setModelError] = useState<string | null>(null)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const { state: connectionState, health } = useBackendStatus()
  const { messages, samples, streamState, source, clear } = useMonitorStream(true)
  const expectedServiceCount = health?.service_count
  const setScope = useCallback((next: AgentWorkspaceContext) => {
    setAgentScope((previous) => JSON.stringify(previous) === JSON.stringify(next) ? previous : next)
  }, [])
  const openAgent = useCallback((prompt?: string) => {
    if (prompt) setAgentIntent({ id: crypto.randomUUID(), prompt })
    setAgentOpen(true)
    if (window.innerWidth < 1180) window.requestAnimationFrame(() => document.getElementById('protocol-agent-dock')?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
  }, [])
  const agentWorkspace = useMemo(() => ({ setScope, openAgent }), [setScope, openAgent])
  const activeContext: AgentWorkspaceContext = agentScope.page === page ? agentScope : { page, source: page === 'pcap' ? 'pcap' : 'live' }

  useEffect(() => {
    const onHashChange = () => setPage(pageFromHash())
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  useEffect(() => {
    if (connectionState === 'connecting') return
    if (connectionState === 'offline') {
      setServicesLoading(false)
      setModelError('后端离线，已保留此前加载的模型；请恢复后端连接。')
      return
    }
    setServicesLoading(true)
    let active = true
    let retryTimer: number | undefined
    let retryCount = 0
    const maxRetries = 8
    const scheduleRetry = () => {
      retryCount += 1
      const delayMs = Math.min(500 * (2 ** (retryCount - 1)), 5_000)
      retryTimer = window.setTimeout(() => void loadServices(), delayMs)
      return delayMs
    }
    async function loadServices() {
      try {
        const items = await api.services()
        if (!active) return

        if (expectedServiceCount && items.length !== expectedServiceCount && retryCount < maxRetries) {
          const delayMs = scheduleRetry()
          logInfo('服务模型与健康状态尚未同步，稍后重试', {
            expected: expectedServiceCount,
            actual: items.length,
            retry: retryCount,
            delayMs,
          })
          return
        }

        setServices(items)
        setServicesLoading(false)
        setServiceSource('live')
        setModelError(null)
        logInfo('服务模型加载完成', { count: items.length })
      } catch (error) {
        if (active && retryCount < maxRetries) {
          const delayMs = scheduleRetry()
          logError('服务模型加载失败，等待后端恢复后重试', error, { retry: retryCount, delayMs })
          return
        }
        if (active) {
          setServicesLoading(false)
          setModelError('服务模型读取失败，已保留此前加载的模型；请检查后端日志。')
        }
        logError('服务模型加载失败，不替换为演示模型', error)
      }
    }
    void loadServices()
    return () => {
      active = false
      if (retryTimer !== undefined) window.clearTimeout(retryTimer)
    }
  }, [connectionState, expectedServiceCount])

  const navigate = (next: PageId) => {
    setPage(next)
    window.history.pushState(null, '', `#/${next}`)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const content = (() => {
    switch (page) {
      case 'services': return <ServicesPage services={services} loading={servicesLoading} source={serviceSource} onServicesChange={(items) => { setServices(items); setServicesLoading(false); setServiceSource('live'); setModelError(null) }} />
      case 'simulation': return <SimulationPage services={services} samples={samples} draft={simulationDraft} onDismissDraft={() => setSimulationDraft(undefined)} loading={servicesLoading} />
      case 'monitor': return <MonitorPage messages={messages} samples={samples} streamState={streamState} source={source} onClear={clear} />
      case 'pcap': return <PcapPage />
      case 'settings': return <SettingsPage />
      case 'dashboard':
      default: return <DashboardPage services={services} modelLoading={servicesLoading} streamState={streamState} messages={messages} samples={samples} source={source} modelSource={serviceSource} onNavigate={navigate} onOpenAgent={() => openAgent()} />
    }
  })()

  return (
    <AgentWorkspace.Provider value={agentWorkspace}>
      <Layout
        page={page}
        onNavigate={navigate}
        onOpenAgent={() => openAgent()}
        sidebarOpen={sidebarOpen}
        onToggleSidebar={() => setSidebarOpen((value) => !value)}
        connectionState={connectionState}
        health={health}
        agentOpen={agentOpen}
        agentPanel={<AgentPanel open={agentOpen} onClose={() => setAgentOpen(false)} connectionState={connectionState} modelConfigured={health?.llm_configured === true} contextLabel={{ dashboard: '工作区总览', services: 'ARXML 服务模型', simulation: '信号仿真', monitor: '报文监控', pcap: '离线抓包', settings: '模型设置' }[page]} context={activeContext} intent={agentIntent} onLoadSimulationPlan={(config) => {
          setSimulationDraft({ id: crypto.randomUUID(), config })
          navigate('simulation')
          logInfo('智能体仿真草案载入工作台，尚未执行', { serviceId: config.service_id, methodId: config.method_id })
        }} />}
      >
        {modelError && <div className="inline-notice error" role="alert">{modelError}</div>}
        {content}
      </Layout>
    </AgentWorkspace.Provider>
  )
}
