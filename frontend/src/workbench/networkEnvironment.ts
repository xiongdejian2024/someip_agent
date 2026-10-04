import { ApiError } from '../api/client'
import type { NetworkProfile } from '../types'
import type { ProjectDocument } from './projects'

export interface NetworkForm {
  name: string; parent: string; interface: string; mode: 'vlan' | 'untagged'
  vlan: string; ipv4: string; mtu: string; multicast: string
}
export const emptyNetworkForm = (): NetworkForm => ({ name: '', parent: '', interface: '', mode: 'vlan', vlan: '', ipv4: '', mtu: '', multicast: '' })
export function formFromProfile(profile: NetworkProfile): NetworkForm {
  return { name: profile.name, parent: profile.parent, interface: profile.interface, mode: profile.vlan_id === null ? 'untagged' : 'vlan',
    vlan: profile.vlan_id === null ? '' : String(profile.vlan_id), ipv4: profile.ipv4, mtu: profile.mtu === null ? '' : String(profile.mtu), multicast: profile.sd_multicast ?? '' }
}
function integer(value: string, name: string, min: number, max: number): number {
  if (!/^\d+$/.test(value) || Number(value) < min || Number(value) > max) throw new Error(`${name}须为 ${min}–${max} 的整数`)
  return Number(value)
}
export function profileFromForm(form: NetworkForm): NetworkProfile {
  const name = form.name.trim(), parent = form.parent.trim(), target = form.interface.trim()
  if (!name) throw new Error('请填写环境名称')
  if (!/^[A-Za-z][A-Za-z0-9_.-]{0,14}$/.test(parent)) throw new Error('请填写有效父网卡名称（最多 15 个字符）')
  if (form.mode === 'vlan' && (!/^[A-Za-z][A-Za-z0-9_.-]{0,14}$/.test(target) || target === parent)) throw new Error('请填写独立的 VLAN 子网卡名称（最多 15 个字符）')
  if (!form.ipv4.trim().includes('/')) throw new Error('IPv4 地址须含前缀，如 192.168.10.2/24；完整合法性由后端校验')
  return { name, parent, interface: form.mode === 'untagged' ? parent : target,
    vlan_id: form.mode === 'untagged' ? null : integer(form.vlan.trim(), 'VLAN ID', 1, 4094), ipv4: form.ipv4.trim(),
    mtu: form.mode === 'untagged' || !form.mtu.trim() ? null : integer(form.mtu.trim(), 'MTU', 576, 9000), sd_multicast: form.multicast.trim() || null }
}
export function networkDraftDocument(document: ProjectDocument, profile: NetworkProfile, previous: string | null): ProjectDocument {
  const profiles = document.network_profiles ?? []
  if (profiles.some(item => item.interface === profile.interface && item.interface !== previous)) throw new Error('该目标网卡已有工程草案，请选择已有草案编辑')
  return { ...document, network_profiles: previous && profiles.some(item => item.interface === previous)
    ? profiles.map(item => item.interface === previous ? profile : item) : [...profiles, profile] }
}
export function networkWriteUnknown(error: unknown): boolean {
  // 系统命令可能已经产生部分效果；5xx 不能等同于“完全没有写入”。
  return error instanceof ApiError && (error.status === 0 || error.status >= 500 || error.status === 409 || error.status === 422)
}

export interface NetworkLink {
  ifname: string; ifindex: number; mtu: number; flags: string[]; link_type: string
  addr_info?: Array<{ family: string; local: string; prefixlen: number; scope?: string }>
}
export interface ManagedEnvironment {
  id: string; profile: NetworkProfile; status: 'pending' | 'failed' | 'applied' | 'removed'
  routes: string[]
}
export interface NetworkEnvironmentStatus {
  available: boolean; reason: string; write_enabled: boolean; allowed_parents: string[]
  native_unicast: string; interfaces: NetworkLink[]; routes: Array<{ dst?: string; dev?: string; gateway?: string }>
  managed: ManagedEnvironment[]; active_tasks: boolean
  activity: { lifecycle_operations: number; configuring: boolean }
}
export interface NetworkPlan { token: string; profile: NetworkProfile; commands: string[][]; destinations: string[]; parent_index: number }

export function networkWriteAllowed(status: NetworkEnvironmentStatus | null, parent?: string): boolean {
  return !!status?.available && status.write_enabled && !status.active_tasks && !status.activity.configuring && status.activity.lifecycle_operations === 0
    && (parent === undefined || status.allowed_parents.includes(parent))
}
