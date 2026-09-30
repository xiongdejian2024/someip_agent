import { useCallback, useEffect, useRef, useState } from 'react'
import { api, monitorWebSocketUrl } from '../api/client'
import { normalizeMonitorMessage, type RawMonitorMessage } from '../api/adapters'
import { logError, logInfo } from '../api/logger'
import { DISPLAY_INTERVAL_MS, MonitorBuffer, SAMPLE_CAPACITY, TRACE_CAPACITY } from '../data/monitorBuffer'
import type { ConnectionState, MonitorMessage, WaveSample } from '../types'

type MonitorSnapshot = { messages: MonitorMessage[]; samples: WaveSample[] }

export function useMonitorStream(enabled = true) {
  const [view, setView] = useState<MonitorSnapshot>({ messages: [], samples: [] })
  const [streamState, setStreamState] = useState<ConnectionState>('connecting')
  const bufferRef = useRef(new MonitorBuffer())

  const clear = useCallback(async () => {
    try {
      await api.clearMessages()
      bufferRef.current.clear()
      setView(bufferRef.current.snapshot())
      logInfo('监控缓冲区已清空')
    } catch (error) {
      logError('清空后端监控缓冲区失败，保留本地数据', error)
      throw error
    }
  }, [])

  useEffect(() => {
    if (!enabled) return
    let active = true
    let socket: WebSocket | null = null
    let reconnectTimer: number | undefined
    let retry = 0

    const publish = () => {
      if (active && bufferRef.current.dirty) setView(bufferRef.current.snapshot())
    }
    const displayTimer = window.setInterval(publish, DISPLAY_INTERVAL_MS)
    const connect = () => {
      if (!active) return
      setStreamState('connecting')
      try {
        const current = new WebSocket(monitorWebSocketUrl())
        socket = current
        current.onopen = () => {
          if (!active || socket !== current) return
          retry = 0
          setStreamState('online')
          logInfo('监控流已连接，启用有界缓存和批量显示', {
            traceCapacity: TRACE_CAPACITY, sampleCapacity: SAMPLE_CAPACITY, displayIntervalMs: DISPLAY_INTERVAL_MS,
          })
        }
        current.onmessage = (event) => {
          if (!active || socket !== current) return
          try {
            const payload = JSON.parse(String(event.data)) as {
              type?: 'snapshot' | 'message' | 'heartbeat'
              messages?: RawMonitorMessage[]
              message?: RawMonitorMessage
            } | RawMonitorMessage
            if ('type' in payload && payload.type === 'snapshot' && Array.isArray(payload.messages)) {
              bufferRef.current.replace(payload.messages.map(normalizeMonitorMessage))
              publish()
            } else if ('type' in payload && payload.type === 'message' && payload.message) {
              bufferRef.current.append(normalizeMonitorMessage(payload.message))
            } else if (!('type' in payload) && 'service_id' in payload) {
              bufferRef.current.append(normalizeMonitorMessage(payload))
            }
          } catch (error) {
            logError('解析监控流报文失败', error)
          }
        }
        current.onerror = () => {
          if (active && socket === current) logError('监控流连接异常', new Error('WebSocket 连接发生错误'))
        }
        current.onclose = () => {
          if (!active || socket !== current) return
          publish()
          setStreamState('offline')
          retry += 1
          const delayMs = Math.min(15_000, 1_000 * 2 ** Math.min(retry, 4))
          logInfo('监控流已断开，保留已采集数据并准备重连', { delayMs })
          reconnectTimer = window.setTimeout(connect, delayMs)
        }
      } catch (error) {
        setStreamState('offline')
        logError('创建监控流失败', error)
        reconnectTimer = window.setTimeout(connect, 5_000)
      }
    }

    connect()
    return () => {
      active = false
      window.clearInterval(displayTimer)
      if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer)
      if (socket) {
        socket.onopen = null
        socket.onmessage = null
        socket.onerror = null
        socket.onclose = null
        socket.close()
      }
    }
  }, [enabled])

  return { ...view, streamState, source: 'live' as const, clear }
}
