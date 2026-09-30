import type {
  AgentChatResponse,
  CaptureInterface,
  HealthResponse,
  LlmSettings,
  NetworkListenerConfig,
  PcapImportResult,
  SimulationStartRequest,
  SimulationStatus,
  UpdateInfo,
} from '../types'
import {
  normalizeMonitorMessage,
  normalizeNetworkListener,
  normalizePcapResult,
  normalizeServices,
  type RawMonitorMessage,
  type RawPcapImportResult,
  type RawServiceDefinition,
} from './adapters'

const API_PREFIX = '/api/v1'

export class ApiError extends Error {
  readonly status: number
  readonly detail?: unknown

  constructor(message: string, status = 0, detail?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

async function request<T>(path: string, init: RequestInit = {}, timeoutMs = 8000): Promise<T> {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), timeoutMs)

  try {
    const response = await fetch(`${API_PREFIX}${path}`, {
      ...init,
      headers: {
        Accept: 'application/json',
        ...(init.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
        ...init.headers,
      },
      signal: controller.signal,
    })

    const contentType = response.headers.get('content-type') ?? ''
    const body: unknown = contentType.includes('application/json')
      ? await response.json()
      : await response.text()

    if (!response.ok) {
      const detail = typeof body === 'object' && body !== null && 'detail' in body
        ? (body as { detail?: unknown }).detail
        : body
      throw new ApiError(`请求失败（HTTP ${response.status}）`, response.status, detail)
    }
    return body as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError('请求超时，请检查后端服务', 0, error)
    }
    throw new ApiError(error instanceof Error ? error.message : '网络连接失败', 0, error)
  } finally {
    window.clearTimeout(timer)
  }
}

function uploadFile<T>(path: string, file: File, fieldName = 'file', extra?: Record<string, string>) {
  const form = new FormData()
  form.append(fieldName, file)
  Object.entries(extra ?? {}).forEach(([key, value]) => form.append(key, value))
  return request<T>(path, { method: 'POST', body: form }, 60_000)
}

export const api = {
  health: () => request<HealthResponse>('/health', {}, 3500),
  services: async () => normalizeServices(await request<RawServiceDefinition[]>('/model/services')),
  importArxml: async (file: File) => {
    const result = await uploadFile<{ services?: RawServiceDefinition[]; warnings?: string[] }>('/arxml/import', file)
    const services = normalizeServices(result.services ?? [])
    return { services, imported: services.length, warnings: result.warnings ?? [] }
  },
  messages: async () => (await request<RawMonitorMessage[]>('/monitor/messages')).map(normalizeMonitorMessage),
  clearMessages: () => request<void>('/monitor/messages', { method: 'DELETE' }),
  networkInterfaces: () => request<CaptureInterface[]>('/network/interfaces'),
  networkListeners: async () => {
    const listeners = await request<Parameters<typeof normalizeNetworkListener>[0][]>('/network/listeners')
    return listeners.map(normalizeNetworkListener)
  },
  startNetworkListener: async (config: NetworkListenerConfig) => normalizeNetworkListener(
    await request<Parameters<typeof normalizeNetworkListener>[0]>('/network/listeners/start', {
      method: 'POST', body: JSON.stringify(config),
    }),
  ),
  stopNetworkListener: (listenerId?: string) => request<unknown[]>('/network/listeners/stop', {
    method: 'POST',
    body: JSON.stringify(listenerId ? { listener_id: listenerId } : {}),
  }),
  startSimulation: (config: SimulationStartRequest) => request<{ id: string; running: boolean }>('/simulation/start', {
    method: 'POST',
    body: JSON.stringify(config),
  }),
  simulations: () => request<SimulationStatus[]>('/simulation'),
  stopSimulation: (simulationId?: string) => request<unknown[]>('/simulation/stop', {
    method: 'POST', body: JSON.stringify(simulationId ? { simulation_id: simulationId } : {}),
  }),
  importPcap: async (file: File): Promise<PcapImportResult> => normalizePcapResult(await uploadFile<RawPcapImportResult>('/pcap/import', file)),
  agentChat: (message: string) => request<AgentChatResponse>('/agent/chat', {
    method: 'POST',
    body: JSON.stringify({ message }),
  }, 60_000),
  getLlmSettings: () => request<LlmSettings>('/settings/llm'),
  checkUpdate: () => request<UpdateInfo>('/updates/check', {}, 15_000),
  installUpdate: () => request<{ status: string; version: string }>('/updates/install', {
    method: 'POST',
  }, 180_000),
  stageUpdate: () => request<{ status: string; path: string }>('/updates/stage', {
    method: 'POST',
    body: '{}',
  }, 180_000),
  testLlmSettings: () => request<{ status: string; reply: string }>('/settings/llm/test', {
    method: 'POST',
    body: '{}',
  }, 60_000),
  updateLlmSettings: (settings: LlmSettings & { api_key?: string }) => {
    const payload = {
      base_url: settings.base_url,
      model: settings.model,
      timeout_seconds: settings.timeout_seconds,
      temperature: settings.temperature,
      ...(settings.api_key ? { api_key: settings.api_key } : {}),
    }
    return request<LlmSettings>('/settings/llm', { method: 'PUT', body: JSON.stringify(payload) })
  },
}

export function monitorWebSocketUrl(): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}${API_PREFIX}/monitor/ws`
}

export function describeApiError(error: unknown): string {
  if (error instanceof ApiError && typeof error.detail === 'string' && error.detail.trim()) return error.detail
  return error instanceof Error ? error.message : '未知错误'
}
