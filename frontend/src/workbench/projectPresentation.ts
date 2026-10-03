import { stringifyJson } from '../api/json'
import type { ProjectDocument } from './projects'

export function projectDraftChanged(document: ProjectDocument, baseline: ProjectDocument) {
  // 工程采用不可变更新；大模型引用未改变时不逐键输入重序列化数 MiB ARXML。
  return ([...new Set([...Object.keys(document), ...Object.keys(baseline)])] as Array<keyof ProjectDocument>).some(key =>
    document[key] !== baseline[key] && stringifyJson(document[key]) !== stringifyJson(baseline[key]))
}

/** 保存返回时，保留请求期间产生的新编辑。 */
export function mergeSavedDraft(current: ProjectDocument, requested: ProjectDocument, saved: ProjectDocument) {
  return projectDraftChanged(current, requested) ? current : saved
}

export function projectContents(document: ProjectDocument) {
  return [
    ['服务配置', Object.keys(document.services).length], ['周期激励', document.cycles.length],
    ['监听草案', document.listeners.length], ['仿真工作集', document.simulations.length],
    ['波形通道', document.workspace.waves.length],
    ['公共时钟组', (document.sync_groups ?? []).length],
  ] as const
}

export function projectDate(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

/** 避免将大型模型的多 MiB 文本塞入原生 textarea 阻塞整个工作台。 */
export function projectEditorText(document: ProjectDocument) {
  const limit = 1024 * 1024
  const compact = stringifyJson(document)
  if (new TextEncoder().encode(compact).byteLength > limit) throw new Error('内联 JSON 编辑上限为 1 MiB；请导出当前草案，外部编辑后导入为新工程。工程文件上限仍为 8 MiB')
  const formatted = stringifyJson(document, 2)
  if (new TextEncoder().encode(formatted).byteLength > limit) return compact
  return formatted
}
