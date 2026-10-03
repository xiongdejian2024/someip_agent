import type { MonitorMessage, WaveSample } from '../types'

export const TRACE_CAPACITY = 2_000
export const SAMPLE_CAPACITY = 3_000
export const DISPLAY_INTERVAL_MS = 100

/** 采集缓存与 React 渲染分离；满载时仅淘汰最旧记录。 */
export class BoundedBuffer<T> {
  private readonly items: Array<T | undefined>
  private cursor = 0
  private count = 0
  evictedTotal = 0

  constructor(readonly capacity: number) {
    if (!Number.isInteger(capacity) || capacity < 1) throw new Error('缓存容量必须是正整数')
    this.items = new Array<T | undefined>(capacity)
  }

  push(value: T) {
    if (this.count === this.capacity) this.evictedTotal += 1
    this.items[this.cursor] = value
    this.cursor = (this.cursor + 1) % this.capacity
    this.count = Math.min(this.count + 1, this.capacity)
  }

  clear() {
    this.items.fill(undefined)
    this.cursor = 0
    this.count = 0
  }

  values(): T[] {
    const start = (this.cursor - this.count + this.capacity) % this.capacity
    return Array.from({ length: this.count }, (_, index) => this.items[(start + index) % this.capacity] as T)
  }
}

export function messageSample(message: MonitorMessage): WaveSample | null {
  const time = new Date(message.timestamp).getTime()
  if (!Number.isFinite(time)) return null
  const values = Object.fromEntries(Object.entries(message.signalValues ?? {}).flatMap(([name, value]) => {
    const numeric = typeof value === 'boolean' ? Number(value) : value
    return typeof numeric === 'number' && Number.isFinite(numeric)
      ? [[`${message.serviceId}/${message.methodId}/${name}`, numeric]]
      : []
  }))
  return Object.keys(values).length ? { time, values } : null
}

export class MonitorBuffer {
  private readonly trace = new BoundedBuffer<MonitorMessage>(TRACE_CAPACITY)
  private readonly signals = new BoundedBuffer<WaveSample>(SAMPLE_CAPACITY)
  dirty = false

  append(message: MonitorMessage) {
    this.trace.push(message)
    const sample = messageSample(message)
    if (sample) this.signals.push(sample)
    this.dirty = true
  }

  replace(messages: MonitorMessage[]) {
    this.clear()
    for (const message of messages) this.append(message)
  }

  clear() {
    this.trace.clear()
    this.signals.clear()
    this.dirty = true
  }

  snapshot() {
    this.dirty = false
    return { messages: this.trace.values().reverse(), samples: this.signals.values().sort((a, b) => a.time - b.time) }
  }

  statistics() {
    return { trace_evicted_total: this.trace.evictedTotal, sample_evicted_total: this.signals.evictedTotal }
  }
}
