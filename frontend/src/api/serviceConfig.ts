import type { NativeServiceRequest } from '../types'

export type ByteOrderSelection = 'arxml' | 'big' | 'little'

// 缺省不发覆盖字段，让后端保留 ARXML 的明确部署；旧标量兜底仍由后端校验。
export function byteOrderOverride(selection: ByteOrderSelection): Pick<NativeServiceRequest['members'][string], 'byte_order'> {
  return selection === 'arxml' ? {} : { byte_order: selection }
}

// 仅页面的一至两个成员采用连续显式 ID；后端仍校验全会话冲突，不依赖协议栈自动分配。
export function memberApplication(routingName: string, routingId: number, independent: boolean, offset: number): Pick<NativeServiceRequest['members'][string], 'application_name' | 'application_id'> {
  if (!independent) return {}
  const id = routingId + offset
  if (!Number.isInteger(routingId) || routingId <= 0 || !Number.isInteger(offset) || offset < 1 || offset > 2 || id >= 0xffff || id === 0x1101) {
    throw new Error('独立成员应用 ID 必须为 1 至 65534，不能使用保留 ID 0x1101；请调整路由宿主 ID')
  }
  return { application_name: `${routingName}_member${offset}`, application_id: id }
}
