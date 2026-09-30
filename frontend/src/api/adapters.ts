import type { MonitorMessage, NetworkListener, PcapImportResult, ServiceDefinition, ServiceSignal } from '../types'

interface RawSignal {
  name?: string
  data_type?: string
  unit?: string | null
  minimum?: number | null
  maximum?: number | null
  byte_order?: 'big' | 'little'
  factor?: number
  offset?: number
}

interface RawMethod {
  name?: string
  method_id?: number | null
  input_signals?: RawSignal[]
  output_signals?: RawSignal[]
  fire_and_forget?: boolean
}

interface RawEvent {
  name?: string
  event_id?: number | null
  event_group_ids?: number[]
  signals?: RawSignal[]
}

interface RawField {
  name?: string
  getter_id?: number | null
  setter_id?: number | null
  notifier_id?: number | null
  signal?: RawSignal | null
}

export interface RawServiceDefinition {
  name?: string
  path?: string
  service_id?: number | null
  instance_ids?: number[]
  major_version?: number
  minor_version?: number
  methods?: RawMethod[]
  events?: RawEvent[]
  fields?: RawField[]
}

export interface RawMonitorMessage {
  id?: string
  timestamp?: string
  direction?: 'rx' | 'tx' | 'sim' | 'pcap' | 'RX' | 'TX'
  transport?: string
  source?: string
  destination?: string
  service_id?: number
  method_id?: number
  message_type?: number | string
  return_code?: number
  payload_hex?: string
  payload_size?: number
  is_sd?: boolean
  sd_summary?: string | null
  signal_values?: Record<string, number | string | boolean>
  metadata?: Record<string, unknown>
}

interface RawNetworkListener {
  id?: string
  config?: {
    name?: string
    transport?: 'udp' | 'tcp'
    bind_host?: string
    port?: number
    multicast_group?: string | null
    interface_ip?: string
  }
  running?: boolean
  started_at?: string
  received_count?: number
  parse_error_count?: number
  last_error?: string | null
}

export interface RawPcapImportResult {
  source_name?: string
  packet_count?: number
  captured_bytes?: number
  someip_count?: number
  someip_packet_count?: number
  sd_count?: number
  sd_packet_count?: number
  skipped_count?: number
  duration_seconds?: number
  start_time?: string | null
  end_time?: string | null
  endpoint_count?: number
  top_endpoints?: Array<{
    endpoint?: string
    ip_version?: number
    is_multicast?: boolean
    packet_count?: number
    sent_count?: number
    received_count?: number
    someip_count?: number
    transport_counts?: Record<string, number>
    offered_service_ids?: number[]
  }>
  transport_counts?: Record<string, number>
  protocol_counts?: Record<string, number>
  sd_entry_counts?: Record<string, number>
  errors?: string[]
}

const messageTypes: Record<number, string> = {
  0x00: 'REQUEST', 0x01: 'REQUEST_NO_RETURN', 0x02: 'NOTIFICATION',
  0x40: 'REQUEST_ACK', 0x41: 'REQUEST_NO_RETURN_ACK', 0x42: 'NOTIFICATION_ACK',
  0x80: 'RESPONSE', 0x81: 'ERROR', 0xC0: 'RESPONSE_ACK', 0xC1: 'ERROR_ACK',
}

const returnCodes: Record<number, string> = {
  0x00: 'E_OK', 0x01: 'E_NOT_OK', 0x02: 'E_UNKNOWN_SERVICE', 0x03: 'E_UNKNOWN_METHOD',
  0x04: 'E_NOT_READY', 0x05: 'E_NOT_REACHABLE', 0x06: 'E_TIMEOUT',
  0x07: 'E_WRONG_PROTOCOL_V', 0x08: 'E_WRONG_INTERFACE_V',
  0x09: 'E_MALFORMED_MESSAGE', 0x0A: 'E_WRONG_MESSAGE_TYPE',
}

export function formatHex(value: number | string | null | undefined, width = 4): string {
  if (typeof value === 'string' && value.toLowerCase().startsWith('0x')) return value.toUpperCase().replace('X', 'x')
  const numeric = typeof value === 'number' ? value : Number(value ?? 0)
  return `0x${(Number.isFinite(numeric) ? numeric : 0).toString(16).toUpperCase().padStart(width, '0')}`
}

function signalLabel(signals: RawSignal[] | undefined): string {
  if (!signals?.length) return 'Empty'
  return signals.map((signal) => signal.name || signal.data_type || 'value').join(', ')
}

function normalizeSignal(raw: RawSignal, fallbackName: string): ServiceSignal {
  return {
    name: raw.name || fallbackName,
    dataType: raw.data_type || 'uint32',
    unit: raw.unit ?? undefined,
    minimum: raw.minimum ?? undefined,
    maximum: raw.maximum ?? undefined,
    byteOrder: raw.byte_order ?? 'big', factor: raw.factor ?? 1, offset: raw.offset ?? 0,
  }
}

export function normalizeService(raw: RawServiceDefinition, index = 0): ServiceDefinition {
  const serviceId = formatHex(raw.service_id)
  const instanceId = formatHex(raw.instance_ids?.[0] ?? 0)
  return {
    id: raw.path || `${raw.name ?? 'service'}-${serviceId}-${index}`,
    name: raw.name || `Service_${serviceId}`,
    serviceId,
    instanceId,
    instanceIds: raw.instance_ids ?? [],
    deployed: raw.service_id != null,
    majorVersion: raw.major_version ?? 1,
    minorVersion: raw.minor_version ?? 0,
    methods: (raw.methods ?? []).map((method, methodIndex) => ({
      id: formatHex(method.method_id ?? methodIndex),
      name: method.name || `Method_${methodIndex}`,
      requestType: signalLabel(method.input_signals),
      responseType: signalLabel(method.output_signals),
      inputSignals: (method.input_signals ?? []).map((signal, signalIndex) => normalizeSignal(signal, `${method.name || `Method_${methodIndex}`}_Input_${signalIndex + 1}`)),
      outputSignals: (method.output_signals ?? []).map((signal, signalIndex) => normalizeSignal(signal, `${method.name || `Method_${methodIndex}`}_Output_${signalIndex + 1}`)),
    })),
    events: (raw.events ?? []).map((event, eventIndex) => ({
      id: formatHex(event.event_id ?? eventIndex),
      name: event.name || `Event_${eventIndex}`,
      deployed: event.event_id != null,
      eventGroup: event.event_group_ids?.length ? event.event_group_ids.map((id) => formatHex(id)).join(', ') : undefined,
      dataType: event.signals?.[0]?.data_type,
      signals: (event.signals ?? []).map((signal, signalIndex) => normalizeSignal(signal, `${event.name || `Event_${eventIndex}`}_${signalIndex + 1}`)),
    })),
    fields: (raw.fields ?? []).map((field, fieldIndex) => ({
      id: formatHex(field.notifier_id ?? field.getter_id ?? field.setter_id ?? fieldIndex),
      name: field.name || `Field_${fieldIndex}`,
      dataType: field.signal?.data_type,
      getter: field.getter_id == null ? undefined : formatHex(field.getter_id),
      setter: field.setter_id == null ? undefined : formatHex(field.setter_id),
      notifier: field.notifier_id == null ? undefined : formatHex(field.notifier_id),
      signal: field.signal ? normalizeSignal(field.signal, field.name || `Field_${fieldIndex}`) : undefined,
    })),
  }
}

export function normalizeServices(raw: RawServiceDefinition[]): ServiceDefinition[] {
  return raw.map(normalizeService)
}

export function normalizeMonitorMessage(raw: RawMonitorMessage, index = 0): MonitorMessage {
  const messageType = typeof raw.message_type === 'number'
    ? messageTypes[raw.message_type] ?? formatHex(raw.message_type, 2)
    : raw.message_type ?? 'UNKNOWN'
  const metadataLatency = raw.metadata?.latency_ms
  return {
    id: raw.id ?? `${Date.now()}-${index}`,
    timestamp: raw.timestamp ?? new Date().toISOString(),
    direction: raw.direction === 'tx' || raw.direction === 'sim' || raw.direction === 'TX' ? 'TX' : 'RX',
    origin: raw.direction === 'pcap' || raw.direction === 'sim' ? raw.direction : raw.direction?.toLowerCase() === 'tx' ? 'tx' : 'rx',
    source: raw.source || '—',
    destination: raw.destination || '—',
    protocol: raw.is_sd ? 'SOME/IP-SD' : 'SOME/IP',
    serviceId: formatHex(raw.service_id),
    methodId: formatHex(raw.method_id),
    messageType: raw.is_sd && raw.sd_summary ? raw.sd_summary : messageType,
    length: raw.payload_size ?? 0,
    status: returnCodes[raw.return_code ?? 0] ?? formatHex(raw.return_code, 2),
    payload: raw.payload_hex,
    latencyMs: typeof metadataLatency === 'number' ? metadataLatency : undefined,
    signalValues: raw.signal_values,
  }
}

export function normalizeNetworkListener(raw: RawNetworkListener, index = 0): NetworkListener {
  const config = raw.config ?? {}
  return {
    id: raw.id ?? `listener-${index}`,
    name: config.name ?? `Listener ${index + 1}`,
    transport: config.transport ?? 'udp',
    bind_host: config.bind_host ?? '0.0.0.0',
    port: config.port ?? 30490,
    multicast_group: config.multicast_group,
    interface_ip: config.interface_ip,
    status: raw.running ? 'running' : 'stopped',
    running: Boolean(raw.running),
    message_count: raw.received_count ?? 0,
    started_at: raw.started_at,
  }
}

export function normalizePcapResult(raw: RawPcapImportResult): PcapImportResult {
  return {
    file_name: raw.source_name ?? 'capture.pcap',
    packet_count: raw.packet_count ?? 0,
    captured_bytes: raw.captured_bytes ?? 0,
    someip_count: raw.someip_count ?? 0,
    someip_packet_count: raw.someip_packet_count ?? 0,
    sd_count: raw.sd_count ?? 0,
    sd_packet_count: raw.sd_packet_count ?? 0,
    skipped_count: raw.skipped_count ?? 0,
    duration_seconds: raw.duration_seconds ?? 0,
    start_time: raw.start_time ?? null,
    end_time: raw.end_time ?? null,
    endpoint_count: raw.endpoint_count ?? 0,
    top_endpoints: (raw.top_endpoints ?? []).map((endpoint) => ({
      endpoint: endpoint.endpoint ?? '未知端点',
      ip_version: endpoint.ip_version === 6 ? 6 : 4,
      is_multicast: Boolean(endpoint.is_multicast),
      packet_count: endpoint.packet_count ?? 0,
      sent_count: endpoint.sent_count ?? 0,
      received_count: endpoint.received_count ?? 0,
      someip_count: endpoint.someip_count ?? 0,
      transport_counts: endpoint.transport_counts ?? {},
      offered_service_ids: endpoint.offered_service_ids ?? [],
    })),
    transport_counts: raw.transport_counts ?? {},
    protocol_counts: raw.protocol_counts ?? {},
    sd_entry_counts: raw.sd_entry_counts ?? {},
    warnings: raw.errors ?? [],
  }
}
