import type { AgentHistoryItem, AgentWorkspaceContext } from '../types'
import { parseJson } from './json'

export type AgentHistoryMessage = AgentHistoryItem

const MAX_HISTORY_MESSAGES = 12
const MAX_HISTORY_ITEM_LENGTH = 6000
const MAX_HISTORY_TOTAL_LENGTH = 24_000
const CONTEXT_PAGES = new Set(['dashboard', 'services', 'simulation', 'monitor', 'pcap', 'settings'])

function truncateText(value: string, limit: number): string {
  const truncated = value.slice(0, limit)
  // 不在 UTF-16 代理对中间截断；长度限制比后端 Unicode 字符计数更保守。
  return /[\uD800-\uDBFF]$/.test(truncated) ? truncated.slice(0, -1) : truncated
}

/** 保留最近完成的对话，同时满足后端单条、条数与总长度限制。 */
export function boundedAgentHistory(history: AgentHistoryMessage[]): AgentHistoryMessage[] {
  const candidates = history.filter((item) => (item.role === 'user' || item.role === 'assistant') && typeof item.content === 'string' && item.content.trim()).slice(-MAX_HISTORY_MESSAGES)
  const bounded: AgentHistoryMessage[] = []
  let remaining = MAX_HISTORY_TOTAL_LENGTH
  for (let index = candidates.length - 1; index >= 0 && remaining > 0; index -= 1) {
    const item = candidates[index]
    const content = truncateText(item.content, Math.min(MAX_HISTORY_ITEM_LENGTH, remaining))
    if (content.trim()) bounded.unshift({ role: item.role, content })
    remaining -= content.length
  }
  return bounded
}

function validatedContext(context: AgentWorkspaceContext): AgentWorkspaceContext {
  if (!CONTEXT_PAGES.has(context.page)) throw new Error('智能体工作区页面无效，请重新选择诊断对象')
  const result: AgentWorkspaceContext = { page: context.page }
  for (const key of ['service_id', 'method_id'] as const) {
    const value = context[key]
    if (value === undefined) continue
    if (!Number.isInteger(value) || value < 0 || value > 0xffff) throw new Error('智能体上下文的 ' + key + ' 必须是 0–65535 的整数')
    result[key] = value
  }
  for (const [key, limit] of [['message_id', 128], ['simulation_id', 128], ['signal_name', 256]] as const) {
    const value = context[key]
    if (value === undefined) continue
    if (typeof value !== 'string' || value.length > limit) throw new Error('智能体上下文的 ' + key + ' 超出长度限制')
    if (value) result[key] = value
  }
  if (context.source !== undefined) {
    if (context.source !== 'live' && context.source !== 'pcap') throw new Error('智能体上下文数据来源无效')
    result.source = context.source
  }
  if (context.frozen !== undefined) {
    if (typeof context.frozen !== 'boolean') throw new Error('智能体上下文冻结状态无效')
    result.frozen = context.frozen
  }
  return result
}

export type AgentStreamEvent =
  | { event: 'status'; data: { phase: string; message: string; round?: number } }
  | { event: 'delta'; data: { text: string } }
  | { event: 'tool'; data: { phase: 'start' | 'result'; id: string; name: string; arguments?: unknown; result?: unknown } }
  | { event: 'error'; data: { message: string } }
  | { event: 'done'; data: { status: 'complete' | 'error'; model?: string; degraded?: boolean } }

/** 读取真正的服务端 SSE。收到 done 才算完成，不能把网络断开当作成功。 */
export async function streamAgentChat(
  message: string,
  options: {
    signal: AbortSignal
    allowMutation: boolean
    onEvent: (event: AgentStreamEvent) => void
    context?: AgentWorkspaceContext
    history?: AgentHistoryMessage[]
  },
): Promise<void> {
  const response = await fetch('/api/v1/agent/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify({
      message,
      allow_mutation: options.allowMutation,
      ...(options.context ? { context: validatedContext(options.context) } : {}),
      ...(options.history ? { history: boundedAgentHistory(options.history) } : {}),
    }),
    signal: options.signal,
  })
  if (!response.ok) throw new Error(`智能体请求失败（HTTP ${response.status}），请检查后端服务`)
  if (!response.headers.get('content-type')?.includes('text/event-stream') || !response.body) {
    throw new Error('后端未返回流式响应，请确认前后端已更新并重启')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let eventName = ''
  let dataLines: string[] = []
  let completed = false
  let streamError: string | undefined

  const dispatch = () => {
    if (!dataLines.length) { eventName = ''; return }
    const data: unknown = parseJson(dataLines.join('\n'))
    dataLines = []
    if (!data || typeof data !== 'object') throw new Error('智能体流式事件格式无效')
    if (['status', 'delta', 'tool', 'error', 'done'].includes(eventName)) {
      const event = { event: eventName, data } as AgentStreamEvent
      if (event.event === 'delta' && typeof event.data.text !== 'string') throw new Error('智能体文本增量格式无效')
      if (event.event === 'error') streamError = event.data.message || '智能体生成失败'
      if (event.event === 'done') {
        completed = true
        if (event.data.status !== 'complete') streamError ??= '智能体生成未完成'
      }
      options.onEvent(event)
    }
    eventName = ''
  }

  const consume = () => {
    let lineEnd = buffer.indexOf('\n')
    while (lineEnd !== -1) {
      const line = buffer.slice(0, lineEnd).replace(/\r$/, '')
      buffer = buffer.slice(lineEnd + 1)
      if (!line) dispatch()
      else if (line.startsWith('event:')) eventName = line.slice(6).trim()
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''))
      if (completed) break
      lineEnd = buffer.indexOf('\n')
    }
  }

  try {
    while (!completed) {
      const { value, done } = await reader.read()
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true })
      consume()
      if (buffer.length > 2_097_152) throw new Error('智能体流式事件超过大小限制')
      if (done) break
    }
    if (streamError) throw new Error(streamError)
    if (!completed) throw new Error('连接提前中断，已收到的回答已保留，请重试')
  } finally {
    try { await reader.cancel() } finally { reader.releaseLock() }
  }
}
