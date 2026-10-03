import { parseJson } from '../api/json'
import type { NativeCycleCommand, NativeServiceRequest, NativeServiceSession, NativeSyncStatus } from '../types'

export function syncEvents(text: string): NativeCycleCommand[] {
  if (new TextEncoder().encode(text).length > 2 * 1024 * 1024) throw new Error('同步事件草案超过 2 MiB 编辑预算')
  const value = parseJson(text)
  if (!Array.isArray(value) || value.length < 1 || value.length > 16) throw new Error('公共时钟组需要 1–16 个完整事件')
  if (value.some(event => !event || typeof event !== 'object' || Array.isArray(event))) throw new Error('每个同步事件必须是完整 JSON 对象')
  return value as NativeCycleCommand[]
}

export function syncSessionMatches(request: NativeServiceRequest | undefined, session: NativeServiceSession | undefined): boolean {
  return !!request && !!session && request.application_name === session.application_name && request.application_id === session.application_id
}

export function syncActions(status: NativeSyncStatus | null, ready: boolean) {
  return { prepare: ready && !!status && !status.active, pause: ready && !!status?.active && !status.paused,
    resume: ready && !!status?.active && status.paused, step: ready && !!status?.active && status.paused,
    speed: ready && !!status?.active, stop: ready && !!status?.active }
}
