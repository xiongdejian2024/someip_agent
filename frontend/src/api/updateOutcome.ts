import type { HealthResponse, UpdateInstallationStatus } from '../types'

export type UpdateOutcome =
  | { status: 'pending' | 'complete' }
  | { status: 'failed' | 'rolled_back'; text: string }

// 必须是本次安装记录及其实际健康版本，不能凭 PID、旧版可达或残留状态宣称恢复。
export function updateOutcome(
  record: UpdateInstallationStatus,
  installationId: string,
  expectedVersion: string,
  health: Pick<HealthResponse, 'status' | 'version'>,
): UpdateOutcome {
  if (record.installation_id !== installationId || record.version !== expectedVersion) {
    return { status: 'pending' }
  }
  if (record.status === 'complete' && health.status === 'ok' && health.version === expectedVersion) {
    return { status: 'complete' }
  }
  if (record.status !== 'failed') return { status: 'pending' }
  const reason = record.error ? `失败原因：${record.error}` : '请查看升级日志。'
  if (record.rollback_failed) {
    return { status: 'failed', text: `升级失败，旧版本回滚未恢复健康，需要人工处理。${reason}` }
  }
  if (record.rollback_completed && record.restored_version) {
    if (health.status !== 'ok' || health.version !== record.restored_version) return { status: 'pending' }
    return { status: 'rolled_back', text: `升级失败，已回滚至 v${record.restored_version}。${reason}` }
  }
  return { status: 'failed', text: `升级失败，尚未确认旧版本恢复。${reason}` }
}
