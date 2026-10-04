import { memo, useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { boundedAgentHistory, streamAgentChat, type AgentStreamEvent } from '../api/agentStream'
import { formatHex } from '../api/adapters'
import { describeApiError } from '../api/client'
import { stringifyJson } from '../api/json'
import { logError, logInfo } from '../api/logger'
import { isGeneratorNumber } from '../data/generatorValues'
import type { AgentMessage, AgentWorkspaceContext, ConnectionState, PageId, SimulationStartRequest } from '../types'
import { Icon } from './Icon'
import { StatusBadge } from './StatusBadge'
import './AgentPanel.css'

interface AgentPanelProps {
  open: boolean
  onClose: () => void
  connectionState: ConnectionState
  modelConfigured: boolean
  contextLabel: string
  context: AgentWorkspaceContext
  intent?: { id: string; prompt: string }
  onLoadSimulationPlan?: (config: SimulationStartRequest) => void
}

interface ToolActivity {
  id: string
  name: string
  phase: 'start' | 'result'
  result?: unknown
}

interface ScopeSnapshot {
  label: string
  detail: string
  evidence: string
}

interface ChatMessage extends AgentMessage {
  status?: 'streaming' | 'complete' | 'stopped' | 'error'
  statusText?: string
  error?: string
  model?: string
  tools?: ToolActivity[]
  scope?: ScopeSnapshot
}

const pageSuggestions: Record<PageId, { label: string; prompt: string }[]> = {
  projects: [
    { label: '工程保护说明', prompt: '请说明工程保存、加载、恢复和运行的区别；不要替我执行任何操作。' },
  ],
  dashboard: [
    { label: '工作区概况', prompt: '请结合当前服务模型、监控数据与仿真任务，概括工作区状态，并列出需要关注的证据。' },
    { label: '通信异常', prompt: '请查询当前监控证据，分析通信异常，并区分已验证事实和待验证假设。' },
    { label: '下一步检查', prompt: '基于当前工作区证据，建议下一步通信排查步骤。' },
  ],
  services: [
    { label: '解释服务接口', prompt: '请读取当前选中服务的结构，解释其 Method、Event、Field、数据类型与部署信息。' },
    { label: '核对报文', prompt: '请将当前选中服务模型与已有报文证据对照，检查 Service ID、Method/Event ID 与数据类型。' },
    { label: '准备仿真方案', prompt: '请为当前选中服务的可支持数值信号准备内部仿真方案，只生成方案供我载入工作台，不启动任务。' },
  ],
  simulation: [
    { label: '分析当前信号', prompt: '请结合当前信号与运行任务，分析已有仿真输出及异常；指出可用证据的时间和样本范围。' },
    { label: '准备仿真方案', prompt: '请为当前选中信号准备内部仿真配置，通过 prepare_simulation 生成可载入的方案，不直接启动。' },
    { label: '检查运行任务', prompt: '请读取当前仿真任务，检查运行状态、发送周期与错误信息。' },
  ],
  monitor: [
    { label: '解释选中报文', prompt: '请分析当前选中报文，解释 SOME/IP 头、负载、服务映射与异常证据；如未选中报文请明确说明。' },
    { label: '检查 SD 链路', prompt: '请分析当前范围的 SOME/IP-SD Offer、Find、Subscribe 与 Ack 证据，指出缺失环节和证据限制。' },
    { label: '分析信号变化', prompt: '请分析当前选中信号的数值变化、范围和异常，并说明样本数量及证据范围。' },
  ],
  pcap: [
    { label: '分析导入报文', prompt: '请查询 PCAP 来源的报文，概括 SOME/IP 与 SD 交互，并说明当前缓冲的证据范围。' },
    { label: '定位异常链路', prompt: '请基于 PCAP 来源报文分析异常服务、返回码和服务发现链路，不将缺少证据当作已证实故障。' },
    { label: '解释选中报文', prompt: '请结合服务模型详细分析当前选中的 PCAP 报文及负载。' },
  ],
  settings: [
    { label: '配置检查建议', prompt: '请说明当前 SOME/IP 工作区的运行配置检查要点，明确哪些状态有工具证据支持。' },
    { label: '解释证据模式', prompt: '请说明本地证据模式和配置模型后的诊断能力区别，以及当前可以读取的工作区证据。' },
  ],
}

const toolLabels: Record<string, string> = {
  list_services: '读取服务模型', get_service_schema: '读取服务接口',
  get_monitor_summary: '读取监控统计', query_messages: '检索报文证据',
  analyze_message: '分析选中报文', analyze_sd: '分析服务发现',
  analyze_signal: '分析信号变化', list_simulations: '读取仿真状态',
  prepare_simulation: '准备内部仿真方案', start_simulation: '启动内部仿真',
  stop_simulation: '停止内部仿真',
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}

function snapshotScope(context: AgentWorkspaceContext, label: string): ScopeSnapshot {
  const details = [
    context.service_id !== undefined ? 'Service ' + formatHex(context.service_id) : '',
    context.method_id !== undefined ? 'Method/Event ' + formatHex(context.method_id) : '',
    context.message_id ? '报文 ' + context.message_id : '',
    context.signal_name ? '信号 ' + context.signal_name : '',
    context.simulation_id ? '任务 ' + context.simulation_id : '',
  ].filter(Boolean)
  const source = context.source === 'pcap' ? 'PCAP 来源' : context.source === 'live' ? '当前监控缓存（可含仿真/导入）' : '当前工作区'
  const scope = context.message_id ? '选中报文及相关证据' : context.service_id !== undefined ? '选中服务的可用证据' : '后端当前模型与缓冲'
  return {
    label,
    detail: details.join(' · ') || '未指定单个服务或报文',
    evidence: source + ' · ' + scope + (context.frozen ? ' · 冻结仅作用于显示' : ''),
  }
}

function welcomeMessage(): ChatMessage {
  return {
    id: 'welcome', role: 'assistant', createdAt: new Date(),
    content: `选中工作台中的**服务、报文或信号**，我可以结合对应证据帮助排查。

快捷任务会先填入输入框；发送时携带当前范围和本会话最近的已完成对话。`,
  }
}

function toolSummary(tool: ToolActivity): string {
  if (tool.phase === 'start') return '正在读取工作区证据…'
  const result = record(tool.result)
  if (result) {
    if (typeof result.error === 'string') return result.error
    for (const key of ['summary', 'message', 'description']) {
      if (typeof result[key] === 'string') return (result[key] as string).slice(0, 240)
    }
    const collections = [['services', '个服务'], ['messages', '条报文'], ['simulations', '个任务'], ['samples', '个样本']] as const
    for (const [key, label] of collections) {
      if (Array.isArray(result[key])) return '已读取 ' + result[key].length + ' ' + label
    }
    if (typeof result.count === 'number') return '读取到 ' + result.count + ' 项证据'
    if (tool.name === 'prepare_simulation' && result.simulation_config) return '已准备配置，载入工作台后可检查并手动启动。'
  }
  return Array.isArray(tool.result) ? '已读取 ' + tool.result.length + ' 项结果' : '读取完成，可展开查看原始证据。'
}

function simulationPlan(tool: ToolActivity): SimulationStartRequest | null {
  if (tool.name !== 'prepare_simulation' || tool.phase !== 'result') return null
  const value = record(record(tool.result)?.simulation_config)
  const generator = record(value?.generator)
  if (!value || value.transport !== 'internal' || !generator || typeof value.name !== 'string'
    || typeof value.destination_host !== 'string'
    || !['service_id', 'instance_id', 'method_id', 'interface_version', 'interval_ms', 'destination_port'].every((key) => typeof value[key] === 'number' && Number.isFinite(value[key]))
    || typeof generator.signal_name !== 'string'
    || !['constant', 'sine', 'ramp', 'random', 'sequence', 'step'].includes(String(generator.kind))
    || !['boolean', 'uint8', 'uint16', 'uint32', 'uint64', 'int8', 'int16', 'int32', 'int64', 'float32', 'float64'].includes(String(generator.data_type))
    || !['minimum', 'maximum', 'initial'].every(key => isGeneratorNumber(generator[key]))
    || typeof generator.period_seconds !== 'number' || !Number.isFinite(generator.period_seconds)
    || !Array.isArray(generator.sequence) || !generator.sequence.every(isGeneratorNumber)) return null
  return value as unknown as SimulationStartRequest
}

const MarkdownMessage = memo(function MarkdownMessage({ content, user }: { content: string; user: boolean }) {
  return <div className={'agent-markdown' + (user ? ' user-content' : '')}>
    <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{
      a: ({ children, href, title }) => <a href={href} title={title} target="_blank" rel="noopener noreferrer">{children}</a>,
      img: ({ alt }) => <span className="agent-image-description">[图片：{alt || '未加载远程图片'}]</span>,
      table: ({ children }) => <div className="agent-markdown-table"><table>{children}</table></div>,
    }}>{content}</ReactMarkdown>
  </div>
})

const ToolDetails = memo(function ToolDetails({ tool, onLoadSimulationPlan }: { tool: ToolActivity; onLoadSimulationPlan?: (config: SimulationStartRequest) => void }) {
  const [loaded, setLoaded] = useState(false)
  const result = useMemo(() => {
    const text = stringifyJson(tool.result, 2)
    return text.slice(0, 12_000) + (text.length > 12_000 ? '\n…（结果已截断）' : '')
  }, [tool.result])
  const plan = simulationPlan(tool)
  const failed = Boolean(record(tool.result)?.error)
  return <article className={'agent-tool-card' + (failed ? ' is-error' : '')}>
    <div className="agent-tool-heading"><Icon name={tool.phase === 'start' ? 'clock' : failed ? 'info' : 'check'} size={13} /><strong>{toolLabels[tool.name] ?? tool.name}</strong><small>{tool.phase === 'start' ? '进行中' : failed ? '未成功' : '已完成'}</small></div>
    <div className="agent-tool-summary">{toolSummary(tool)}</div>
    {plan && onLoadSimulationPlan && <button className="agent-load-plan" onClick={() => {
      onLoadSimulationPlan(plan)
      setLoaded(true)
      logInfo('载入智能体生成的内部仿真方案', { serviceId: plan.service_id, methodId: plan.method_id })
    }}><Icon name={loaded ? 'check' : 'layers'} size={13} />{loaded ? '已载入 · 再次载入' : '载入仿真工作台'}</button>}
    {tool.phase === 'result' && <details className="agent-tool-item"><summary>查看原始证据</summary><pre>{result}</pre></details>}
  </article>
})

export function AgentPanel({ open, onClose, connectionState, modelConfigured, contextLabel, context, intent, onLoadSimulationPlan }: AgentPanelProps) {
  const [messages, setMessages] = useState<ChatMessage[]>(() => [welcomeMessage()])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [allowMutation, setAllowMutation] = useState(false)
  const [following, setFollowing] = useState(true)
  const scrollRef = useRef<HTMLDivElement>(null)
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const followRef = useRef(true)
  const controllerRef = useRef<AbortController | null>(null)
  const handledIntentRef = useRef<string | null>(null)
  const currentScope = snapshotScope(context, contextLabel)
  const suggestions = pageSuggestions[context.page] ?? pageSuggestions.dashboard
  const disconnected = connectionState !== 'online'

  useEffect(() => () => controllerRef.current?.abort(), [])
  useEffect(() => {
    if (intent && intent.id !== handledIntentRef.current) {
      handledIntentRef.current = intent.id
      setInput(intent.prompt)
      if (open) composerRef.current?.focus()
    }
  }, [intent, open])
  useEffect(() => {
    if (open && followRef.current && scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight
  }, [messages, open])

  const updateMessage = (id: string, update: (message: ChatMessage) => ChatMessage) => {
    setMessages((current) => current.map((message) => message.id === id ? update(message) : message))
  }

  const send = async () => {
    const content = input.trim()
    if (!content || controllerRef.current || disconnected) return
    const answerId = crypto.randomUUID()
    const controller = new AbortController()
    const requestAllowsMutation = allowMutation
    const requestContext = { ...context }
    const requestScope = snapshotScope(requestContext, contextLabel)
    const history = boundedAgentHistory(messages
      .filter((message) => message.id !== 'welcome' && message.status === 'complete' && message.content.trim())
      .map((message) => ({ role: message.role, content: message.content })))
    setAllowMutation(false)
    controllerRef.current = controller
    followRef.current = true
    setFollowing(true)
    setMessages((current) => [...current,
      { id: crypto.randomUUID(), role: 'user', content, createdAt: new Date(), status: 'complete', scope: requestScope },
      { id: answerId, role: 'assistant', content: '', createdAt: new Date(), status: 'streaming', statusText: '正在连接智能体…', tools: [], scope: requestScope },
    ])
    setInput('')
    setSending(true)
    let pendingText = ''
    let frame = 0
    const flush = () => {
      if (frame) cancelAnimationFrame(frame)
      frame = 0
      if (!pendingText) return
      const text = pendingText
      pendingText = ''
      updateMessage(answerId, (message) => ({ ...message, content: message.content + text }))
    }
    const onEvent = (event: AgentStreamEvent) => {
      if (event.event === 'delta') {
        pendingText += event.data.text
        if (!frame) frame = requestAnimationFrame(flush)
      } else if (event.event === 'status') {
        updateMessage(answerId, (message) => ({ ...message, statusText: event.data.message }))
      } else if (event.event === 'tool') {
        const activity = event.data
        updateMessage(answerId, (message) => ({
          ...message,
          statusText: activity.phase === 'start' ? (toolLabels[activity.name] ?? activity.name) + '…' : '正在整理证据…',
          tools: [...(message.tools ?? []).filter((tool) => tool.id !== activity.id), activity],
        }))
      } else if (event.event === 'done' && event.data.status === 'complete') {
        flush()
        const { model, degraded } = event.data
        updateMessage(answerId, (message) => ({
          ...message, status: 'complete', model,
          statusText: degraded ? '本地证据结果 · 未调用大模型' : '回答完成',
        }))
      }
    }

    try {
      logInfo('智能体流式请求开始', { context: requestContext, historyMessages: history.length, allowMutation: requestAllowsMutation })
      await streamAgentChat(content, { signal: controller.signal, allowMutation: requestAllowsMutation, context: requestContext, history, onEvent })
      logInfo('智能体流式响应完成', { context: requestContext })
    } catch (error) {
      flush()
      if (controller.signal.aborted) {
        updateMessage(answerId, (message) => ({ ...message, status: 'stopped', statusText: '已停止生成 · 已保留收到的内容' }))
        logInfo('用户停止智能体生成', { context: requestContext })
      } else {
        logError('智能体流式请求失败', error, { context: requestContext })
        updateMessage(answerId, (message) => ({ ...message, status: 'error', error: describeApiError(error), statusText: '生成失败' }))
      }
    } finally {
      flush()
      controllerRef.current = null
      setSending(false)
    }
  }

  const fillComposer = (prompt: string) => {
    setInput(prompt)
    composerRef.current?.focus()
  }

  return (
    <aside className={'agent-panel agent-stream-panel' + (open ? ' open' : '')} hidden={!open} aria-label="工作区诊断智能体">
      <div className="agent-header">
        <div className="agent-title-wrap"><span className="agent-avatar"><Icon name="sparkles" size={18} /></span><div><strong>Pi 诊断智能体</strong><span>内置运行时 · 无需插件或 Skills</span></div></div>
        <div className="agent-header-actions"><button className="icon-button" disabled={sending} onClick={() => {
          setMessages([welcomeMessage()]); setInput(''); setAllowMutation(false); followRef.current = true; setFollowing(true)
          logInfo('创建新的智能体会话')
        }} title="新会话" aria-label="新建智能体会话"><Icon name="refresh" size={15} /></button><button className="icon-button" onClick={onClose} aria-label="关闭智能体"><Icon name="x" size={16} /></button></div>
      </div>
      <div className="agent-context">
        <StatusBadge state={connectionState} onlineText={modelConfigured ? '模型网关已配置' : '后端在线 · 本地证据模式'} offlineText="后端离线 · 请启动后端" compact />
        <div className="agent-scope-heading"><strong>{currentScope.label}</strong><span>发送时附带</span></div>
        <div className="agent-scope-detail" title={currentScope.detail}>{currentScope.detail}</div>
        <div className="agent-scope-evidence" title={currentScope.evidence}>{currentScope.evidence}</div>
      </div>

      <div className="agent-messages" ref={scrollRef} onScroll={() => {
        const view = scrollRef.current
        if (!view) return
        followRef.current = view.scrollHeight - view.scrollTop - view.clientHeight < 70
        setFollowing(followRef.current)
      }}>
        {messages.map((message) => (
          <div key={message.id} className={'agent-message ' + message.role}>
            {message.role === 'assistant' && <span className="message-avatar"><Icon name="bot" size={15} /></span>}
            <div className="agent-message-body">
              {message.scope && message.role === 'assistant' && <div className="agent-answer-scope" title={message.scope.label + '\n' + message.scope.detail + '\n' + message.scope.evidence}><Icon name="layers" size={12} /><span>{message.scope.label} · {message.scope.detail}</span></div>}
              {Boolean(message.tools?.length) && <div className="agent-tool-list">{message.tools?.map((tool) => <ToolDetails key={tool.id} tool={tool} onLoadSimulationPlan={onLoadSimulationPlan} />)}</div>}
              {message.content && <MarkdownMessage content={message.content} user={message.role === 'user'} />}
              {message.status === 'streaming' && <div className="agent-generation-status" role="status"><span className="agent-stream-dot" />{message.statusText}</div>}
              {message.error && <div className="agent-stream-error" role="alert"><Icon name="info" size={14} /><span>{message.error}</span></div>}
              {message.role === 'assistant' && message.status && message.status !== 'streaming' && <div className={'agent-generation-status ' + message.status}>{message.statusText}</div>}
              <time>{message.createdAt.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}{message.model ? ' · ' + message.model : ''}</time>
            </div>
          </div>
        ))}
      </div>
      {!following && <button className="agent-jump-latest" onClick={() => {
        followRef.current = true; setFollowing(true)
        if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight
      }}><Icon name="arrowDown" size={14} />回到最新回答</button>}
      <div className="agent-suggestions" aria-label="当前页面快捷任务">{suggestions.map((item) => <button key={item.label} disabled={sending} title={item.prompt} onClick={() => fillComposer(item.prompt)}>{item.label}</button>)}</div>
      <label className="agent-mutation-choice" title="授权本次启停、RPC/通知、回复和清空缓存；网络发送仍受白名单限制"><input type="checkbox" checked={allowMutation} disabled={sending || disconnected} onChange={(event) => setAllowMutation(event.target.checked)} />允许本次控制台写操作</label>
      <div className="agent-composer">
        <textarea ref={composerRef} value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => {
          if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send() }
        }} placeholder={disconnected ? '后端连接恢复后可发送；可以先编辑问题…' : '输入问题，或从工作台选择诊断对象…'} aria-label="智能体消息" rows={2} maxLength={12000} />
        {sending
          ? <button className="agent-stop-button" onClick={() => controllerRef.current?.abort()} aria-label="停止生成"><Icon name="stop" size={14} />停止</button>
          : <button className="send-button" onClick={() => void send()} disabled={!input.trim() || disconnected} aria-label="发送"><Icon name="send" size={17} /></button>}
      </div>
      <p className="agent-disclaimer">{disconnected ? '后端未连接，当前不会发送请求' : 'Enter 发送 · Shift + Enter 换行 · 最近 12 条完成对话'}</p>
    </aside>
  )
}
