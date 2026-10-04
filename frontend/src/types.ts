export type JsonValue = null | boolean | number | bigint | string | JsonValue[] | { [key: string]: JsonValue }

export type PageId =
  | 'dashboard'
  | 'services'
  | 'simulation'
  | 'monitor'
  | 'pcap'
  | 'settings'
  | 'projects'
  | 'network'

export type ConnectionState = 'online' | 'offline' | 'connecting'

export interface NetworkProfile {
  name: string
  parent: string
  interface: string
  vlan_id: number | null
  ipv4: string
  mtu: number | null
  sd_multicast: string | null
}

export interface MonitorStreamCounters {
  backend_epoch: string
  counter_scope: 'backend_process_lifetime'
  published_total: number
  retained_messages: number
  history_evicted_total: number
  cleared_total: number
  subscriber_discarded_total: number
  current_subscriber_discarded: number | null
  active_subscribers: number
}

export interface MonitorBufferCounters {
  trace_evicted_total: number
  sample_evicted_total: number
}

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
  minimum?: number | bigint
  maximum?: number | bigint
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
  path?: string
  deploymentPath?: string
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

export interface NativeServiceRequest {
  application_name: string
  application_id: number
  members: Record<string, {
    service: string
    deployment_path?: string
    role: 'client' | 'server'
    transport: 'internal' | 'udp' | 'tcp'
    instance_id: number
    byte_order?: 'big' | 'little'
    peer_host?: string
    port?: number
    application_name?: string
    application_id?: number
  }>
}

export interface NativeEventCycle {
  member: string
  function: string | null
  interval_ms: number | null
  running: boolean
  emitted_count: number
  source_count: number
  active_states?: Record<string, string>
  logical_seconds: number
  observation: 'native_schedule'
  wire_verified: false
  synchronized?: boolean
}

export interface NativeEventSource {
  path: string
  generator: {
    kind?: 'constant' | 'sine' | 'ramp' | 'random' | 'sequence' | 'step' | 'state_machine'
    initial?: number | bigint | boolean | string
    minimum?: number | bigint
    maximum?: number | bigint
    period_seconds?: number
    sequence?: Array<number | bigint | boolean | string>
    seed?: number | bigint
    step_at_ms?: number | bigint | null
    step_value?: number | bigint | boolean | string | null
    initial_state?: string | null
    states?: TimedSignalState[]
  }
}

export interface TimedSignalState {
  name: string
  value: number | bigint | boolean | string
  duration_ms?: number | bigint | null
  next?: string | null
}

export type NativeCycleCommand = NativeServiceCommand & { interval_ms: number; sources?: NativeEventSource[]; csv_text?: string | null }

export interface NativeSyncCommand {
  events: NativeCycleCommand[]
  paused?: boolean
  speed?: number
}

export interface NativeSyncStatus {
  active: boolean
  paused: boolean
  group_id: string | null
  logical_ms: number | bigint
  frame_index: number | bigint
  interval_ms: number | null
  speed: number
  events: Array<{ member: string; function: string; interval_ms: number; emitted_count: number | bigint;
    last_logical_ms: number | bigint | null; source_count: number; active_states: Record<string, string> }>
  last_error: string | null
  observation: 'native_schedule'
  wire_verified: false
}

export type NativeSyncControl = { action: 'pause' | 'resume' | 'step' | 'stop' } | { action: 'speed'; speed: number }

export interface NativeServiceSession {
  id: string
  runtime: 'vsomeip'
  model_id: string
  source_sha256: string | null
  application_name: string
  application_id: number
  started_at: string
  active: boolean
  running: boolean
  pid: number | null
  last_error: string | null
  members: Array<{
    key: string
    application_name: string | null
    application_id: number | null
    role: 'client' | 'server'
    service_path: string
    deployment_path: string | null
    service_id: number
    instance_id: number
    transport: 'internal' | 'udp' | 'tcp'
    state: string
    connected: boolean
    methods: string[]
    no_return_methods: string[]
    events: string[]
    last_error: string | null
  }>
}

export interface NativeServiceCommand {
  member: string
  function: string
  args: unknown
  timeout?: number
  request_id?: number
  return_code?: number
  is_error?: boolean
}

export interface NativeServiceRequestMessage {
  member: string
  function: string
  request_id: number
  args: unknown
  payload_hex: string
  received_at: number
  reply_allowed: boolean
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
  signalValues?: Record<string, JsonValue>
  signalDecoder?: string
  signalDecodeError?: string
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
  mode?: 'socket' | 'pcap'
  capture_interface?: string | null
  capture_filter?: string
  captured_count?: number
  kernel_dropped_count?: number | null
  interface_dropped_count?: number | null
  parse_error_count?: number
  active_streams?: number
  active_fragment_datagrams?: number
  fragment_buffered_bytes?: number
  reassembled_datagrams?: number
  fragment_error_count?: number
  last_error?: string | null
}

export interface CaptureInterface {
  name: string
  description: string
  loopback: boolean
  addresses: string[]
}

export interface NetworkListenerConfig {
  name: string
  transport: 'udp' | 'tcp'
  bind_host: string
  port: number
  multicast_group?: string
  interface_ip: string
  mode?: 'socket' | 'pcap'
  capture_interface?: string
  capture_filter?: string
  promiscuous?: boolean
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
    kind: 'constant' | 'sine' | 'ramp' | 'random' | 'sequence' | 'step' | 'state_machine'
    data_type: 'boolean' | 'uint8' | 'uint16' | 'uint32' | 'uint64' | 'int8' | 'int16' | 'int32' | 'int64' | 'float32' | 'float64'
    minimum: number | bigint
    maximum: number | bigint
    initial: number | bigint
    period_seconds: number
    sequence: Array<number | bigint>
    seed?: number | bigint
    step_at_ms?: number | bigint | null
    step_value?: number | bigint | null
    initial_state?: string | null
    states?: TimedSignalState[]
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
  runtime: 'vsomeip' | null
  link_type: number | null
  reassembled_datagrams: number
  fragment_error_count: number
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
  runtime?: 'pi-agent-core' | 'local-evidence-engine'
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

export interface UpdateInstallationStatus {
  installation_id: string
  version: string
  status: 'prepared' | 'complete' | 'failed'
  rollback_completed: boolean
  rollback_failed: boolean
  restored_version: string | null
  error: string | null
}

export const LLM_MODELS = [
  'qwen3.5-plus',
  'deepseek-v4-pro',
  'deepseek-v4-flash',
  'glm-5.2',
  'deepseek-v4.1-flash',
] as const

export const DEFAULT_LLM_BASE_URL = 'https://voyahgpt-gateway.voyah.cn/api/gateway/v1'
