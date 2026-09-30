import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import { logError, logInfo } from '../api/logger'
import type { ConnectionState, HealthResponse } from '../types'

export function useBackendStatus() {
  const [state, setState] = useState<ConnectionState>('connecting')
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [lastChecked, setLastChecked] = useState<Date | null>(null)

  const check = useCallback(async () => {
    try {
      const value = await api.health()
      setHealth(value)
      setState('online')
      setLastChecked(new Date())
      logInfo('后端健康检查通过', { version: value.version ?? 'unknown' })
    } catch (error) {
      setState('offline')
      setLastChecked(new Date())
      logError('后端健康检查失败，启用演示数据', error)
    }
  }, [])

  useEffect(() => {
    void check()
    const timer = window.setInterval(() => void check(), 15_000)
    return () => window.clearInterval(timer)
  }, [check])

  return { state, health, lastChecked, check }
}
