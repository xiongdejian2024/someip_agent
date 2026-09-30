export type PageId =
  | 'dashboard'
  | 'services'
  | 'simulation'
  | 'monitor'
  | 'pcap'
  | 'settings'

export type ConnectionState = 'online' | 'offline' | 'connecting'

export interface HealthResponse {
  status: string
  version?: string
  llm_configured?: boolean
  uptime_seconds?: number
  arxml_loaded?: boolean
  service_count?: number
  monitor_count?: number
  active_simulations?: number
  active_listeners?: number
  services?: Record<string, string>
}

export interface ServiceMethod {
  id: string
  name: string
  requestType?: string
  responseType?: string
  reliable?: boolean
  inputSignals?: ServiceSignal[]
  outputSignals?: ServiceSignal[]
}

export interface ServiceSignal {
  name: string
  dataType: string
  unit?: string
  minimum?: number
  maximum?: number
  byteOrder?: 'big' | 'little'
  factor?: number
  offset?: number
}

export interface ServiceEvent {
  id: string
  name: string
  deployed?: boolean
  eventGroup?: string
  cycleMs?: number
  dataType?: string
  signals?: ServiceSignal[]
}

export interface ServiceField {
  id: string
  name: string
  dataType?: string
  notifier?: string
  getter?: string
  setter?: string
  signal?: ServiceSignal
}

export interface ServiceDefinition {
  id: string
  name: string
  serviceId: string
  instanceId: string
  instanceIds?: number[]
  deployed?: boolean
  majorVersion?: number
  minorVersion?: number
  transport?: 'UDP' | 'TCP' | string
  endpoint?: string
  methods: ServiceMethod[]
  events: ServiceEvent[]
  fields: ServiceField[]
}

export interface MonitorMessage {
  id: string
  timestamp: string
  direction: 'RX' | 'TX'
  source: string
  destination: string
  protocol: 'SOME/IP' | 'SOME/IP-SD' | string
  serviceId: string
  methodId: string
  messageType: string
  length: number
  status?: string
  payload?: string
  latencyMs?: number
  signalValues?: Record<string, number | string | boolean>
  origin?: 'rx' | 'tx' | 'sim' | 'pcap'
}

export interface NetworkListener {
  id: string
  name: string
  transport: 'udp' | 'tcp'
  bind_host: string
  port: number
  multicast_group?: string | null
  interface_ip?: string | null
  status?: 'running' | 'stopped' | string
  running?: boolean
  message_count?: number
  started_at?: string
}

export interface NetworkListenerConfig {
  name: string
  transport: 'udp' | 'tcp'
  bind_host: string
  port: number
  multicast_group?: string
  interface_ip: string
}

export interface WaveSample {
  time: number
  values: Record<string, number>
}

export interface SimulationConfig {
  destinationHost: string
  destinationPort: number
  cycleMs: number
  multiplier: number
  periodSeconds: number
  enableSd: boolean
  autoRespond: boolean
  selectedServices: string[]
}

export interface SimulationStartRequest {
  name: string
  service_id: number
  instance_id: number
  method_id: number
  interface_version: number
  interval_ms: number
  transport: 'internal' | 'udp'
  enable_sd?: boolean
  destination_host: string
  destination_port: number
  generator: {
    signal_name: string
    kind: 'constant' | 'sine' | 'ramp' | 'random' | 'sequence'
    data_type: 'boolean' | 'uint8' | 'uint16' | 'uint32' | 'uint64' | 'int8' | 'int16' | 'int32' | 'int64' | 'float32' | 'float64'
    minimum: number
    maximum: number
    initial: number
    period_seconds: number
    sequence: number[]
  }
}

export interface SimulationStatus {
  id: string
  running: boolean
  config: SimulationStartRequest
  emitted_count?: number
  last_error?: string | null
}

export interface PcapEndpointStat {
  endpoint: string
  ip_version: 4 | 6
  is_multicast: boolean
  packet_count: number
  sent_count: number
  received_count: number
  someip_count: number
  transport_counts: Record<string, number>
  offered_service_ids: number[]
}

export interface PcapImportResult {
  file_name: string
  packet_count: number
  captured_bytes: number
  someip_count: number
  someip_packet_count: number
  sd_count: number
  sd_packet_count: number
  skipped_count: number
  duration_seconds: number
  start_time: string | null
  end_time: string | null
  endpoint_count: number
  top_endpoints: PcapEndpointStat[]
  transport_counts: Record<string, number>
  protocol_counts: Record<string, number>
  sd_entry_counts: Record<string, number>
  warnings: string[]
}

export interface AgentMessage {
  id: string
  role: 'assistant' | 'user'
  content: string
  createdAt: Date
}

/** 仅发送工作台对象引用，实际工程证据由后端重新读取。 */
export interface AgentWorkspaceContext {
  page: PageId
  service_id?: number
  method_id?: number
  message_id?: string
  simulation_id?: string
  signal_name?: string
  source?: 'live' | 'pcap'
  frozen?: boolean
}

export interface AgentHistoryItem {
  role: 'user' | 'assistant'
  content: string
}

export interface AgentChatResponse {
  answer?: string
  message?: string
  content?: string
  suggestions?: string[]
}

export interface LlmSettings {
  base_url: string
  model: string
  api_key_configured?: boolean
  timeout_seconds?: number
  temperature?: number
}

export interface UpdateInfo {
  current_version: string
  latest_version?: string | null
  available: boolean
  release_notes?: string | null
  download_url?: string | null
  sha256?: string | null
  signature_verified: boolean
}

export const LLM_MODELS = [
  'qwen3.5-plus',
  'deepseek-v4-pro',
  'deepseek-v4-flash',
  'glm-5.2',
  'deepseek-v4.1-flash',
] as const

export const DEFAULT_LLM_BASE_URL = 'https://voyahgpt-gateway.voyah.cn/api/gateway/v1'
