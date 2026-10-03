import type {
  AgentChatResponse,
  CaptureInterface,
  HealthResponse,
  LlmSettings,
  NetworkListenerConfig,
  NativeServiceRequest,
  NativeServiceSession,
  NativeServiceCommand,
  NativeServiceRequestMessage,
  NativeEventCycle,
  NativeCycleCommand,
  NativeSyncCommand,
  NativeSyncControl,
  NativeSyncStatus,
  PcapImportResult,
  SimulationStartRequest,
  SimulationStatus,
  UpdateInfo,
  UpdateInstallationStatus,
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
import { parseJson, stringifyJson } from './json'

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
      ? parseJson(await response.text())
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
  scenarioRuns: () => request<import('../workbench/ScenarioPanel').ScenarioSummary[]>('/scenarios/runs?limit=100'),
  scenarioRun: (id: string) => request<import('../workbench/ScenarioPanel').ScenarioRun>(`/scenarios/runs/${encodeURIComponent(id)}`),
  validateScenario: (definition: unknown) => request<unknown>('/scenarios/runs/validate', { method: 'POST', body: stringifyJson(definition) }),
  startScenario: (project_id: string, definition: unknown) => request<import('../workbench/ScenarioPanel').ScenarioRun>('/scenarios/runs', { method: 'POST', body: stringifyJson({ project_id, definition }) }, 30000),
  cancelScenario: (id: string) => request<import('../workbench/ScenarioPanel').ScenarioRun>(`/scenarios/runs/${encodeURIComponent(id)}/cancel`, { method: 'POST' }, 100000),
  scenarioArtifactUrl: (id: string, kind: 'junit' | 'report' | 'evidence') => `${API_PREFIX}/scenarios/runs/${encodeURIComponent(id)}/${kind}`,
  scenarioInputs: (id: string) => request<{ project: unknown; definition: unknown }>(`/scenarios/runs/${encodeURIComponent(id)}/inputs`),
  compareScenarios: (id: string, baseline: string) => request<import('../workbench/ScenarioPanel').ScenarioComparison>(`/scenarios/runs/${encodeURIComponent(id)}/compare/${encodeURIComponent(baseline)}`, {}, 30000),
  recordings: () => request<import('../workbench/RecordingPanel').RecordingView[]>('/recordings'),
  startRecording: (config: { name: string; quota_bytes: number }) => request<import('../workbench/RecordingPanel').RecordingView>('/recordings', { method: 'POST', body: stringifyJson(config) }),
  stopRecording: (id: string) => request<import('../workbench/RecordingPanel').RecordingView>(`/recordings/${encodeURIComponent(id)}/stop`, { method: 'POST' }, 40000),
  recordingFrames: (id: string, parameters: Record<string, string>) => request<import('../workbench/replay').ReplayPage>(`/recordings/${encodeURIComponent(id)}/frames?${new URLSearchParams(parameters)}`, {}, 30000),
  recordingExportUrl: (id: string) => `${API_PREFIX}/recordings/${encodeURIComponent(id)}/export`,
  projectModel: () => request<unknown | null>('/model', {}, 30000),
  projects: () => request<import('../workbench/projects').ProjectSummary[]>('/projects'),
  project: (id: string) => request<import('../workbench/projects').ProjectView>(`/projects/${encodeURIComponent(id)}`, {}, 30000),
  currentProject: () => request<import('../workbench/projects').ProjectView | null>('/projects/current', {}, 30000),
  saveProject: (document: import('../workbench/projects').ProjectDocument, current?: import('../workbench/projects').ProjectView) => request<import('../workbench/projects').ProjectView>(current ? `/projects/${current.id}` : '/projects', {
    method: current ? 'PUT' : 'POST', body: stringifyJson({ document, ...(current ? { expected_revision: current.revision } : {}) }),
  }, 30000),
  openProject: (id: string) => request<import('../workbench/projects').ProjectView>(`/projects/${encodeURIComponent(id)}/open`, { method: 'POST' }, 30000),
  exportProject: (id: string) => request<import('../workbench/projects').ProjectDocument>(`/projects/${encodeURIComponent(id)}/export`, {}, 30000),
  importProject: (document: unknown) => request<import('../workbench/projects').ProjectView>('/projects/import', { method: 'POST', body: stringifyJson(document) }, 30000),
  validateProject: (document: unknown) => request<import('../workbench/projects').ProjectDocument>('/projects/validate', { method: 'POST', body: stringifyJson(document) }, 30000),
  projectBackups: (id: string) => request<Array<{ revision: number; updated_at: string }>>(`/projects/${encodeURIComponent(id)}/backups`),
  restoreProject: (id: string, revision: number, expected_revision: number) => request<import('../workbench/projects').ProjectView>(`/projects/${encodeURIComponent(id)}/restore`, { method: 'POST', body: stringifyJson({ revision, expected_revision }) }, 30000),
  consoleCommands: (afterId?: number) => request<{
    last_id: number; commands: Array<{ id: number; action: 'navigate'; page: import('../types').PageId }>
  }>(`/console/commands${afterId === undefined ? '' : `?after_id=${afterId}`}`),
  health: () => request<HealthResponse>('/health', {}, 3500),
  services: async () => normalizeServices(await request<RawServiceDefinition[]>('/model/services')),
  serviceSessions: () => request<NativeServiceSession[]>('/services/sessions'),
  startServiceSession: (config: NativeServiceRequest) => request<NativeServiceSession>('/services/sessions', {
    method: 'POST', body: JSON.stringify(config),
  }, 30_000),
  stopServiceSession: (id: string) => request<NativeServiceSession>(`/services/sessions/${encodeURIComponent(id)}/stop`, { method: 'POST' }, 30_000),
  serviceRequests: (id: string) => request<NativeServiceRequestMessage[]>(`/services/sessions/${encodeURIComponent(id)}/requests`),
  serviceCommand: (id: string, action: 'call' | 'notify' | 'respond', command: NativeServiceCommand) => request<{
    status: 'responded' | 'submitted'; result: unknown; observation: 'vsomeip_response' | 'native_submission'; wire_verified: false
  }>(`/services/sessions/${encodeURIComponent(id)}/${action}`, { method: 'POST', body: stringifyJson(command) }, 40_000),
  serviceCycles: (id: string) => request<NativeEventCycle[]>(`/services/sessions/${encodeURIComponent(id)}/cycles`),
  configureServiceCycle: (id: string, action: 'start' | 'update', command: NativeCycleCommand) => request<NativeEventCycle>(
    `/services/sessions/${encodeURIComponent(id)}/cycles/${action}`, { method: 'POST', body: stringifyJson(command) }, 40_000,
  ),
  stopServiceCycle: (id: string, member: string) => request<NativeEventCycle>(
    `/services/sessions/${encodeURIComponent(id)}/cycles/stop`, { method: 'POST', body: stringifyJson({ member }) }, 40_000,
  ),
  serviceSync: (id: string) => request<NativeSyncStatus>(`/services/sessions/${encodeURIComponent(id)}/sync`),
  startServiceSync: (id: string, command: NativeSyncCommand) => request<NativeSyncStatus>(
    `/services/sessions/${encodeURIComponent(id)}/sync/start`, { method: 'POST', body: stringifyJson(command) }, 40_000,
  ),
  controlServiceSync: (id: string, command: NativeSyncControl) => request<NativeSyncStatus>(
    `/services/sessions/${encodeURIComponent(id)}/sync/control`, { method: 'POST', body: stringifyJson(command) }, 40_000,
  ),
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
    body: stringifyJson(config),
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
  installUpdate: () => request<{ status: string; version: string; installation_id: string }>('/updates/install', {
    method: 'POST',
  }, 180_000),
  updateInstallationStatus: (installationId: string) => request<UpdateInstallationStatus>(
    `/updates/install/${encodeURIComponent(installationId)}`, {}, 5000,
  ),
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
