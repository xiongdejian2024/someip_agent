import { useEffect, useMemo, useRef, useState } from 'react'
import { api, describeApiError } from '../api/client'
import { formatHex } from '../api/adapters'
import { logError, logInfo } from '../api/logger'
import { Icon } from '../components/Icon'
import { SignalScope } from '../components/SignalScope'
import { GeneratorNumberInput } from '../components/GeneratorNumberInput'
import { GeneratorSequenceInput } from '../components/GeneratorSequenceInput'
import { TimedStateInput } from '../components/TimedStateInput'
import { validateTimedGraph } from '../data/timedStates'
import type { TimedSignalState } from '../types'
import { generatorMidpoint, generatorSliderSafe, isGeneratorNumber, validateGeneratorRange, validateGeneratorStep, validateGeneratorValue, validateGeneratorSequence, type GeneratorNumber } from '../data/generatorValues'
import { protocolId, useAgentScope } from '../agent/workspace'
import type { ServiceDefinition, ServiceSignal, SimulationConfig, SimulationStartRequest, SimulationStatus, WaveSample } from '../types'
import './SimulationPage.css'
import { useProject } from '../workbench/projects'

interface SimulationPageProps {
  services: ServiceDefinition[]
  samples: WaveSample[]
  loading?: boolean
  draft?: { id: string; config: SimulationStartRequest }
  onDismissDraft: () => void
}

type SimState = 'idle' | 'starting' | 'running' | 'stopping'
type GeneratorMode = 'constant' | 'sine' | 'ramp' | 'step' | 'random' | 'sequence' | 'state_machine'
type GeneratorDataType = SimulationStartRequest['generator']['data_type']

interface SimulatableSignal {
  key: string
  name: string
  originalName: string
  serviceKey: string
  serviceName: string
  serviceId: number
  instanceId: number
  interfaceVersion: number
  methodId: number
  source: string
  dataType: GeneratorDataType
  unit?: string
  minimum: GeneratorNumber
  maximum: GeneratorNumber
  step: number
  inferredBounds: boolean
}

interface SimulationProjection {
  signals: SimulatableSignal[]
  warnings: { serviceKey: string; message: string }[]
}

interface SignalSetting { kind: GeneratorMode; value: GeneratorNumber; periodSeconds: number; minimum: GeneratorNumber; maximum: GeneratorNumber; seed: GeneratorNumber; sequence: GeneratorNumber[]; stepAtMs: GeneratorNumber; stepValue: GeneratorNumber; initialState: string; states: TimedSignalState[] }
const generatorLabels: Record<GeneratorMode, string> = { constant: '常量', sine: '正弦', ramp: '斜坡', step: '阶跃', random: '随机', sequence: '序列', state_machine: '时间状态机' }
const SIGNAL_PAGE_SIZE = 40
const TASK_PAGE_SIZE = 8

const generatorDataTypes = new Set<GeneratorDataType>([
  'boolean', 'uint8', 'uint16', 'uint32', 'uint64',
  'int8', 'int16', 'int32', 'int64', 'float32', 'float64',
])

function numericId(value: string): number {
  const parsed = Number.parseInt(value, value.toLowerCase().startsWith('0x') ? 16 : 10)
  return Number.isFinite(parsed) ? parsed : 0
}

function generatorDataType(value: string | undefined): GeneratorDataType | null {
  const normalized = value?.toLowerCase() as GeneratorDataType | undefined
  return normalized && generatorDataTypes.has(normalized) ? normalized : null
}

function defaultBounds(dataType: GeneratorDataType): [number, number] {
  if (dataType === 'boolean') return [0, 1]
  if (dataType.startsWith('int')) return [-100, 100]
  return [0, 100]
}

function signalBounds(signal: ServiceSignal, dataType: GeneratorDataType): [GeneratorNumber, GeneratorNumber] {
  const [fallbackMinimum, fallbackMaximum] = defaultBounds(dataType)
  const minimum = isGeneratorNumber(signal.minimum) ? signal.minimum : fallbackMinimum
  const maximumCandidate = isGeneratorNumber(signal.maximum) ? signal.maximum : fallbackMaximum
  return [minimum, maximumCandidate >= minimum ? maximumCandidate : minimum]
}

function signalStep(dataType: GeneratorDataType, minimum: GeneratorNumber, maximum: GeneratorNumber): number {
  if (dataType === 'boolean' || dataType.includes('int')) return 1
  return Math.max(0.01, Number(((Number(maximum) - Number(minimum)) / 1000).toPrecision(2)))
}

function supportedEncoding(signal: ServiceSignal): boolean {
  return signal.byteOrder !== 'little' && (signal.factor ?? 1) === 1 && (signal.offset ?? 0) === 0
}

function buildSimulationProjection(services: ServiceDefinition[], selectedServices: Set<string>): SimulationProjection {
  const signals: SimulatableSignal[] = []
  const warnings: SimulationProjection['warnings'] = []
  services.forEach((service) => {
    if (!selectedServices.has(service.id)) return
    const warn = (message: string) => warnings.push({ serviceKey: service.id, message })
    if (service.deployed === false) {
      warn(`${service.name}：ARXML 未提供 Service ID，当前版本不启动`)
      return
    }
    if (service.instanceIds?.length === 0) {
      warn(`${service.name}：ARXML 缺少 Instance ID 部署，请补全后再仿真`)
      return
    }
    const common = {
      serviceKey: service.id,
      serviceName: service.name,
      serviceId: numericId(service.serviceId),
      instanceId: numericId(service.instanceId),
      interfaceVersion: service.majorVersion ?? 1,
    }
    service.events.forEach((event, eventIndex) => {
      if (event.deployed === false) {
        warn(`${service.name}.${event.name}：ARXML 未提供 Event ID，当前版本不启动`)
        return
      }
      const definitions: ServiceSignal[] = event.signals?.length
        ? event.signals
        : event.dataType ? [{ name: event.name, dataType: event.dataType }] : []
      if (definitions.length !== 1) {
        warn(`${service.name}.${event.name}：${definitions.length ? `${definitions.length} 个信号的复杂 payload` : '缺少信号定义'}，当前版本不启动`)
        return
      }
      const signal = definitions[0]
      if (!supportedEncoding(signal)) {
        warn(`${service.name}.${event.name}：当前发生器不支持该信号的字节序或缩放编码`)
        return
      }
      const dataType = generatorDataType(signal.dataType)
      if (!dataType) {
        warn(`${service.name}.${event.name}：${signal.dataType || '未知类型'} 暂不支持数值仿真`)
        return
      }
      const [minimum, maximum] = signalBounds(signal, dataType)
      signals.push({
        ...common,
        key: `${service.id}:event:${event.id}:${signal.name}:${eventIndex}`,
        name: signal.name,
        originalName: signal.name,
        methodId: numericId(event.id),
        source: `事件 ${event.name} · ${event.id}`,
        dataType,
        unit: signal.unit,
        minimum,
        maximum,
        step: signalStep(dataType, minimum, maximum),
        inferredBounds: !isGeneratorNumber(signal.minimum) || !isGeneratorNumber(signal.maximum),
      })
    })
    service.fields.forEach((field, fieldIndex) => {
      if (!field.notifier) return
      const definition = field.signal ?? (field.dataType ? { name: field.name, dataType: field.dataType } : undefined)
      if (!definition) {
        warn(`${service.name}.${field.name}：Notifier 缺少信号定义，当前版本不启动`)
        return
      }
      if (!supportedEncoding(definition)) {
        warn(`${service.name}.${field.name}：当前发生器不支持该信号的字节序或缩放编码`)
        return
      }
      const dataType = generatorDataType(definition.dataType)
      if (!dataType) {
        warn(`${service.name}.${field.name}：${definition.dataType || '未知类型'} 暂不支持数值仿真`)
        return
      }
      const [minimum, maximum] = signalBounds(definition, dataType)
      signals.push({
        ...common,
        key: `${service.id}:field:${field.notifier}:${definition.name}:${fieldIndex}`,
        name: definition.name,
        originalName: definition.name,
        methodId: numericId(field.notifier),
        source: `字段 ${field.name} · Notifier ${field.notifier}`,
        dataType,
        unit: definition.unit,
        minimum,
        maximum,
        step: signalStep(dataType, minimum, maximum),
        inferredBounds: !isGeneratorNumber(definition.minimum) || !isGeneratorNumber(definition.maximum),
      })
    })
  })
  return { signals, warnings }
}

function initialSignalValue(signal: SimulatableSignal): GeneratorNumber {
  return generatorMidpoint(signal.minimum, signal.maximum, signal.dataType)
}

function defaultSetting(signal: SimulatableSignal): SignalSetting {
  return { kind: 'constant', value: initialSignalValue(signal), periodSeconds: 5, minimum: signal.minimum, maximum: signal.maximum, seed: 0, sequence: [initialSignalValue(signal)], stepAtMs: 1000, stepValue: signal.maximum, initialState: 'hold', states: [{ name: 'hold', value: signal.dataType === 'boolean' ? Boolean(initialSignalValue(signal)) : initialSignalValue(signal) }] }
}

function settingGenerator(signal: SimulatableSignal, setting: SignalSetting): SimulationStartRequest['generator'] {
  return { signal_name: signal.name, kind: setting.kind, data_type: signal.dataType,
    minimum: setting.minimum, maximum: setting.maximum, initial: setting.value,
    period_seconds: setting.periodSeconds, sequence: setting.kind === 'sequence' ? setting.sequence : [], seed: setting.seed,
    ...(setting.kind === 'step' ? {step_at_ms:setting.stepAtMs,step_value:setting.stepValue} : {}),
    ...(setting.kind === 'state_machine' ? { initial_state: setting.initialState, states: setting.states } : {}) }
}

function waveKey(signal: Pick<SimulatableSignal, 'serviceId' | 'methodId' | 'name'>): string {
  return formatHex(signal.serviceId) + '/' + formatHex(signal.methodId) + '/' + signal.name
}

export function SimulationPage({ services, samples, draft, onDismissDraft, loading = false }: SimulationPageProps) {
  const project = useProject()
  const restoreDrafts = useRef(project?.document.simulations ?? [])
  const [simState, setSimState] = useState<SimState>('idle')
  const [mode, setMode] = useState<'physical' | 'virtual'>('virtual')
  const [config, setConfig] = useState<SimulationConfig>({
    destinationHost: '127.0.0.1', destinationPort: 30501, cycleMs: 20, multiplier: 1,
    periodSeconds: 5, enableSd: true, autoRespond: false, selectedServices: [],
  })
  const [serviceQuery, setServiceQuery] = useState('')
  const [signalQuery, setSignalQuery] = useState('')
  const [serviceKey, setServiceKey] = useState('')
  const [selectedKeys, setSelectedKeys] = useState<string[]>([])
  const [focusedKey, setFocusedKey] = useState('')
  const [settings, setSettings] = useState<Record<string, SignalSetting>>({})
  const [tableView, setTableView] = useState<'catalog' | 'workset'>('catalog')
  const [propertyTab, setPropertyTab] = useState<'signal' | 'runtime'>('signal')
  const [signalPage, setSignalPage] = useState(0)
  const [taskPage, setTaskPage] = useState(0)
  const [taskView, setTaskView] = useState<'running' | 'all'>('running')
  const [workspaceView, setWorkspaceView] = useState<'signals' | 'waveform' | 'tasks'>('signals')
  const [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null)
  const [runtimeChecked, setRuntimeChecked] = useState(false)
  const [tasks, setTasks] = useState<SimulationStatus[]>([])
  const [stoppingTask, setStoppingTask] = useState<string | null>(null)
  const [invalidInputs, setInvalidInputs] = useState<Record<string, boolean>>({})
  const inputsValid = !Object.values(invalidInputs).some(Boolean)
  const inputValidity = (key: string, valid: boolean) => setInvalidInputs(current => {
    if (!!current[key] === !valid) return current
    const next = { ...current }
    if (valid) delete next[key]; else next[key] = true
    return next
  })
  const mutationEpoch = useRef(0)

  const projection = useMemo(() => buildSimulationProjection(services, new Set(services.map((service) => service.id))), [services])
  const signalIndex = useMemo(() => new Map(projection.signals.map((signal) => [signal.key, signal])), [projection.signals])
  const restored = useRef(false)
  useEffect(() => {
    if (restored.current || !projection.signals.length) return
    restored.current = true
    const matches = restoreDrafts.current.flatMap(request => {
      const signal = projection.signals.find(item => item.serviceId === request.service_id && item.instanceId === request.instance_id && item.methodId === request.method_id && item.name === request.generator.signal_name)
      return signal && ['constant', 'sine', 'ramp', 'step', 'random', 'sequence', 'state_machine'].includes(request.generator.kind) ? [{ signal, request }] : []
    })
    setSelectedKeys(matches.map(item => item.signal.key))
    setSettings(Object.fromEntries(matches.map(({ signal, request }) => [signal.key, { kind: request.generator.kind as GeneratorMode, value: request.generator.initial, periodSeconds: request.generator.period_seconds, minimum: request.generator.minimum, maximum: request.generator.maximum, seed: request.generator.seed ?? 0, sequence: request.generator.kind === 'sequence' ? request.generator.sequence : [request.generator.initial], stepAtMs: request.generator.step_at_ms ?? 1000, stepValue: request.generator.step_value ?? signal.maximum, initialState: request.generator.initial_state ?? 'hold', states: request.generator.states?.length ? request.generator.states : defaultSetting(signal).states }])))
    const first = matches[0]?.request
    if (first) {
      setMode(first.transport === 'udp' ? 'physical' : 'virtual')
      setConfig(current => ({ ...current, cycleMs: first.interval_ms, multiplier: 1, destinationHost: first.destination_host, destinationPort: first.destination_port, enableSd: first.enable_sd ?? true }))
    }
  }, [projection.signals])
  const signalCounts = useMemo(() => {
    const result = new Map<string, number>()
    projection.signals.forEach((signal) => result.set(signal.serviceKey, (result.get(signal.serviceKey) ?? 0) + 1))
    return result
  }, [projection.signals])
  const activeServiceKey = services.some((service) => service.id === serviceKey)
    ? serviceKey : services.find((service) => signalCounts.has(service.id))?.id ?? services[0]?.id ?? ''
  const activeService = services.find((service) => service.id === activeServiceKey)
  const selectedKeySet = useMemo(() => new Set(selectedKeys), [selectedKeys])
  const selectedSignals = useMemo(() => selectedKeys.flatMap((key) => signalIndex.get(key) ? [signalIndex.get(key)!] : []), [selectedKeys, signalIndex])
  const selectedServices = useMemo(() => new Set(selectedSignals.map((signal) => signal.serviceKey)), [selectedSignals])
  const visibleServices = useMemo(() => {
    const query = serviceQuery.trim().toLowerCase()
    return services.filter((service) => [service.name, service.serviceId, service.instanceId].some((value) => value.toLowerCase().includes(query)))
  }, [services, serviceQuery])
  const visibleSignals = useMemo(() => {
    const query = signalQuery.trim().toLowerCase()
    return projection.signals.filter((signal) => (
      tableView === 'workset' ? selectedKeySet.has(signal.key) : signal.serviceKey === activeServiceKey
    ) && [signal.name, signal.serviceName, signal.source, signal.dataType, formatHex(signal.methodId)].some((value) => value.toLowerCase().includes(query)))
  }, [activeServiceKey, projection.signals, selectedKeySet, signalQuery, tableView])
  const maxSignalPage = Math.max(0, Math.ceil(visibleSignals.length / SIGNAL_PAGE_SIZE) - 1)
  const currentSignalPage = Math.min(signalPage, maxSignalPage)
  const pageSignals = visibleSignals.slice(currentSignalPage * SIGNAL_PAGE_SIZE, (currentSignalPage + 1) * SIGNAL_PAGE_SIZE)
  const focusedSignal = signalIndex.get(focusedKey) ?? pageSignals[0]
  const focusedSetting = focusedSignal ? settings[focusedSignal.key] ?? defaultSetting(focusedSignal) : null
  const openAgent = useAgentScope({ page: 'simulation', source: 'live', service_id: focusedSignal?.serviceId ?? protocolId(activeService?.serviceId), method_id: focusedSignal?.methodId, signal_name: focusedSignal?.originalName })
  const serviceWarnings = projection.warnings.filter((warning) => warning.serviceKey === activeServiceKey)
  const runningTasks = tasks.filter((task) => task.running)
  const running = simState === 'running' || simState === 'stopping'
  const locked = simState !== 'idle'
  const effectiveIntervalMs = Math.max(10, Math.round(config.cycleMs / Math.max(config.multiplier, 0.01)))
  const projectUpdate = project?.update
  useEffect(() => {
    if (!restored.current) return
    const requests: SimulationStartRequest[] = selectedSignals.map(signal => {
      const setting = settings[signal.key] ?? defaultSetting(signal)
      return {
        name: `${signal.serviceName}-${signal.name}-simulation`, service_id: signal.serviceId, instance_id: signal.instanceId,
        method_id: signal.methodId, interface_version: signal.interfaceVersion, interval_ms: effectiveIntervalMs,
        transport: mode === 'physical' ? 'udp' : 'internal', destination_host: config.destinationHost, destination_port: config.destinationPort, enable_sd: config.enableSd,
        generator: settingGenerator(signal, setting),
      }
    })
    projectUpdate?.(doc => ({ ...doc, simulations: requests }))
  }, [projectUpdate, selectedSignals, settings, effectiveIntervalMs, mode, config.destinationHost, config.destinationPort, config.enableSd])
  const visibleTasks = taskView === 'running' ? runningTasks : tasks
  const maxTaskPage = Math.max(0, Math.ceil(visibleTasks.length / TASK_PAGE_SIZE) - 1)
  const currentTaskPage = Math.min(taskPage, maxTaskPage)
  const pageTasks = visibleTasks.slice(currentTaskPage * TASK_PAGE_SIZE, (currentTaskPage + 1) * TASK_PAGE_SIZE)
  const allowedWaveKeys = useMemo(() => Array.from(new Set([
    ...selectedSignals.map(waveKey),
    ...tasks.filter((task) => task.running).map((task) => waveKey({
      serviceId: task.config.service_id, methodId: task.config.method_id, name: task.config.generator.signal_name,
    })),
  ])), [selectedSignals, tasks])

  useEffect(() => {
    let cancelled = false
    let timer: number | undefined
    let failed = false
    let initialized = false
    const synchronizeRuntime = async () => {
      const epoch = mutationEpoch.current
      try {
        const latest = await api.simulations()
        if (cancelled || epoch !== mutationEpoch.current) return
        setTasks(latest)
        setSimState((current) => current === 'starting' || current === 'stopping' ? current : latest.some((task) => task.running) ? 'running' : 'idle')
        if (!initialized) {
          const activeTask = latest.find((task) => task.running)
          if (activeTask) {
            setMode(activeTask.config.transport === 'udp' ? 'physical' : 'virtual')
            setConfig((current) => ({ ...current, cycleMs: activeTask.config.interval_ms, destinationHost: activeTask.config.destination_host, destinationPort: activeTask.config.destination_port }))
          }
          initialized = true
        }
        setRuntimeChecked(true)
        if (failed) setNotice((current) => current?.text === '暂时无法读取后端任务状态，请检查后端连接。' ? null : current)
        failed = false
      } catch (error) {
        if (!cancelled) setRuntimeChecked(false)
        if (!cancelled && !failed) {
          logError('同步仿真任务状态失败', error)
          setNotice({ text: '暂时无法读取后端任务状态，请检查后端连接。', error: true })
        }
        failed = true
      } finally {
        if (!cancelled) {
          timer = window.setTimeout(() => void synchronizeRuntime(), 2500)
        }
      }
    }
    void synchronizeRuntime()
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [])

  useEffect(() => {
    setSelectedKeys((current) => current.filter((key) => signalIndex.has(key)))
    setSettings((current) => Object.fromEntries(Object.entries(current).filter(([key]) => signalIndex.has(key))))
  }, [signalIndex])

  const updateConfig = <K extends keyof SimulationConfig>(key: K, value: SimulationConfig[K]) => {
    setConfig((current) => ({ ...current, [key]: value }))
  }
  const updateSetting = (signal: SimulatableSignal, update: Partial<SignalSetting>) => {
    setSettings((current) => ({ ...current, [signal.key]: { ...(current[signal.key] ?? defaultSetting(signal)), ...update } }))
  }
  const toggleSignal = (signal: SimulatableSignal) => {
    setSelectedKeys((current) => current.includes(signal.key) ? current.filter((key) => key !== signal.key) : [...current, signal.key])
    setFocusedKey(signal.key)
    setPropertyTab('signal')
  }

  const applyDraft = () => {
    if (!draft || locked || !runtimeChecked) return
    try {
      const planned = draft.config
      const target = projection.signals.find((signal) => signal.serviceId === planned.service_id && signal.instanceId === planned.instance_id && signal.methodId === planned.method_id && signal.originalName === planned.generator.signal_name)
      if (!target || planned.transport !== 'internal' || planned.generator.data_type !== target.dataType || !['constant', 'sine', 'ramp', 'step', 'random', 'sequence', 'state_machine'].includes(planned.generator.kind)) throw new Error('草案与当前 ARXML 或仿真能力不匹配，请重新生成')
      const { minimum, maximum, initial, period_seconds } = planned.generator
      validateGeneratorRange(target.dataType, planned.generator.kind, minimum, maximum, initial)
      if (planned.generator.kind === 'step') validateGeneratorStep(target.dataType, planned.generator.step_at_ms, planned.generator.step_value, minimum, maximum)
      validateGeneratorValue(planned.generator.seed ?? 0, 'uint64')
      if (planned.generator.kind === 'sequence') validateGeneratorSequence(planned.generator.sequence, target.dataType, minimum, maximum)
      if (planned.generator.kind === 'state_machine') validateTimedGraph({ initial_state: planned.generator.initial_state, states: planned.generator.states }, target.dataType, minimum, maximum)
      if (![period_seconds, planned.interval_ms].every(Number.isFinite) || period_seconds < 0.1 || planned.interval_ms < 10 || planned.interval_ms > 60_000) throw new Error('草案包含无效的激励参数')
      if (!target.inferredBounds && (minimum < target.minimum || maximum > target.maximum)) throw new Error('草案超出当前 ARXML 范围')
      setSelectedKeys([target.key])
      setSettings({ [target.key]: { kind: planned.generator.kind as GeneratorMode, value: initial, periodSeconds: period_seconds, minimum, maximum, seed: planned.generator.seed ?? 0, sequence: planned.generator.kind === 'sequence' ? planned.generator.sequence : [initial], stepAtMs: planned.generator.step_at_ms ?? 1000, stepValue: planned.generator.step_value ?? maximum, initialState: planned.generator.initial_state ?? 'hold', states: planned.generator.states?.length ? planned.generator.states : defaultSetting(target).states } })
      setServiceKey(target.serviceKey); setFocusedKey(target.key); setServiceQuery(''); setSignalQuery(''); setSignalPage(0)
      setTableView('workset'); setWorkspaceView('signals'); setPropertyTab('signal'); setMode('virtual')
      setConfig((current) => ({ ...current, cycleMs: planned.interval_ms, multiplier: 1, enableSd: planned.enable_sd ?? false }))
      onDismissDraft()
      setNotice({ text: '已将草案应用到单信号工作集，尚未运行。请检查参数后点击“启动工作集”。' })
      logInfo('已确认应用智能体草案，等待手动启动', { serviceId: planned.service_id, methodId: planned.method_id })
    } catch (error) {
      logError('应用智能体仿真草案失败', error)
      setNotice({ error: true, text: describeApiError(error) })
    }
  }

  const start = async () => {
    if (!runtimeChecked) return
    mutationEpoch.current += 1
    setSimState('starting')
    setNotice(null)
    const startedIds: string[] = []
    try {
      if (!inputsValid) throw new Error('请先修正无效的激励数值输入')
      if (!selectedSignals.length) throw new Error('请先将需要仿真的信号加入工作集')
      if (mode === 'physical' && !config.destinationHost.trim()) throw new Error('真实 UDP 模式必须填写目标主机')
      if (!Number.isFinite(config.cycleMs) || config.cycleMs < 10 || config.cycleMs > 60000) throw new Error('基础周期必须在 10–60000 ms 之间')
      if (mode === 'physical' && (!Number.isInteger(config.destinationPort) || config.destinationPort < 1 || config.destinationPort > 65535)) {
        throw new Error('目标端口必须在 1–65535 之间')
      }
      const offeredServices = new Set<string>()
      const requests: SimulationStartRequest[] = selectedSignals.map((signal) => {
        const setting = settings[signal.key] ?? defaultSetting(signal)
        validateGeneratorRange(signal.dataType, setting.kind, setting.minimum, setting.maximum, setting.value)
        if (setting.kind === 'step') validateGeneratorStep(signal.dataType, setting.stepAtMs, setting.stepValue, setting.minimum, setting.maximum)
        validateGeneratorValue(setting.seed, 'uint64')
        if (setting.kind === 'sequence') validateGeneratorSequence(setting.sequence, signal.dataType, setting.minimum, setting.maximum)
        if (setting.kind === 'state_machine') validateTimedGraph({ initial_state: setting.initialState, states: setting.states }, signal.dataType, setting.minimum, setting.maximum)
        if (!signal.inferredBounds && (setting.minimum < signal.minimum || setting.maximum > signal.maximum)) throw new Error(signal.name + ' 的激励超出 ARXML 范围')
        if (!Number.isFinite(setting.periodSeconds) || setting.periodSeconds < 0.1) throw new Error(signal.name + ' 的曲线周期不得小于 0.1 秒')
        const serviceInstanceKey = signal.serviceId + ':' + signal.instanceId
        const enableSd = config.enableSd && !offeredServices.has(serviceInstanceKey)
        offeredServices.add(serviceInstanceKey)
        return {
          name: signal.serviceName + '-' + signal.name + '-simulation',
          service_id: signal.serviceId, instance_id: signal.instanceId, method_id: signal.methodId,
          interface_version: signal.interfaceVersion, interval_ms: effectiveIntervalMs,
          transport: mode === 'physical' ? 'udp' : 'internal', enable_sd: enableSd,
          destination_host: config.destinationHost.trim() || '127.0.0.1', destination_port: config.destinationPort,
          generator: settingGenerator(signal, setting),
        }
      })
      for (const request of requests) {
        const result = await api.startSimulation(request)
        startedIds.push(result.id)
        setTasks((current) => [...current, { id: result.id, running: result.running, config: request, emitted_count: 0 }])
      }
      setSimState('running')
      setTaskView('running')
      setNotice({ text: '已启动工作集中的 ' + requests.length + ' 个信号，发布周期 ' + effectiveIntervalMs + ' ms。' })
      logInfo('启动仿真工作集', { services: selectedServices.size, signals: requests.length, intervalMs: effectiveIntervalMs, mode })
    } catch (error) {
      logError('启动仿真工作集失败', error, { startedIds, selectedSignals: selectedSignals.length, mode })
      const rollbackFailures: string[] = []
      for (const id of startedIds) {
        try {
          await api.stopSimulation(id)
          setTasks((current) => current.map((task) => task.id === id ? { ...task, running: false } : task))
        } catch (rollbackError) {
          rollbackFailures.push(id)
          logError('回滚本次仿真任务失败', rollbackError, { simulationId: id })
        }
      }
      setSimState(rollbackFailures.length ? 'running' : 'idle')
      setNotice({ text: '启动失败：' + describeApiError(error) + (rollbackFailures.length ? '；部分任务回滚失败，请检查运行任务并停止。' : ''), error: true })
    } finally {
      mutationEpoch.current += 1
    }
  }

  const stop = async (taskId?: string) => {
    mutationEpoch.current += 1
    if (taskId) setStoppingTask(taskId)
    else setSimState('stopping')
    try {
      await api.stopSimulation(taskId)
      setTasks((current) => current.map((task) => !taskId || task.id === taskId ? { ...task, running: false } : task))
      const remaining = taskId ? runningTasks.filter((task) => task.id !== taskId).length : 0
      setSimState(remaining ? 'running' : 'idle')
      setNotice({ text: taskId ? '已停止选中的仿真任务。' : '全部仿真任务已停止。' })
      logInfo('停止仿真任务', { simulationId: taskId ?? '全部' })
    } catch (error) {
      logError('停止仿真任务失败', error, { simulationId: taskId })
      setSimState('running')
      setNotice({ text: '停止失败：' + describeApiError(error), error: true })
    } finally {
      mutationEpoch.current += 1
      setStoppingTask(null)
    }
  }

  return (
    <div className="page sim-workbench">
      {draft && <section className="panel agent-draft-review" aria-label="智能体仿真草案审阅"><div><strong>待审阅：{draft.config.generator.signal_name}</strong><p>{formatHex(draft.config.service_id)} / {formatHex(draft.config.method_id)} · {draft.config.generator.kind} · {draft.config.interval_ms} ms · 范围 {String(draft.config.generator.minimum)}–{String(draft.config.generator.maximum)} · 初始 {String(draft.config.generator.initial)} · 仅内部总线</p><small>应用后替换当前工作集为此单信号；不会自动启动。运行期间需先手动停止任务。</small></div><div className="context-actions"><button className="button primary" disabled={locked || !runtimeChecked || loading} onClick={applyDraft}>确认替换工作集</button><button className="button ghost" onClick={onDismissDraft}>取消草案</button></div></section>}
      <section className="panel sim-session-bar">
        <div className="sim-session-heading">
          <span className={'sim-session-indicator' + (running ? ' is-running' : '')}><Icon name={running ? 'activity' : 'play'} size={20} /></span>
          <div><h2>{running ? '测量运行中' : '信号仿真工作台'}</h2><p>{running ? runningTasks.length + ' 个运行任务' : '选择服务 → 勾选工作信号 → 配置激励 → 启动测量'}</p></div>
        </div>
        <div className="sim-session-metrics">
          <span><b>{selectedSignals.length}</b> 工作信号</span><span><b>{selectedServices.size}</b> 服务</span>
          <button onClick={() => { setPropertyTab('runtime'); setWorkspaceView('signals') }} className="sim-target-link" title="打开运行配置">{mode === 'virtual' ? '虚拟总线' : config.destinationHost + ':' + config.destinationPort} · {effectiveIntervalMs} ms <Icon name="settings" size={14} /></button>
        </div>
        <button className="button secondary" disabled={!focusedSignal || loading} onClick={() => openAgent('请基于当前选中的 ARXML 信号生成一个内部仿真草案，说明数据类型、范围、周期和验证步骤；仅准备草案，不启动任务。')}><Icon name="sparkles" size={16} />生成信号草案</button>
        {running ? <button className="button danger" disabled={simState === 'stopping' || stoppingTask !== null} onClick={() => void stop()}><Icon name="stop" size={16} />{simState === 'stopping' ? '停止中…' : '停止全部'}</button>
          : <button className="button primary" disabled={!runtimeChecked || simState === 'starting' || !selectedSignals.length || !inputsValid} onClick={() => void start()}>{!runtimeChecked || simState === 'starting' ? <span className="spinner" /> : <Icon name="play" size={16} />}{!runtimeChecked ? '同步状态…' : simState === 'starting' ? '启动中…' : '启动工作集'}</button>}
      </section>
      {notice && <div role="status" className={'inline-notice ' + (notice.error ? 'error' : 'success')}><Icon name="info" size={16} /><span>{notice.text}</span><button aria-label="关闭仿真提示" onClick={() => setNotice(null)}><Icon name="x" size={14} /></button></div>}

      <nav className="sim-view-tabs" aria-label="仿真工作区视图"><button className={workspaceView === 'signals' ? 'active' : ''} onClick={() => setWorkspaceView('signals')}><Icon name="layers" size={16} /> 信号激励 <span>{selectedSignals.length}</span></button><button className={workspaceView === 'waveform' ? 'active' : ''} onClick={() => setWorkspaceView('waveform')}><Icon name="activity" size={16} /> 输出波形</button><button className={workspaceView === 'tasks' ? 'active' : ''} onClick={() => setWorkspaceView('tasks')}><Icon name="terminal" size={16} /> 运行任务 <span>{runningTasks.length}</span></button></nav>

      {workspaceView === 'signals' && <section className="sim-workspace-grid" aria-label="仿真配置工作区">
        <aside className="panel sim-catalog-pane">
          <div className="sim-pane-heading"><h3><Icon name="database" size={16} /> 服务目录</h3><span>{services.length}</span></div>
          <label className="sim-search"><Icon name="search" size={15} /><input aria-label="搜索仿真服务" placeholder="服务名称 / ID" value={serviceQuery} onChange={(event) => setServiceQuery(event.target.value)} /></label>
          <div className="sim-service-list" role="list" aria-label="仿真服务列表">
            {visibleServices.map((service) => <button key={service.id} className={'sim-service-option' + (activeServiceKey === service.id ? ' is-active' : '')} onClick={() => { setServiceKey(service.id); setSignalPage(0); setSignalQuery(''); setTableView('catalog'); setFocusedKey('') }} title={service.name + ' · ' + service.serviceId + ' · Instance ' + service.instanceId}>
              <span className="sim-service-title"><strong>{service.name}</strong>{selectedServices.has(service.id) && <i aria-label="有工作信号" />}</span>
              <span className="sim-service-meta"><code>{service.serviceId}</code><span>{signalCounts.get(service.id) ?? 0} 路可用</span></span>
            </button>)}
            {!visibleServices.length && <div className="sim-empty">{loading ? '正在加载 ARXML 服务…' : '没有匹配的服务'}</div>}
          </div>
          <div className="sim-pane-footer">匹配 {visibleServices.length} / {services.length} 个服务</div>
        </aside>

        <section className="panel sim-signal-pane">
          <div className="sim-pane-heading"><h3><Icon name="layers" size={16} /> 信号激励</h3><span>{visibleSignals.length} 路</span></div>
          <div className="sim-table-toolbar">
            <div className="segmented"><button className={tableView === 'catalog' ? 'active' : ''} onClick={() => { setTableView('catalog'); setSignalPage(0); setFocusedKey('') }}>服务信号</button><button className={tableView === 'workset' ? 'active' : ''} onClick={() => { setTableView('workset'); setSignalPage(0); setFocusedKey('') }}>工作集 ({selectedSignals.length})</button></div>
            <button className="sim-text-button" disabled={locked || !selectedSignals.length} onClick={() => { setSelectedKeys([]); logInfo('清空仿真工作集选择') }}>清空选择</button>
          </div>
          <div className="sim-context-line" title={tableView === 'catalog' ? activeService?.name : '仅启动已加入工作集的信号'}>
            <span>{tableView === 'catalog' ? activeService?.name ?? '尚未加载服务' : '已选择的工作信号'}</span>
            <code>{tableView === 'catalog' ? activeService?.serviceId : selectedServices.size + ' 个服务'}</code>
          </div>
          <label className="sim-search"><Icon name="search" size={15} /><input aria-label="搜索仿真信号" placeholder="信号名称、报文 ID、类型" value={signalQuery} onChange={(event) => { setSignalQuery(event.target.value); setSignalPage(0) }} /></label>
          <div className="sim-table-scroll">
            <table className="sim-signal-table">
              <colgroup><col style={{ width: 42 }} /><col /><col style={{ width: 100 }} /><col style={{ width: 92 }} /><col style={{ width: 78 }} /><col style={{ width: 88 }} /></colgroup>
              <thead><tr><th title="加入工作集">选</th><th>信号 / 报文来源</th><th>数据类型</th><th>初始值</th><th>单位</th><th>激励</th></tr></thead>
              <tbody>{pageSignals.map((signal) => {
                const setting = settings[signal.key] ?? defaultSetting(signal)
                return <tr key={signal.key} className={focusedSignal?.key === signal.key ? 'is-focused' : ''} onClick={() => { setFocusedKey(signal.key); setPropertyTab('signal') }}>
                  <td><input type="checkbox" aria-label={'加入工作集 ' + signal.name} checked={selectedKeySet.has(signal.key)} disabled={locked} onClick={(event) => event.stopPropagation()} onChange={() => toggleSignal(signal)} /></td>
                  <td><button className="sim-row-link" title={signal.name + '\n' + signal.serviceName + ' · ' + signal.source} onClick={() => { setFocusedKey(signal.key); setPropertyTab('signal') }}><strong>{signal.name}</strong><small>{tableView === 'workset' ? signal.serviceName + ' · ' : ''}{signal.source}</small></button></td>
                  <td><code title={signal.dataType}>{signal.dataType}</code></td><td className="sim-numeric" title={String(setting.value)}>{String(setting.value)}</td><td title={signal.unit || 'ARXML 未定义单位'}>{signal.unit || '—'}</td><td><span className={'sim-generator-label ' + setting.kind}>{generatorLabels[setting.kind]}</span></td>
                </tr>
              })}</tbody>
            </table>
            {!pageSignals.length && <div className="sim-empty"><Icon name="activity" size={24} /><strong>{tableView === 'workset' ? '工作集为空' : '没有可用的数值信号'}</strong><span>{tableView === 'workset' ? '在服务信号表中勾选需要仿真的信号。' : '选择其他服务，或查看下方解析说明。'}</span></div>}
          </div>
          <div className="sim-pane-footer"><span>{visibleSignals.length ? currentSignalPage * SIGNAL_PAGE_SIZE + 1 : 0}–{Math.min((currentSignalPage + 1) * SIGNAL_PAGE_SIZE, visibleSignals.length)} / {visibleSignals.length}</span><Pager page={currentSignalPage} maxPage={maxSignalPage} onChange={setSignalPage} label="信号" /></div>
          {tableView === 'catalog' && (serviceWarnings.length > 0 || !!activeService?.methods.length) && <details className="sim-limitations"><summary>仿真支持说明{serviceWarnings.length ? ' · ' + serviceWarnings.length + ' 项暂不可用' : ''}</summary><div><p>本页工作集使用数值型单信号 Event / Field Notifier。完整多信号/复合事件请使用“服务模型”的完整事件周期与路径激励入口；Method 请求使用服务调用或自动场景。</p>{serviceWarnings.map((warning, index) => <p key={index}>{warning.message}</p>)}</div></details>}
        </section>

        <aside className="panel sim-properties-pane">
          <div className="sim-property-tabs"><button className={propertyTab === 'signal' ? 'active' : ''} onClick={() => setPropertyTab('signal')}><Icon name="activity" size={15} /> 信号属性</button><button className={propertyTab === 'runtime' ? 'active' : ''} onClick={() => setPropertyTab('runtime')}><Icon name="settings" size={15} /> 运行配置</button></div>
          <div className="sim-property-body">
            {propertyTab === 'signal' ? focusedSignal && focusedSetting ? <>
              <p className="sim-eyebrow">当前信号</p><h3 className="sim-property-name" title={focusedSignal.name}>{focusedSignal.name}</h3>
              <dl className="sim-metadata"><dt>服务</dt><dd title={focusedSignal.serviceName}>{focusedSignal.serviceName}</dd><dt>Service / Instance</dt><dd>{formatHex(focusedSignal.serviceId)} / {formatHex(focusedSignal.instanceId)}</dd><dt>Event / Notifier</dt><dd>{formatHex(focusedSignal.methodId)}</dd><dt>数据类型 / 单位</dt><dd>{focusedSignal.dataType} / {focusedSignal.unit || '未定义'}</dd><dt>数值范围</dt><dd>{String(focusedSignal.minimum)} ~ {String(focusedSignal.maximum)}</dd></dl>
              {focusedSignal.inferredBounds && <p className="sim-help">ARXML 未完整定义范围，当前使用默认激励范围。</p>}
              <div className="sim-property-divider" />
              <GeneratorNumberInput key={focusedSignal.key + ':minimum'} label="激励最小值" value={focusedSetting.minimum} dataType={focusedSignal.dataType} disabled={locked} onChange={minimum => updateSetting(focusedSignal, { minimum })} onValidityChange={valid => inputValidity(focusedSignal.key + ':minimum', valid)} />
              <GeneratorNumberInput key={focusedSignal.key + ':maximum'} label="激励最大值" value={focusedSetting.maximum} dataType={focusedSignal.dataType} disabled={locked} onChange={maximum => updateSetting(focusedSignal, { maximum })} onValidityChange={valid => inputValidity(focusedSignal.key + ':maximum', valid)} />
              <label className="sim-form-field"><span>激励方式</span><select aria-label="激励方式" value={focusedSetting.kind} disabled={locked} onChange={(event) => updateSetting(focusedSignal, { kind: event.target.value as GeneratorMode })}><option value="constant">常量 · 固定值</option><option value="sine">正弦 · 周期变化</option><option value="ramp">斜坡 · 线性扫描</option><option value="step">阶跃 · 到时切换并保持</option><option value="random">随机 · 独立种子</option><option value="sequence">序列 · 逐项循环</option><option value="state_machine">时间状态机 · 定时转换</option></select></label>
              <GeneratorNumberInput key={focusedSignal.key + ':value'} label={focusedSetting.kind === 'constant' ? '输出值' : '初始值'} value={focusedSetting.value} dataType={focusedSignal.dataType} disabled={locked} onChange={value => updateSetting(focusedSignal, { value })} onValidityChange={valid => inputValidity(focusedSignal.key + ':value', valid)} />
              {focusedSetting.kind === 'sequence' ? <GeneratorSequenceInput key={focusedSignal.key + ':sequence'} value={focusedSetting.sequence} dataType={focusedSignal.dataType} minimum={focusedSetting.minimum} maximum={focusedSetting.maximum} disabled={locked} onChange={sequence => updateSetting(focusedSignal, { sequence })} onValidityChange={valid => inputValidity(focusedSignal.key + ':sequence', valid)} />
                : focusedSetting.kind === 'state_machine' ? <TimedStateInput key={focusedSignal.key + ':states'} initialState={focusedSetting.initialState} states={focusedSetting.states} dataType={focusedSignal.dataType} minimum={focusedSetting.minimum} maximum={focusedSetting.maximum} disabled={locked} onChange={graph => updateSetting(focusedSignal, { initialState: graph.initial_state, states: graph.states })} onValidityChange={valid => inputValidity(focusedSignal.key + ':states', valid)} />
                : focusedSetting.kind === 'random' ? <>
                  <GeneratorNumberInput key={focusedSignal.key + ':seed'} label="随机种子（uint64）" value={focusedSetting.seed} dataType="uint64" disabled={locked} onChange={seed => updateSetting(focusedSignal, { seed })} onValidityChange={valid => inputValidity(focusedSignal.key + ':seed', valid)} />
                  <p className="sim-help">每个任务使用独立种子；相同类型、范围、种子与原生版本在重新启动后产生相同序列。每次发送取一个随机样本，曲线周期不影响随机源。</p>
                </> : focusedSetting.kind === 'step' ? <>
                <GeneratorNumberInput key={focusedSignal.key + ':stepAtMs'} label="阶跃时刻（ms）" value={focusedSetting.stepAtMs} dataType="uint64" disabled={locked} onChange={stepAtMs => updateSetting(focusedSignal, { stepAtMs })} onValidityChange={valid => inputValidity(focusedSignal.key + ':stepAtMs', valid)} />
                <GeneratorNumberInput key={focusedSignal.key + ':stepValue'} label="阶跃后值" value={focusedSetting.stepValue} dataType={focusedSignal.dataType} disabled={locked} onChange={stepValue => updateSetting(focusedSignal, { stepValue })} onValidityChange={valid => inputValidity(focusedSignal.key + ':stepValue', valid)} />
                <p className="sim-help">时刻为非负整型毫秒；到时后的首次发送使用阶跃后值并保持。本页按原生经过时间计算，不承诺硬实时；完整事件入口使用共享逻辑时间。</p>
              </> : focusedSetting.kind === 'constant' ? generatorSliderSafe(focusedSetting.minimum, focusedSetting.maximum, focusedSetting.value) ? <div className="sim-property-range"><input aria-label="调整信号输出值" type="range" min={Number(focusedSetting.minimum)} max={Number(focusedSetting.maximum)} step={focusedSignal.step} value={Number(focusedSetting.value)} disabled={locked || !inputsValid} onChange={(event) => updateSetting(focusedSignal, { value: Number(event.target.value) })} /><div><span>{String(focusedSetting.minimum)}</span><span>{String(focusedSetting.maximum)}</span></div></div> : <p className="sim-help">精确大整数使用十进制文本输入，不使用浮点滑块。</p>
                : <label className="sim-form-field"><span>曲线周期 (s)</span><input type="number" min={0.1} step={0.1} value={focusedSetting.periodSeconds} disabled={locked} onChange={(event) => updateSetting(focusedSignal, { periodSeconds: Number(event.target.value) })} /></label>}
              <button className={'button sim-add-signal ' + (selectedKeySet.has(focusedSignal.key) ? 'subtle' : 'primary')} disabled={locked} onClick={() => toggleSignal(focusedSignal)}><Icon name={selectedKeySet.has(focusedSignal.key) ? 'check' : 'layers'} size={15} />{selectedKeySet.has(focusedSignal.key) ? '已加入工作集 · 点击移除' : '加入工作集'}</button>
              <p className="sim-help">{locked ? '任务运行期间配置已锁定，停止测量后可修改。' : '此处的参数仅作用于当前信号。加入工作集后随测量一起启动。'}</p>
            </> : <div className="sim-empty"><Icon name="eye" /><strong>选择信号查看属性</strong><span>点击中间表格中的信号，查看来源并配置激励。</span></div> : <>
              <p className="sim-eyebrow">测量配置</p>
              <label className="sim-form-field"><span>运行模式</span><select value={mode} disabled={locked} onChange={(event) => setMode(event.target.value as 'virtual' | 'physical')}><option value="virtual">虚拟总线 · 内部监控</option><option value="physical">真实 UDP · 网络发送</option></select></label>
              {mode === 'physical' ? <><label className="sim-form-field"><span>授权订阅主机</span><input value={config.destinationHost} disabled={locked} onChange={(event) => updateConfig('destinationHost', event.target.value)} placeholder="192.168.10.20" /></label><label className="sim-form-field"><span>本机服务端口</span><input type="number" min={1} max={65535} value={config.destinationPort} disabled={locked} onChange={(event) => updateConfig('destinationPort', Number(event.target.value))} /></label><p className="sim-help">vsomeip 在本机服务端口提供事件，客户端完成 SD 订阅后接收；需配置本机网卡地址和主机级白名单。Trace 中的提交记录不代替线上抓包。</p></> : <p className="sim-help">原生发生器在本地协议栈运行，信号写入内部监控流，不发送网络报文或 SD 组播。</p>}
              <div className="sim-property-divider" />
              <label className="sim-form-field"><span>基础发送周期 (ms)</span><input type="number" min={10} max={60000} value={config.cycleMs} disabled={locked} onChange={(event) => updateConfig('cycleMs', Number(event.target.value))} /></label>
              <label className="sim-form-field"><span>速率倍率</span><select value={config.multiplier} disabled={locked} onChange={(event) => updateConfig('multiplier', Number(event.target.value))}><option value={0.5}>0.5×</option><option value={1}>1×</option><option value={2}>2×</option><option value={10}>10×</option></select></label>
              <div className="sim-effective-rate"><span>实际发送周期</span><strong>{effectiveIntervalMs} ms</strong><small>{(1000 / effectiveIntervalMs).toFixed(1)} Hz / 信号</small></div>
              <label className="sim-checkbox-field"><input type="checkbox" checked={mode === 'physical' && config.enableSd} disabled={locked || mode === 'virtual'} onChange={(event) => updateConfig('enableSd', event.target.checked)} /><span>启用 SOME/IP-SD OfferService</span></label>
              <p className="sim-help">真实网络模式由 vsomeip 维护服务发现与订阅；方法自动响应可通过 Python SOA API 配置，页面入口尚未接入。</p>
            </>}
          </div>
        </aside>
      </section>}

      {workspaceView === 'waveform' && <div className="sim-waveform-workspace"><SignalScope samples={samples} allowedSignalKeys={allowedWaveKeys} title="工作集输出波形" /><p className="sim-help">只展示工作集和正在运行的任务信号；在信号选择器中选择关注项，可切换分轨视图避免不同量纲叠加。</p></div>}

      {workspaceView === 'tasks' && <section className="panel sim-runtime-pane">
        <div className="sim-pane-heading"><h3><Icon name="terminal" size={16} /> 运行任务</h3><div className="segmented"><button className={taskView === 'running' ? 'active' : ''} onClick={() => { setTaskView('running'); setTaskPage(0) }}>运行中 {runningTasks.length}</button><button className={taskView === 'all' ? 'active' : ''} onClick={() => { setTaskView('all'); setTaskPage(0) }}>全部 {tasks.length}</button></div></div>
        <div className="sim-task-scroll"><table className="sim-task-table"><colgroup><col /><col style={{ width: 160 }} /><col style={{ width: 110 }} /><col style={{ width: 100 }} /><col style={{ width: 90 }} /><col style={{ width: 85 }} /></colgroup><thead><tr><th>任务 / 信号</th><th>Service / Event ID</th><th>输出通道</th><th>已发布</th><th>状态</th><th>操作</th></tr></thead><tbody>{pageTasks.map((task) => <tr key={task.id}><td><strong title={task.config.name}>{task.config.generator.signal_name}</strong><small title={task.last_error || task.config.name}>{task.last_error || task.config.name}</small></td><td><code>{formatHex(task.config.service_id)} / {formatHex(task.config.method_id)}</code></td><td title={task.config.transport === 'udp' ? task.config.destination_host + ':' + task.config.destination_port : '内部监控总线'}>{task.config.transport === 'udp' ? 'UDP' : '内部总线'}<small>{task.config.interval_ms} ms</small></td><td className="sim-numeric">{(task.emitted_count ?? 0).toLocaleString()}</td><td><span className={'sim-task-status ' + (task.last_error ? 'is-error' : task.running ? 'is-running' : '')}>{task.last_error ? '异常' : task.running ? '运行中' : '已停止'}</span></td><td><button className="sim-text-button" disabled={!task.running || stoppingTask !== null || simState === 'stopping'} onClick={() => void stop(task.id)}>{stoppingTask === task.id ? '停止中…' : '停止'}</button></td></tr>)}</tbody></table>{!pageTasks.length && <div className="sim-empty">暂无{taskView === 'running' ? '运行中的' : ''}仿真任务</div>}</div>
        <div className="sim-pane-footer"><span>每 2.5 秒同步任务状态 · 共 {visibleTasks.length} 项</span><Pager page={currentTaskPage} maxPage={maxTaskPage} onChange={setTaskPage} label="任务" /></div>
      </section>}
    </div>
  )
}

function Pager({ page, maxPage, onChange, label }: { page: number; maxPage: number; onChange: (page: number) => void; label: string }) {
  return <div className="sim-pagination"><button aria-label={label + '上一页'} disabled={page === 0} onClick={() => onChange(Math.max(0, page - 1))}>上一页</button><span>{page + 1} / {maxPage + 1}</span><button aria-label={label + '下一页'} disabled={page >= maxPage} onClick={() => onChange(page + 1)}>下一页</button></div>
}
