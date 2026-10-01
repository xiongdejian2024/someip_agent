import type { NativeServiceRequest } from '../types'

export type ByteOrderSelection = 'arxml' | 'big' | 'little'

// 缺省不发覆盖字段，让后端保留 ARXML 的明确部署；旧标量兜底仍由后端校验。
export function byteOrderOverride(selection: ByteOrderSelection): Pick<NativeServiceRequest['members'][string], 'byte_order'> {
  return selection === 'arxml' ? {} : { byte_order: selection }
}
