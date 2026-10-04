import type { ReactNode } from 'react'
import type { ConnectionState, HealthResponse, PageId } from '../types'
import { Icon, type IconName } from './Icon'
import { StatusBadge } from './StatusBadge'
import { useProject } from '../workbench/projects'

interface LayoutProps {
  page: PageId
  onNavigate: (page: PageId) => void
  onOpenAgent: () => void
  sidebarOpen: boolean
  onToggleSidebar: () => void
  connectionState: ConnectionState
  health: HealthResponse | null
  agentOpen: boolean
  agentPanel: ReactNode
  children: ReactNode
}

interface NavItem {
  id: PageId
  label: string
  icon: IconName
  badge?: string
}

const primaryNav: NavItem[] = [
  { id: 'dashboard', label: '系统总览', icon: 'grid' },
  { id: 'services', label: '服务模型', icon: 'database' },
  { id: 'simulation', label: '信号仿真', icon: 'play' },
  { id: 'monitor', label: '报文监控', icon: 'activity', badge: 'LIVE' },
  { id: 'pcap', label: 'PCAP 分析', icon: 'file' },
]

const pageMeta: Record<PageId, { title: string; eyebrow: string }> = {
  dashboard: { title: '系统总览', eyebrow: 'SOME/IP 智能工作台' },
  services: { title: 'ARXML 服务模型', eyebrow: '模型与接口定义' },
  simulation: { title: '信号仿真', eyebrow: '场景编排与信号注入' },
  monitor: { title: '实时监控', eyebrow: '报文、波形与诊断' },
  pcap: { title: 'PCAP 离线分析', eyebrow: '抓包解析与协议洞察' },
  settings: { title: '模型与系统设置', eyebrow: '安全配置中心' },
  projects: { title: '工程保存与加载', eyebrow: '配置、历史与迁移' },
}

export function Layout({
  page, onNavigate, onOpenAgent, sidebarOpen, onToggleSidebar, connectionState, health, agentOpen, agentPanel, children,
}: LayoutProps) {
  const meta = pageMeta[page]
  const project = useProject()
  const navigate = (id: PageId) => {
    onNavigate(id)
    if (window.innerWidth < 900) onToggleSidebar()
  }

  return (
    <div className="app-shell">
      {sidebarOpen && <button className="mobile-backdrop" aria-label="关闭菜单" onClick={onToggleSidebar} />}
      <aside className={`sidebar${sidebarOpen ? ' mobile-open' : ''}`}>
        <button className="brand" onClick={() => navigate('dashboard')}>
          <span className="brand-mark"><i /><i /><i /></span>
          <span><strong>NEXUS</strong><small>AUTOMOTIVE ETHERNET</small></span>
        </button>
        <div className="workspace-selector">
          <span className="workspace-icon"><Icon name="network" /></span>
          <span><small>配置工作区</small><strong>{project?.document.name ?? '未打开工程'}</strong></span>
        </div>
        <nav className="main-nav" aria-label="主导航">
          <p>工作台</p>
          {primaryNav.map((item) => (
            <button key={item.id} className={page === item.id ? 'active' : ''} onClick={() => navigate(item.id)}>
              <Icon name={item.icon} />
              <span>{item.label}</span>
              {item.badge && <em>{item.badge}</em>}
            </button>
          ))}
          <p className="nav-section">系统</p>
          <button className={page === 'projects' ? 'active' : ''} onClick={() => navigate('projects')}>
            <Icon name="file" /><span>工程保存与加载</span>
          </button>
          <button className={page === 'settings' ? 'active' : ''} onClick={() => navigate('settings')}>
            <Icon name="settings" /><span>模型与设置</span>
          </button>
        </nav>
        <div className="sidebar-bottom">
          <button className="agent-entry" onClick={onOpenAgent}>
            <span><Icon name="sparkles" /></span>
            <div><strong>智能诊断</strong><small>协议分析助手</small></div>
            <Icon name="chevron" size={15} />
          </button>
          <div className="version-block">
            <span>Console {health?.version ? `v${health.version}` : 'v0.1.0'}</span>
            <span className={connectionState === 'online' ? 'online' : ''}>{connectionState === 'online' ? '服务正常' : connectionState === 'connecting' ? '正在连接' : '后端离线'}</span>
          </div>
        </div>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div className="topbar-title">
            <button className="mobile-menu icon-button" onClick={onToggleSidebar} aria-label="打开菜单"><Icon name="menu" /></button>
            <div><span>{meta.eyebrow}</span><h1>{meta.title}</h1></div>
          </div>
          <div className="topbar-actions">
            <StatusBadge state={connectionState} />
            <button className="button secondary" onClick={onOpenAgent} aria-expanded={agentOpen} aria-controls="protocol-agent-dock"><Icon name="sparkles" size={16} />工程智能体</button>
          </div>
        </header>
        <div className={`workspace-body${agentOpen ? ' has-agent' : ''}`}>
          <main className="main-content">{children}</main>
          <div id="protocol-agent-dock" className="protocol-agent-dock" hidden={!agentOpen}>{agentPanel}</div>
        </div>
      </section>
    </div>
  )
}
