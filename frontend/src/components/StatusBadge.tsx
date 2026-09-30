import type { ConnectionState } from '../types'

interface StatusBadgeProps {
  state: ConnectionState
  onlineText?: string
  offlineText?: string
  compact?: boolean
}
export function StatusBadge({ state, onlineText = '后端在线', offlineText = '演示模式', compact }: StatusBadgeProps) {
  const label = state === 'online' ? onlineText : state === 'connecting' ? '连接中' : offlineText
  return (
    <span className={`status-badge status-${state}${compact ? ' compact' : ''}`}>
      <span className="status-dot" />
      {label}
    </span>
  )
}
