import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { api } from '../api/client'
import { stringifyJson } from '../api/json'
import { logError, logInfo } from '../api/logger'
import { projectDraftChanged } from './projectPresentation'
import type { NativeCycleCommand, NativeServiceRequest, NativeSyncCommand, NetworkListenerConfig, PageId, SimulationStartRequest } from '../types'

export interface ProjectDocument {
  format: 'someip-agent-project'
  format_version: 2
  name: string
  description: string
  model: unknown | null
  services: Record<string, NativeServiceRequest>
  listeners: NetworkListenerConfig[]
  simulations: SimulationStartRequest[]
  cycles: Array<{ service_profile: string; command: NativeCycleCommand }>
  sync_groups: Array<{ service_profile: string; command: NativeSyncCommand }>
  workspace: { page: PageId; service_paths: string[]; waves: Array<{ service_id: number; method_id: number; signal_name: string }> }
}
export interface ProjectView { id: string; revision: number; updated_at: string; document: ProjectDocument }
export interface ProjectSummary { id: string; revision: number; updated_at: string; name: string }
export interface ProjectControls {
  document: ProjectDocument
  update: (change: (current: ProjectDocument) => ProjectDocument) => void
}

export function emptyProject(): ProjectDocument {
  return { format: 'someip-agent-project', format_version: 2, name: '未命名工程', description: '', model: null,
    services: {}, listeners: [], simulations: [], cycles: [], sync_groups: [], workspace: { page: 'dashboard', service_paths: [], waves: [] } }
}

export const ProjectContext = createContext<ProjectControls | null>(null)
export function useProject() { return useContext(ProjectContext) }

export { ProjectBar } from './ProjectManager'

export function useProjectState(onRestore: (view: ProjectView) => Promise<void>) {
  const [view, setView] = useState<ProjectView | null>(null)
  const [document, setDocument] = useState<ProjectDocument>(emptyProject)
  const [epoch, setEpoch] = useState(0)
  const [ready, setReady] = useState(false)
  const update = useCallback((change: (current: ProjectDocument) => ProjectDocument) => {
    setDocument(current => {
      const next = change(current)
      return projectDraftChanged(next, current) ? next : current
    })
  }, [])
  const apply = useCallback(async (next: ProjectView) => {
    await onRestore(next)
    setView(next); setDocument(next.document); setEpoch(current => current + 1)
    logInfo('工程配置已载入，未启动运行任务', { projectId: next.id, revision: next.revision })
  }, [onRestore])
  useEffect(() => {
    let active = true
    void api.currentProject().then(async next => {
      if (active && next) await apply(next)
      // 本次打开页面前可能导入过新 ARXML；不能把旧工程修订显示成无未保存更改。
      const model = await api.projectModel()
      if (active) setDocument(current => stringifyJson(current.model) === stringifyJson(model) ? current : { ...current, model })
    }).catch(error => logError('恢复当前工程配置失败', error)).finally(() => { if (active) setReady(true) })
    return () => { active = false }
  }, [apply])
  return { view, document, update, apply, epoch, ready, setView }
}
