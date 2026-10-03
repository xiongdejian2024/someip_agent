import { parseJson } from '../api/json'
import { validateGeneratorValue, type GeneratorDataType, type GeneratorNumber } from './generatorValues'
import type { TimedSignalState } from '../types'

export interface TimedStateGraph { initial_state: string; states: TimedSignalState[] }

export function validateTimedGraph(value: unknown, dataType: GeneratorDataType, minimum: GeneratorNumber, maximum: GeneratorNumber): TimedStateGraph {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('状态图必须为 JSON 对象')
  const graph = value as Record<string, unknown>
  if (Object.keys(graph).some(key => !['initial_state', 'states'].includes(key)) || typeof graph.initial_state !== 'string'
    || !Array.isArray(graph.states) || graph.states.length < 1 || graph.states.length > 128) throw new Error('状态图需要 initial_state 与 1–128 个状态')
  const states = graph.states.map((entry): TimedSignalState => {
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) throw new Error('每个状态必须为对象')
    const state = entry as Record<string, unknown>
    if (Object.keys(state).some(key => !['name', 'value', 'duration_ms', 'next'].includes(key))
      || typeof state.name !== 'string' || !/^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(state.name)) throw new Error('状态名无效或有未知字段')
    const scalar = dataType === 'boolean' && typeof state.value === 'boolean' ? state.value : validateGeneratorValue(state.value, dataType)
    if (dataType === 'boolean' && typeof scalar !== 'boolean') throw new Error('布尔状态值须使用 true／false')
    const numeric = typeof scalar === 'boolean' ? Number(scalar) : scalar
    if (numeric < minimum || numeric > maximum) throw new Error('状态值超出激励范围')
    const next = state.next ?? null
    const duration = state.duration_ms ?? null
    if (next === null) {
      if (duration !== null) throw new Error('终态不能设置 duration_ms')
    } else {
      if (typeof next !== 'string' || duration === null) throw new Error('转换必须指定下一状态和持续时间')
      validateGeneratorValue(duration, 'uint64')
      if (BigInt(duration as GeneratorNumber) === 0n) throw new Error('持续时间必须大于 0 ms')
    }
    return { name: state.name, value: scalar, next: next as string | null, duration_ms: duration as GeneratorNumber | null }
  })
  const mapping = new Map(states.map(state => [state.name, state]))
  if (mapping.size !== states.length || !mapping.has(graph.initial_state)) throw new Error('状态名重复或初态不存在')
  const seen = new Set<string>()
  let name: string | null = graph.initial_state
  let elapsed = 0n
  while (name !== null && !seen.has(name)) {
    const state = mapping.get(name)
    if (!state) throw new Error('转换目标不存在')
    seen.add(name)
    elapsed += state.duration_ms == null ? 0n : BigInt(state.duration_ms)
    if (elapsed > 18446744073709551615n) throw new Error('状态图累计时间超出 uint64 毫秒范围')
    name = state.next ?? null
  }
  if (seen.size !== states.length) throw new Error('状态图包含不可达状态')
  return { initial_state: graph.initial_state, states }
}

export function parseTimedGraph(text: string, dataType: GeneratorDataType, minimum: GeneratorNumber, maximum: GeneratorNumber): TimedStateGraph {
  if (text.length > 262144) throw new Error('状态图文本不能超过 256 KiB 字符')
  return validateTimedGraph(parseJson(text), dataType, minimum, maximum)
}
